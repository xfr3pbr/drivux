"""The folder the user sees: names come from the on-disk index, everything else
goes to the rclone mount hidden behind it.

    ~/GoogleDrive  (this module, FUSE)  ->  rclone mount (hidden)  ->  Google Drive

Listing and file info are answered from SQLite, so they work the moment the
computer starts, even offline. Opening, reading and changing files is passed
straight to the rclone mount, which keeps doing the cache and the uploads.
"""

import errno
import logging
import os
import signal
import stat
import time
from pathlib import Path

import pyfuse3
import trio

from . import rclone
from .index import ROOT, Index, Node

log = logging.getLogger("drivux.front")

BACK_WAIT_SECONDS = 60  # an open() while rclone is still starting waits this long before failing
CACHE_SECONDS = 5  # how long the kernel may reuse an answer (also how late a scan shows up)
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR
FUSE_SUBTYPE = "sshfs"
TRACE = bool(os.environ.get("DRIVUX_TRACE"))  # log every open and read: who asked, for what, how long


def _who(ctx) -> str:
    try:
        return Path(f"/proc/{ctx.pid}/comm").read_text().strip()
    except (OSError, AttributeError):
        return "?"


def _errno(exc: OSError) -> pyfuse3.FUSEError:
    return pyfuse3.FUSEError(exc.errno or errno.EIO)


class Front(pyfuse3.Operations):
    def __init__(self, index: Index, back: Path, require_mount: bool = True):
        super().__init__()
        self.index = index
        self.back = back
        self.require_mount = require_mount
        self.writers: dict[int, list[int]] = {}  # inode -> open file descriptors that may write
        self.handles: dict[int, int] = {}  # fd -> inode

    # Helpers

    def _node(self, inode: int) -> Node:
        node = self.index.get(inode)
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return node

    def _back_path(self, inode: int, name: str | None = None) -> Path:
        try:
            path = self.back / self.index.path_of(inode)
        except KeyError:
            raise pyfuse3.FUSEError(errno.ENOENT) from None
        return path / name if name else path

    async def _wait_for_back(self) -> None:
        """The folder is up before rclone is (listing does not need it); opening a file does."""
        if not self.require_mount:
            return
        for _ in range(BACK_WAIT_SECONDS * 4):
            if await trio.to_thread.run_sync(os.path.ismount, self.back):
                return
            await trio.sleep(0.25)
        raise pyfuse3.FUSEError(errno.EIO)

    async def _io(self, func, *args):
        """Run blocking work on the rclone mount off the event loop (a cold folder can take seconds)."""
        await self._wait_for_back()
        try:
            return await trio.to_thread.run_sync(func, *args)
        except OSError as exc:
            raise _errno(exc) from exc

    def _attr(self, node: Node, size: int | None = None, mtime_ns: int | None = None) -> pyfuse3.EntryAttributes:
        attr = pyfuse3.EntryAttributes()
        attr.st_ino = node.inode
        attr.st_mode = (stat.S_IFDIR | 0o755) if node.is_dir else (stat.S_IFREG | 0o644)
        attr.st_nlink = 2 if node.is_dir else 1
        attr.st_uid = os.getuid()
        attr.st_gid = os.getgid()
        attr.st_size = 0 if node.is_dir else (node.size if size is None else size)
        mtime = node.mtime_ns if mtime_ns is None else mtime_ns
        attr.st_atime_ns = attr.st_mtime_ns = attr.st_ctime_ns = mtime
        attr.st_blksize = 4096
        attr.st_blocks = (attr.st_size + 511) // 512
        attr.entry_timeout = attr.attr_timeout = CACHE_SECONDS
        return attr

    async def _live_attr(self, node: Node) -> pyfuse3.EntryAttributes:
        """Attributes; a file being written reports the size it has right now."""
        for fd in self.writers.get(node.inode, []):
            try:
                st = await trio.to_thread.run_sync(os.fstat, fd)
                return self._attr(node, st.st_size, st.st_mtime_ns)
            except OSError:
                break
        return self._attr(node)

    # Reading the tree (answered from the index)

    async def getattr(self, inode, ctx=None):
        return await self._live_attr(self._node(inode))

    async def lookup(self, parent_inode, name, ctx=None):
        node = self.index.lookup(parent_inode, os.fsdecode(name))
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return await self._live_attr(node)

    async def opendir(self, inode, ctx):
        if not self._node(inode).is_dir:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        return inode

    async def readdir(self, fh, start_id, token):
        after = start_id
        while True:
            page = self.index.children(fh, after)
            if not page:
                return
            for node in page:
                if not pyfuse3.readdir_reply(token, os.fsencode(node.name), self._attr(node), node.inode):
                    return
                after = node.inode

    async def releasedir(self, fh):
        pass

    async def statfs(self, ctx):
        info = pyfuse3.StatvfsData()
        try:
            st = await trio.to_thread.run_sync(os.statvfs, self.back)
            info.f_bsize, info.f_frsize = st.f_bsize, st.f_frsize
            info.f_blocks, info.f_bfree, info.f_bavail = st.f_blocks, st.f_bfree, st.f_bavail
        except OSError:
            info.f_bsize = info.f_frsize = 4096
            info.f_blocks = info.f_bfree = info.f_bavail = 0
        info.f_files = max(self.index.count(), 1)
        info.f_ffree = info.f_favail = 1 << 20
        return info

    async def forget(self, inode_list):
        pass  # inodes live in the index, there is nothing to free

    # Files (passed to the rclone mount)

    async def open(self, inode, flags, ctx):
        node = self._node(inode)
        if node.is_dir:
            raise pyfuse3.FUSEError(errno.EISDIR)
        started = time.monotonic()
        fd = await self._io(os.open, self._back_path(inode), flags & ~os.O_NOFOLLOW)
        self._register(fd, inode, flags)
        if TRACE:
            log.info("open  %-28s %6.0f ms  %s", _who(ctx), (time.monotonic() - started) * 1000, self.index.path_of(inode))
        return pyfuse3.FileInfo(fh=fd)

    def _register(self, fd: int, inode: int, flags: int) -> None:
        self.handles[fd] = inode
        if flags & WRITE_FLAGS:
            self.writers.setdefault(inode, []).append(fd)

    async def create(self, parent_inode, name, mode, flags, ctx):
        text = os.fsdecode(name)
        path = self._back_path(parent_inode, text)
        fd = await self._io(os.open, path, flags | os.O_CREAT, mode & 0o7777)
        node = self.index.add_local(parent_inode, text, False)
        self._register(fd, node.inode, flags | os.O_WRONLY)
        return pyfuse3.FileInfo(fh=fd), await self._live_attr(node)

    async def read(self, fh, off, size):
        started = time.monotonic()
        data = await self._io(os.pread, fh, size, off)
        if TRACE:
            log.info("read  off=%-10d size=%-7d %6.0f ms  %s", off, size, (time.monotonic() - started) * 1000,
                     self.index.path_of(self.handles.get(fh, ROOT)))
        return data

    async def write(self, fh, off, buf):
        return await self._io(os.pwrite, fh, buf, off)

    async def fsync(self, fh, datasync):
        await self._io(os.fsync, fh)

    async def release(self, fh):
        inode = self.handles.pop(fh, None)
        try:
            if inode is not None and fh in self.writers.get(inode, []):
                self.writers[inode].remove(fh)
                if not self.writers[inode]:
                    del self.writers[inode]
                try:
                    st = await trio.to_thread.run_sync(os.fstat, fh)
                    self.index.set_size(inode, st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
        finally:
            await trio.to_thread.run_sync(os.close, fh)

    # Changing the tree

    async def mkdir(self, parent_inode, name, mode, ctx):
        text = os.fsdecode(name)
        await self._io(os.mkdir, self._back_path(parent_inode, text), mode & 0o7777)
        return await self._live_attr(self.index.add_local(parent_inode, text, True))

    async def unlink(self, parent_inode, name, ctx):
        node = self.index.lookup(parent_inode, os.fsdecode(name))
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        await self._io(os.unlink, self._back_path(node.inode))
        self.index.remove(node.inode)

    async def rmdir(self, parent_inode, name, ctx):
        node = self.index.lookup(parent_inode, os.fsdecode(name))
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        await self._io(os.rmdir, self._back_path(node.inode))
        self.index.remove(node.inode)

    async def rename(self, parent_inode_old, name_old, parent_inode_new, name_new, flags, ctx):
        if flags:
            raise pyfuse3.FUSEError(errno.EINVAL)  # RENAME_NOREPLACE / RENAME_EXCHANGE are not supported
        node = self.index.lookup(parent_inode_old, os.fsdecode(name_old))
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        new_name = os.fsdecode(name_new)
        await self._io(os.rename, self._back_path(node.inode), self._back_path(parent_inode_new, new_name))
        self.index.rename(node.inode, parent_inode_new, new_name)

    async def setattr(self, inode, attr, fields, fh, ctx):
        self._node(inode)
        if fields.update_size:
            if fh is not None:
                await self._io(os.ftruncate, fh, attr.st_size)
            else:
                await self._io(os.truncate, self._back_path(inode), attr.st_size)
            self.index.set_size(inode, attr.st_size, time.time_ns())
        if fields.update_mtime:
            mtime = attr.st_mtime_ns
            atime = attr.st_atime_ns if fields.update_atime else mtime
            await self._io(lambda: os.utime(self._back_path(inode), ns=(atime, mtime)))
            self.index.set_mtime(inode, mtime)
        # mode, owner and times of access are not stored in Google Drive: accepted and ignored
        return await self._live_attr(self._node(inode))


async def refresh_loop(index: Index, remote: str, interval: int) -> None:
    """Keep the index equal to the Drive: one full listing every `interval` seconds."""
    last = index.last_scan()
    if last:  # the index is fresh (first start just read it, or a quick restart): do not read it again at once
        await trio.sleep(max(0.0, last + interval - time.time()))
    while True:
        started = time.monotonic()
        try:
            entries = await trio.to_thread.run_sync(rclone.list_tree, remote)
            result = index.apply_scan(entries, started)
            log.info(
                "scan: %d added, %d updated, %d removed, %d skipped (%d items)",
                result.added, result.updated, result.removed, result.skipped, index.count(),
            )
        except Exception as exc:  # noqa: BLE001 - offline or rclone busy: keep the old index and try again
            log.warning("scan failed: %s", exc)
        await trio.sleep(interval)


async def _stop_on_signal() -> None:
    with trio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
        async for _ in signals:
            pyfuse3.terminate()
            return


def run(
    folder: Path,
    back: Path,
    index_file: Path,
    remote: str,
    interval: int = 300,
    scan: bool = True,
    require_mount: bool = True,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    index = Index(index_file)
    if scan and index.last_scan() is None:
        log.info("first start: reading the Drive tree once (about a minute)")
        started = time.monotonic()
        index.apply_scan(rclone.list_tree(remote), started)
    folder.mkdir(parents=True, exist_ok=True)

    options = set(pyfuse3.default_options)
    options.add("fsname=drivux")
    # GIO only treats a few FUSE types as remote (sshfs is one). Declared remote, Nautilus
    # skips thumbnails and content sniffing here, which would read whole files from Google.
    options.add(f"subtype={FUSE_SUBTYPE}")
    pyfuse3.init(Front(index, back, require_mount), str(folder), options)

    async def main() -> None:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(_stop_on_signal)
            if scan:
                nursery.start_soon(refresh_loop, index, remote, interval)
            await pyfuse3.main()
            nursery.cancel_scope.cancel()

    try:
        trio.run(main)
    finally:
        pyfuse3.close(unmount=True)
        index.close()

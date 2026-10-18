"""On-disk index of the Drive tree: names, sizes and dates, never file contents.

The folder shows these names the moment it is mounted, without asking Google.
A background scan (`rclone lsjson -R`) keeps the index equal to the Drive.
"""

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_ACCOUNT, cache_dir

ROOT = 1
SCHEMA_VERSION = "1"
FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_APPS_PREFIX = "application/vnd.google-apps."
# A local change is trusted over a scan that started before it, for this long.
RECENT_SECONDS = 600


def index_path(name: str = DEFAULT_ACCOUNT) -> Path:
    return cache_dir(name) / "index.sqlite3"


def scan_info(path: Path) -> tuple[float, int] | None:
    """(time of the last scan, number of items) read without opening the index for writing."""
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = db.execute("SELECT value FROM meta WHERE key='last_scan'").fetchone()
        if not row:
            return None
        return float(row[0]), db.execute("SELECT COUNT(*) FROM nodes WHERE inode != ?", (ROOT,)).fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        db.close()


@dataclass(frozen=True)
class Node:
    inode: int
    parent: int
    name: str
    is_dir: bool
    size: int
    mtime_ns: int


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0  # same name twice in one folder, or a name taken by a local change


def _mtime_ns(text: str) -> int:
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1e9)
    except ValueError:
        return 0


class Index:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit; transactions are explicit
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._create()
        self._recent: dict[tuple[int, str], float] = {}
        self._paths: dict[int, str] = {}

    def _create(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if row and row[0] != SCHEMA_VERSION:  # the index is only a copy of the Drive: rebuild it
            self.db.execute("DROP TABLE IF EXISTS nodes")
            self.db.execute("DELETE FROM meta")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS nodes (
                inode INTEGER PRIMARY KEY AUTOINCREMENT,
                drive_id TEXT UNIQUE,
                parent INTEGER NOT NULL,
                name TEXT NOT NULL,
                is_dir INTEGER NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                UNIQUE (parent, name))"""
        )
        self.db.execute("INSERT OR IGNORE INTO nodes (inode, parent, name, is_dir) VALUES (?, ?, '', 1)", (ROOT, ROOT))
        self.set_meta("schema", SCHEMA_VERSION)

    def close(self) -> None:
        self.db.close()

    # Metadata

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def last_scan(self) -> float | None:
        value = self.get_meta("last_scan")
        return float(value) if value else None

    # Reading

    @staticmethod
    def _node(row) -> Node:
        return Node(row[0], row[1], row[2], bool(row[3]), row[4], row[5])

    _COLUMNS = "inode, parent, name, is_dir, size, mtime_ns"

    def get(self, inode: int) -> Node | None:
        row = self.db.execute(f"SELECT {self._COLUMNS} FROM nodes WHERE inode=?", (inode,)).fetchone()
        return self._node(row) if row else None

    def lookup(self, parent: int, name: str) -> Node | None:
        row = self.db.execute(
            f"SELECT {self._COLUMNS} FROM nodes WHERE parent=? AND name=? AND inode != ?", (parent, name, ROOT)
        ).fetchone()
        return self._node(row) if row else None

    def children(self, parent: int, after: int = 0, limit: int = 512) -> list[Node]:
        rows = self.db.execute(
            f"SELECT {self._COLUMNS} FROM nodes WHERE parent=? AND inode > ? AND inode != ? ORDER BY inode LIMIT ?",
            (parent, after, ROOT, limit),
        ).fetchall()
        return [self._node(row) for row in rows]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM nodes WHERE inode != ?", (ROOT,)).fetchone()[0]

    def path_of(self, inode: int) -> str:
        """Path relative to the Drive root ("" for the root itself)."""
        if inode == ROOT:
            return ""
        if inode in self._paths:
            return self._paths[inode]
        parts: list[str] = []
        current = inode
        for _ in range(256):  # the guard stops a loop if the index is ever corrupted
            if current == ROOT:
                break
            node = self.get(current)
            if node is None:
                raise KeyError(inode)
            parts.append(node.name)
            current = node.parent
        else:
            raise KeyError(inode)
        path = "/".join(reversed(parts))
        self._paths[inode] = path
        return path

    # Local changes (the Drive folder was changed by the user)

    def _touch(self, parent: int, name: str) -> None:
        self._recent[(parent, name)] = time.monotonic()

    def add_local(self, parent: int, name: str, is_dir: bool) -> Node:
        existing = self.lookup(parent, name)
        if existing:
            return existing
        cursor = self.db.execute(
            "INSERT INTO nodes (parent, name, is_dir, mtime_ns) VALUES (?, ?, ?, ?)",
            (parent, name, int(is_dir), time.time_ns()),
        )
        self._touch(parent, name)
        return self.get(cursor.lastrowid)

    def set_size(self, inode: int, size: int, mtime_ns: int) -> None:
        self.db.execute("UPDATE nodes SET size=?, mtime_ns=? WHERE inode=?", (size, mtime_ns, inode))

    def set_mtime(self, inode: int, mtime_ns: int) -> None:
        self.db.execute("UPDATE nodes SET mtime_ns=? WHERE inode=?", (mtime_ns, inode))

    def remove(self, inode: int) -> None:
        """Remove a node and everything below it."""
        node = self.get(inode)
        if node is None or inode == ROOT:
            return
        self.db.execute("BEGIN")
        try:
            pending = [inode]
            while pending:
                current = pending.pop()
                pending += [row[0] for row in self.db.execute(
                    "SELECT inode FROM nodes WHERE parent=? AND inode != ?", (current, ROOT))]
                self.db.execute("DELETE FROM nodes WHERE inode=?", (current,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self._paths.clear()
        self._touch(node.parent, node.name)

    def rename(self, inode: int, parent: int, name: str) -> None:
        node = self.get(inode)
        if node is None:
            return
        target = self.lookup(parent, name)
        if target and target.inode != inode:  # renaming over a file replaces it
            self.remove(target.inode)
        self.db.execute("UPDATE nodes SET parent=?, name=? WHERE inode=?", (parent, name, inode))
        self._paths.clear()
        self._touch(node.parent, node.name)
        self._touch(parent, name)

    # Scan

    def _is_recent(self, parent: int, name: str, started: float) -> bool:
        touched = self._recent.get((parent, name))
        return touched is not None and touched >= started

    def apply_scan(self, entries: list[dict], started: float) -> ScanResult:
        """Make the index equal to a full listing of the Drive (rclone lsjson -R).

        `started` is time.monotonic() from before the listing began: anything the
        user changed locally after that moment is newer than the listing and wins.
        """
        result = ScanResult()
        wanted: dict[str, dict] = {}
        for entry in entries:
            mime = entry.get("MimeType", "")
            if mime.startswith(GOOGLE_APPS_PREFIX) and mime != FOLDER_MIME:
                continue  # Docs, Sheets, shortcuts: they are not mounted either
            if entry["Path"] in wanted:  # the Drive allows two items with one name: only the first is shown
                result.skipped += 1
                continue
            wanted[entry["Path"]] = entry
        listed_ids = {entry["ID"] for entry in wanted.values()}
        now = time.monotonic()
        self._recent = {key: t for key, t in self._recent.items() if now - t < RECENT_SECONDS * 6}

        self.db.execute("BEGIN")
        try:
            # 1. Remove what is gone. Doing it first frees names for the renames below.
            for inode, drive_id, parent, name in self.db.execute(
                "SELECT inode, drive_id, parent, name FROM nodes WHERE inode != ?", (ROOT,)
            ).fetchall():
                if drive_id in listed_ids or self._is_recent(parent, name, started):
                    continue
                if drive_id is None:  # created here and never seen on the Drive: keep it for a while
                    touched = self._recent.get((parent, name))
                    if touched is not None and started - touched < RECENT_SECONDS:
                        continue
                self.db.execute("DELETE FROM nodes WHERE inode=?", (inode,))
                result.removed += 1

            # 2. Add and update, parents before children.
            by_id = dict(self.db.execute("SELECT drive_id, inode FROM nodes WHERE drive_id IS NOT NULL"))
            inode_of = {"": ROOT}
            for path in sorted(wanted, key=lambda p: p.count("/")):
                entry = wanted[path]
                parent_path, _, name = path.rpartition("/")
                parent = inode_of.get(parent_path)
                if parent is None:
                    result.skipped += 1
                    continue
                is_dir = bool(entry["IsDir"])
                size = 0 if is_dir else int(entry.get("Size", 0))
                mtime = _mtime_ns(entry.get("ModTime", ""))
                inode = self._upsert(by_id, entry["ID"], parent, name, is_dir, size, mtime, started, result)
                if inode is None:
                    continue
                if is_dir:
                    inode_of[path] = inode

            self.set_meta("last_scan", str(time.time()))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self._paths.clear()
        return result

    def _upsert(self, by_id, drive_id, parent, name, is_dir, size, mtime, started, result) -> int | None:
        try:
            if self._is_recent(parent, name, started):
                local = self.lookup(parent, name)
                result.skipped += 1
                return local.inode if local and local.is_dir else None
            inode = by_id.get(drive_id)
            if inode is None:
                local = self.lookup(parent, name)
                if local and self.db.execute("SELECT drive_id FROM nodes WHERE inode=?", (local.inode,)).fetchone()[0] is None:
                    # created here, now it shows up on the Drive: adopt it
                    self.db.execute(
                        "UPDATE nodes SET drive_id=?, size=?, mtime_ns=? WHERE inode=?", (drive_id, size, mtime, local.inode)
                    )
                    result.updated += 1
                    return local.inode
                if local:  # two different items with the same name in one folder
                    result.skipped += 1
                    return None
                cursor = self.db.execute(
                    "INSERT INTO nodes (drive_id, parent, name, is_dir, size, mtime_ns) VALUES (?, ?, ?, ?, ?, ?)",
                    (drive_id, parent, name, int(is_dir), size, mtime),
                )
                result.added += 1
                return cursor.lastrowid
            current = self.get(inode)
            if (current.parent, current.name, current.size, current.mtime_ns) != (parent, name, size, mtime):
                self.db.execute(
                    "UPDATE nodes SET parent=?, name=?, size=?, mtime_ns=? WHERE inode=?", (parent, name, size, mtime, inode)
                )
                result.updated += 1
            return inode
        except sqlite3.IntegrityError:  # a name taken by another item: the next scan sorts it out
            result.skipped += 1
            return None

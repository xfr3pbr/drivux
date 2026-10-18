"""Drivux command line. With no arguments, opens the user interface."""

import argparse
import sys
from pathlib import Path

from . import config, notifier, offline, rclone, service


def cmd_setup(args) -> int:
    issues = rclone.problems()
    if issues:
        print("\n\n".join(issues), file=sys.stderr)
        return 1
    folder = Path(args.folder).expanduser().resolve()
    service.check_folder(folder)
    print("Opening your browser: sign in with your Google account and click Allow.")
    rclone.create_remote(rclone.REMOTE, args.client_id or "", args.client_secret or "")
    cfg = config.Config(remote=rclone.REMOTE, folder=folder)
    config.save(cfg)
    service.install(cfg)
    print(f"Done. Google Drive is available at {folder}")
    return 0


def cmd_status(_args) -> int:
    cfg = config.load()
    if not cfg:
        print("Drivux is not set up. Run: drivux setup")
        return 1
    st = service.status()
    print(f"Folder:        {cfg.folder}")
    print(f"Connected:     {'yes' if st.active else 'no (paused)'}")
    print(f"At startup:    {'starts automatically' if st.enabled else 'does not start'}")
    print(f"Notifications: {'on' if st.notifications else 'off'}")
    print(f"Listing:       {'from the index on disk (instant)' if st.fast_listing else 'from Google Drive (rclone)'}")
    if st.active:
        print(f"Folders:       {'loading…' if st.loading_folders else 'loaded'}")
        print(f"Uploading:     {st.uploading + st.queued} file(s)")
        print(f"Local cache:   {st.cache_used / 1024**2:.0f} MB")
    return 0


def cmd_start(_args) -> int:
    service.start()
    return 0


def cmd_stop(_args) -> int:
    service.stop()
    return 0


def cmd_notify(_args) -> int:
    notifier.run(service.socket_path())
    return 0


def cmd_remove(_args) -> int:
    cfg = config.load()
    service.uninstall()
    if cfg:
        rclone.delete_remote(cfg.remote)
    config.remove()
    print("Drivux disconnected. Nothing was deleted from Google Drive.")
    return 0


def cmd_front(args) -> int:
    from . import front, index  # needs python-pyfuse3, which only this mode uses

    cfg = config.load()
    if not cfg and not (args.folder and args.back and args.remote):
        print("Drivux is not set up. Run: drivux setup", file=sys.stderr)
        return 1
    front.run(
        folder=Path(args.folder or cfg.folder),
        back=Path(args.back or service.mount_folder(cfg)),
        index_file=Path(args.index) if args.index else index.index_path(),
        remote=args.remote or cfg.remote,
        interval=args.interval,
        scan=not args.no_scan,
        require_mount=not args.no_mount_check,
    )
    return 0


def cmd_fast(args) -> int:
    cfg = config.load()
    if not cfg:
        raise ValueError("Drivux is not set up. Run: drivux setup")
    enabled = args.mode == "on"
    if enabled:
        try:
            import pyfuse3  # noqa: F401
        except ImportError:
            raise ValueError("Fast listing needs python-pyfuse3 (Arch) / python3-pyfuse3 (Debian, Fedora).") from None
    service.set_fast_listing(cfg, enabled)
    print("Fast listing " + ("on: folders appear at once, the list is kept on disk." if enabled else "off."))
    return 0


def _mounted_config() -> config.Config:
    cfg = config.load()
    if not cfg:
        raise ValueError("Drivux is not set up. Run: drivux setup")
    if not service.is_active():
        raise ValueError("The Drive folder is paused. Run: drivux start")
    return cfg


def _notify_done(title: str, body: str) -> None:
    try:
        notifier.dbus_sender()(title, body)
    except Exception:  # no desktop session (timer, ssh): the terminal output is enough
        pass


def _report(result: offline.Result) -> str:
    text = f"{result.files} file(s), {result.size / 1024**2:.0f} MB available offline"
    if result.failed:
        text += f"; {len(result.failed)} failed:\n  " + "\n  ".join(result.failed[:5])
    return text


def cmd_offline_add(args) -> int:
    cfg = _mounted_config()
    rels = [offline.relative(cfg.folder, path) for path in args.paths]
    offline.save(offline.add(offline.load(), rels))
    service.ensure_offline_timer()
    print("Downloading… the files stay available offline.")
    result = offline.fetch(cfg.folder, rels)
    print(_report(result))
    _notify_done("Available offline" if not result.failed else "Offline: some files failed", _report(result))
    return 1 if result.failed else 0


def cmd_offline_remove(args) -> int:
    cfg = config.load()
    if not cfg:
        raise ValueError("Drivux is not set up. Run: drivux setup")
    rels = [offline.relative(cfg.folder, path) for path in args.paths]
    offline.save(offline.remove(offline.load(), rels))
    print("No longer kept offline. The cached copies are removed when they are not used for a while.")
    return 0


def cmd_offline_list(_args) -> int:
    for item in offline.load():
        print(item)
    return 0


def cmd_offline_sync(_args) -> int:
    cfg = _mounted_config()
    result = offline.fetch(cfg.folder, offline.load())
    print(_report(result))
    return 1 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drivux", description="On-demand Google Drive for Linux.")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="connect your Google account and mount the folder")
    setup.add_argument("--folder", default=str(config.DEFAULT_FOLDER), help="Drive folder (default: ~/GoogleDrive)")
    setup.add_argument("--client-id", help="your own Google Cloud client ID (optional)")
    setup.add_argument("--client-secret", help="client secret for your own client ID")
    setup.set_defaults(func=cmd_setup)

    sub.add_parser("status", help="show the sync status").set_defaults(func=cmd_status)
    sub.add_parser("start", help="link the folder to Drive").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="pause (unlink the folder from Drive)").set_defaults(func=cmd_stop)
    sub.add_parser("remove", help="disconnect the account and remove the services").set_defaults(func=cmd_remove)
    sub.add_parser("notify", help="show notifications about what is syncing").set_defaults(func=cmd_notify)

    keep = sub.add_parser("offline", help="keep files and folders available without internet").add_subparsers(
        dest="action", required=True
    )
    add = keep.add_parser("add", help="download now and keep in the cache")
    add.add_argument("paths", nargs="+", help="files or folders inside the Drive folder")
    add.set_defaults(func=cmd_offline_add)
    rem = keep.add_parser("remove", help="stop keeping offline")
    rem.add_argument("paths", nargs="+", help="files or folders inside the Drive folder")
    rem.set_defaults(func=cmd_offline_remove)
    keep.add_parser("list", help="show what is kept offline").set_defaults(func=cmd_offline_list)
    keep.add_parser("sync", help="download again what is missing (the daily timer runs this)").set_defaults(
        func=cmd_offline_sync
    )

    front = sub.add_parser("front", help="run the instant-listing layer (the service does this)")
    front.add_argument("--folder", help="folder the user sees (default: from the settings)")
    front.add_argument("--back", help="where the rclone mount is (default: from the settings)")
    front.add_argument("--index", help="index file (default: ~/.cache/drivux/index.sqlite3)")
    front.add_argument("--remote", help="rclone remote name (default: from the settings)")
    front.add_argument("--interval", type=int, default=300, help="seconds between Drive scans (default: 300)")
    front.add_argument("--no-scan", action="store_true", help="do not read the Drive (tests)")
    front.add_argument("--no-mount-check", action="store_true", help="--back is a plain folder (tests)")
    front.set_defaults(func=cmd_front)
    fast = sub.add_parser("fast", help="turn instant folder listing on or off")
    fast.add_argument("mode", choices=["on", "off"])
    fast.set_defaults(func=cmd_fast)

    args = parser.parse_args(argv)
    if not args.command:
        from .app import main as gui_main

        return gui_main()
    try:
        return args.func(args)
    except (ValueError, rclone.RcloneError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

"""Drivux command line. With no arguments, opens the user interface."""

import argparse
import sys
import time
from pathlib import Path

from . import config, notifier, offline, rclone, service


def _accounts(name: str | None) -> list[config.Config]:
    """The account called `name`, or all of them."""
    if name:
        cfg = config.load(name)
        if not cfg:
            raise ValueError(f"There is no account called {name}. See: drivux account list")
        return [cfg]
    accounts = config.load_all()
    if not accounts:
        raise ValueError("Drivux is not set up. Run: drivux setup")
    return accounts


def _one_account(name: str | None) -> config.Config:
    """A single account: the one asked for, or the first."""
    return _accounts(name)[0]


def _connect(name: str, folder: str | None, client_id: str, client_secret: str) -> int:
    config.check_name(name)
    if config.load(name):
        raise ValueError(f"The account {name} is already connected.")
    issues = rclone.problems()
    if issues:
        print("\n\n".join(issues), file=sys.stderr)
        return 1
    target = Path(folder).expanduser().resolve() if folder else config.default_folder(name)
    service.check_folder(target)
    remote = config.default_remote(name)
    print("Opening your browser: sign in with your Google account and click Allow.")
    rclone.create_remote(remote, client_id, client_secret)
    cfg = config.Config(remote=remote, folder=target, name=name)
    config.save(cfg)
    service.install(cfg)
    print(f"Done. Google Drive ({name}) is available at {target}")
    return 0


def cmd_setup(args) -> int:
    return _connect(args.account or config.DEFAULT_ACCOUNT, args.folder, args.client_id or "", args.client_secret or "")


def cmd_account_add(args) -> int:
    return _connect(args.name, args.folder, args.client_id or "", args.client_secret or "")


def cmd_account_list(_args) -> int:
    for cfg in config.load_all():
        print(f"{cfg.name:<12} {cfg.folder}")
    return 0


def _ago(moment: float | None) -> str:
    if moment is None:
        return "not yet"
    seconds = max(0, int(time.time() - moment))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago" if seconds < 86400 else f"{seconds // 86400} d ago"


def cmd_status(args) -> int:
    accounts = _accounts(args.account)
    for position, cfg in enumerate(accounts):
        st = service.status(cfg.name)
        if position:
            print()
        if len(accounts) > 1:
            print(f"[{cfg.name}]")
        print(f"Folder:        {cfg.folder}")
        print(f"Connected:     {'yes' if st.active else 'no (paused)'}")
        print(f"At startup:    {'starts automatically' if st.enabled else 'does not start'}")
        print(f"Notifications: {'on' if st.notifications else 'off'}")
        print(f"Listing:       {'from the index on disk (instant)' if st.fast_listing else 'from Google Drive (rclone)'}")
        if st.fast_listing:
            print(f"Index:         {st.items} items, checked {_ago(st.last_scan)}")
        if st.active:
            print(f"Folders:       {'loading…' if st.loading_folders else 'loaded'}")
            print(f"Uploading:     {st.uploading + st.queued} file(s)")
            print(f"Local cache:   {st.cache_used / 1024**2:.0f} MB")
    return 0


def cmd_start(args) -> int:
    service.start(_one_account(args.account).name if args.account else None)
    return 0


def cmd_stop(args) -> int:
    service.stop(_one_account(args.account).name if args.account else None)
    return 0


def cmd_notify(args) -> int:
    cfg = _one_account(args.account)
    label = cfg.name if len(config.load_all()) > 1 else ""
    notifier.run(config.socket_path(cfg.name), label)
    return 0


def cmd_tray(_args) -> int:
    from . import tray  # needs the desktop libraries, which only this mode uses

    return tray.run()


def _remove_account(cfg: config.Config) -> None:
    service.uninstall(cfg.name)
    rclone.delete_remote(cfg.remote)
    config.remove(cfg.name)


def cmd_remove(args) -> int:
    accounts = config.load_all()
    if len(accounts) > 1 and not args.account:
        raise ValueError("Several accounts are connected. Choose one: drivux remove --account NAME")
    for cfg in _accounts(args.account):
        _remove_account(cfg)
    print("Drivux disconnected. Nothing was deleted from Google Drive.")
    return 0


def cmd_account_remove(args) -> int:
    _remove_account(_one_account(args.name))
    print(f"Account {args.name} disconnected. Nothing was deleted from Google Drive.")
    return 0


def cmd_front(args) -> int:
    from . import front, index  # needs python-pyfuse3, which only this mode uses

    cfg = config.load(args.account) if args.account else config.load()
    if not cfg and not (args.folder and args.back and args.remote):
        print("Drivux is not set up. Run: drivux setup", file=sys.stderr)
        return 1
    front.run(
        folder=Path(args.folder or cfg.folder),
        back=Path(args.back or service.mount_folder(cfg)),
        index_file=Path(args.index) if args.index else index.index_path(cfg.name),
        remote=args.remote or cfg.remote,
        interval=args.interval,
        scan=not args.no_scan,
        require_mount=not args.no_mount_check,
    )
    return 0


def cmd_fast(args) -> int:
    accounts = _accounts(args.account)
    enabled = args.mode == "on"
    if enabled:
        try:
            import pyfuse3  # noqa: F401
        except ImportError:
            raise ValueError("Fast listing needs python-pyfuse3 (Arch) / python3-pyfuse3 (Debian, Fedora).") from None
    for cfg in accounts:
        service.set_fast_listing(cfg, enabled)
    print("Fast listing " + ("on: folders appear at once, the list is kept on disk." if enabled else "off."))
    return 0


def cmd_tray_switch(args) -> int:
    service.set_tray(args.mode == "on")
    return 0


def _mounted(cfg: config.Config) -> config.Config:
    if not service.is_active(service.unit_name("folder", cfg.name)):
        raise ValueError(f"The Drive folder {cfg.folder} is paused. Run: drivux start")
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


def _by_account(paths: list[str]) -> dict[str, tuple[config.Config, list[str]]]:
    """The paths grouped by the account whose folder holds them (relative to that folder)."""
    groups: dict[str, tuple[config.Config, list[str]]] = {}
    for path in paths:
        cfg = config.account_of(path)
        if not cfg:
            raise ValueError(f"{path} is not inside a Drive folder.")
        groups.setdefault(cfg.name, (cfg, []))[1].append(offline.relative(cfg.folder, path))
    return groups


def cmd_offline_add(args) -> int:
    failed = False
    for cfg, rels in _by_account(args.paths).values():
        _mounted(cfg)
        offline.save(offline.add(offline.load(cfg.name), rels), cfg.name)
        service.ensure_offline_timer(cfg.name)
        print("Downloading… the files stay available offline.")
        result = offline.fetch(cfg.folder, rels)
        print(_report(result))
        _notify_done("Available offline" if not result.failed else "Offline: some files failed", _report(result))
        failed = failed or bool(result.failed)
    return 1 if failed else 0


def cmd_offline_remove(args) -> int:
    for cfg, rels in _by_account(args.paths).values():
        offline.save(offline.remove(offline.load(cfg.name), rels), cfg.name)
    print("No longer kept offline. The cached copies are removed when they are not used for a while.")
    return 0


def cmd_offline_list(args) -> int:
    accounts = _accounts(args.account)
    for cfg in accounts:
        for item in offline.load(cfg.name):
            print(f"{cfg.name}: {item}" if len(accounts) > 1 else item)
    return 0


def cmd_offline_sync(args) -> int:
    failed = False
    for cfg in _accounts(args.account):
        result = offline.fetch(_mounted(cfg).folder, offline.load(cfg.name))
        print(_report(result))
        failed = failed or bool(result.failed)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drivux", description="On-demand Google Drive for Linux.")
    sub = parser.add_subparsers(dest="command")
    account = argparse.ArgumentParser(add_help=False)
    account.add_argument("-a", "--account", help="account name (default: all of them, or the first)")

    setup = sub.add_parser("setup", help="connect your Google account and mount the folder", parents=[account])
    setup.add_argument("--folder", help="Drive folder (default: ~/GoogleDrive, or ~/GoogleDrive-NAME)")
    setup.add_argument("--client-id", help="your own Google Cloud client ID (optional)")
    setup.add_argument("--client-secret", help="client secret for your own client ID")
    setup.set_defaults(func=cmd_setup)

    accounts = sub.add_parser("account", help="connect more than one Google account").add_subparsers(
        dest="action", required=True
    )
    add_account = accounts.add_parser("add", help="connect another Google account")
    add_account.add_argument("name", help="short name, for example: work")
    add_account.add_argument("--folder", help="Drive folder (default: ~/GoogleDrive-NAME)")
    add_account.add_argument("--client-id", help="your own Google Cloud client ID (optional)")
    add_account.add_argument("--client-secret", help="client secret for your own client ID")
    add_account.set_defaults(func=cmd_account_add)
    accounts.add_parser("list", help="show the connected accounts").set_defaults(func=cmd_account_list)
    remove_account = accounts.add_parser("remove", help="disconnect one account")
    remove_account.add_argument("name")
    remove_account.set_defaults(func=cmd_account_remove)

    sub.add_parser("status", help="show the sync status", parents=[account]).set_defaults(func=cmd_status)
    sub.add_parser("start", help="link the folder to Drive", parents=[account]).set_defaults(func=cmd_start)
    sub.add_parser("stop", help="pause (unlink the folder from Drive)", parents=[account]).set_defaults(func=cmd_stop)
    sub.add_parser("remove", help="disconnect the account and remove the services", parents=[account]).set_defaults(
        func=cmd_remove
    )
    sub.add_parser("notify", help="show notifications about what is syncing", parents=[account]).set_defaults(
        func=cmd_notify
    )
    sub.add_parser("tray", help="show the sync status icon in the system tray (the service does this)").set_defaults(
        func=cmd_tray
    )
    tray_switch = sub.add_parser("tray-icon", help="turn the tray icon on or off")
    tray_switch.add_argument("mode", choices=["on", "off"])
    tray_switch.set_defaults(func=cmd_tray_switch)

    keep = sub.add_parser("offline", help="keep files and folders available without internet").add_subparsers(
        dest="action", required=True
    )
    add = keep.add_parser("add", help="download now and keep in the cache")
    add.add_argument("paths", nargs="+", help="files or folders inside a Drive folder")
    add.set_defaults(func=cmd_offline_add)
    rem = keep.add_parser("remove", help="stop keeping offline")
    rem.add_argument("paths", nargs="+", help="files or folders inside a Drive folder")
    rem.set_defaults(func=cmd_offline_remove)
    keep.add_parser("list", help="show what is kept offline", parents=[account]).set_defaults(func=cmd_offline_list)
    keep.add_parser(
        "sync", help="download again what is missing (the daily timer runs this)", parents=[account]
    ).set_defaults(func=cmd_offline_sync)

    front = sub.add_parser("front", help="run the instant-listing layer (the service does this)", parents=[account])
    front.add_argument("--folder", help="folder the user sees (default: from the settings)")
    front.add_argument("--back", help="where the rclone mount is (default: from the settings)")
    front.add_argument("--index", help="index file (default: ~/.cache/drivux/index.sqlite3)")
    front.add_argument("--remote", help="rclone remote name (default: from the settings)")
    front.add_argument("--interval", type=int, default=300, help="seconds between Drive scans (default: 300)")
    front.add_argument("--no-scan", action="store_true", help="do not read the Drive (tests)")
    front.add_argument("--no-mount-check", action="store_true", help="--back is a plain folder (tests)")
    front.set_defaults(func=cmd_front)
    fast = sub.add_parser("fast", help="turn instant folder listing on or off", parents=[account])
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

"""Drivux command line. With no arguments, opens the user interface."""

import argparse
import sys
from pathlib import Path

from . import config, notifier, rclone, service


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

    args = parser.parse_args(argv)
    if not args.command:
        from .app import main as gui_main

        return gui_main()
    try:
        return args.func(args)
    except (ValueError, rclone.RcloneError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

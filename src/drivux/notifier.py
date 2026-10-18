"""Desktop notifications about what is still syncing."""

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, rclone

POLL_SECONDS = 2
# Only notify after N consecutive reads with pending work (~4 s), so quick
# saves don't flash on screen.
BUSY_TICKS_BEFORE_NOTIFY = 2
MAX_LISTED = 4


@dataclass
class Activity:
    uploads: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    errors: int = 0
    last_error: str = ""


def read_activity(socket: Path) -> Activity | None:
    """Read pending work from rclone. None if the folder is not mounted."""
    try:
        stats = rclone.rc(socket, "core/stats")
    except rclone.RcloneError:
        return None
    try:
        uploads = [item["name"] for item in rclone.rc(socket, "vfs/queue").get("queue", [])]
    except rclone.RcloneError:
        uploads = []  # older rclone without vfs/queue: use transfers only
    downloads = [t["name"] for t in stats.get("transferring", []) if t["name"] not in uploads]
    return Activity(uploads, downloads, stats.get("errors", 0), stats.get("lastError", ""))


def describe(act: Activity, label: str = "") -> tuple[str, str]:
    """Title and body of the progress notification."""
    names = [f"↑ {Path(n).name}" for n in act.uploads] + [f"↓ {Path(n).name}" for n in act.downloads]
    total = len(names)
    title = f"Syncing {total} file{'s' if total > 1 else ''}"
    if label:
        title += f" ({label})"
    body = "\n".join(names[:MAX_LISTED])
    if total > MAX_LISTED:
        body += f"\nand {total - MAX_LISTED} more"
    return title, body


class Notifier:
    """Decide when to show, update and close notifications."""

    def __init__(self, send, label: str = "", types=lambda: config.NOTIFY_TYPES):
        # send(title, body, *, replaces, persistent, critical) -> notification id
        self.send = send
        self.label = label  # account name, when there is more than one
        self.types = types  # called every read: the kinds of notification the user wants
        self.progress_id = 0
        self.last_shown: tuple[str, str] | None = None
        self.busy_ticks = 0
        self.errors_seen: int | None = None
        self.worked = False  # a sync long enough to notify about has happened

    def titled(self, title: str) -> str:
        return f"{title} ({self.label})" if self.label else title

    def update(self, act: Activity | None) -> None:
        if act is None:
            self.busy_ticks = 0
            return
        wanted = set(self.types())
        busy = bool(act.uploads or act.downloads)
        shown = Activity(
            act.uploads if "uploads" in wanted else [],
            act.downloads if "downloads" in wanted else [],
        )

        if self.errors_seen is None:
            self.errors_seen = act.errors  # ignore errors from before Drivux started
        elif act.errors > self.errors_seen:
            self.errors_seen = act.errors
            if "errors" in wanted:
                self.send(self.titled("Sync error"), act.last_error or "See the Drivux log.", critical=True)

        if busy:
            self.busy_ticks += 1
            if self.busy_ticks < BUSY_TICKS_BEFORE_NOTIFY:
                return
            self.worked = True
            if shown.uploads or shown.downloads:
                message = describe(shown, self.label)
                if message != self.last_shown:
                    self.progress_id = self.send(*message, replaces=self.progress_id, persistent=True)
                    self.last_shown = message
            return

        self.busy_ticks = 0
        if self.worked:
            if "done" in wanted:
                self.send(self.titled("Everything is synced"), "Your files are up to date with Google Drive.", replaces=self.progress_id)
            self.progress_id = 0
            self.last_shown = None
            self.worked = False


def dbus_sender():
    """Send notifications through the freedesktop standard (works on any desktop)."""
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def send(title: str, body: str, *, replaces: int = 0, persistent: bool = False, critical: bool = False) -> int:
        hints = {
            "urgency": GLib.Variant("y", 2 if critical else 1),
            "desktop-entry": GLib.Variant("s", "io.github.xfr3pbr.Drivux"),
        }
        if not persistent:
            hints["transient"] = GLib.Variant("b", True)
        timeout = 0 if persistent or critical else 4000
        reply = bus.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            GLib.Variant(
                "(susssasa{sv}i)",
                ("Drivux", replaces, "folder-remote", title, body, [], hints, timeout),
            ),
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return reply.unpack()[0]

    return send


def run(socket: Path, label: str = "", name: str = config.DEFAULT_ACCOUNT) -> None:
    def types() -> list[str]:  # read again each time, so the preferences window takes effect at once
        cfg = config.load(name)
        return cfg.notify_types if cfg else list(config.NOTIFY_TYPES)

    notifier = Notifier(dbus_sender(), label, types)
    while True:
        notifier.update(read_activity(socket))
        time.sleep(POLL_SECONDS)

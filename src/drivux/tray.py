"""Sync status icon in the system tray (StatusNotifierItem, the standard of KDE, Waybar, Quickshell, GNOME with
the AppIndicator extension...). Written straight on D-Bus so it needs nothing beyond PyGObject.

The icon shows whether everything is synced, syncing or paused. Hovering it lists what is going up and down;
the menu opens the folders and the Drivux window and pauses or resumes the sync.
"""

import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field

from . import config, notifier, service

POLL_SECONDS = 2
ERROR_SHOWN_SECONDS = 60
MAX_LISTED = 5

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="u" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ia{sv})"/></signal>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
  </interface>
</node>
"""


@dataclass
class Snapshot:
    """What one account is doing right now."""

    name: str
    folder: str
    active: bool
    loading: bool = False
    pending: int = 0
    uploads: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    error: str = ""  # a recent sync error


@dataclass
class Summary:
    icon: str
    status: str  # StatusNotifierItem status: Active, NeedsAttention
    headline: str
    lines: list[str]


def _file_lines(snap: Snapshot) -> list[str]:
    names = [f"↑ {os.path.basename(n)}" for n in snap.uploads] + [f"↓ {os.path.basename(n)}" for n in snap.downloads]
    lines = names[:MAX_LISTED]
    if len(names) > MAX_LISTED:
        lines.append(f"and {len(names) - MAX_LISTED} more")
    return lines


def account_line(snap: Snapshot) -> str:
    if not snap.active:
        return "Paused"
    if snap.error:
        return f"Error: {snap.error}"
    if snap.loading:
        return "Loading folders…"
    busy = max(snap.pending, len(snap.uploads) + len(snap.downloads))
    if busy:
        return f"Syncing {busy} file{'s' if busy > 1 else ''}"
    return "Everything is synced"


def summarize(snaps: list[Snapshot]) -> Summary:
    """One icon for all accounts, and a line (plus the files) for each."""
    many = len(snaps) > 1
    lines: list[str] = []
    for snap in snaps:
        lines.append(f"{snap.name}: {account_line(snap)}" if many else account_line(snap))
        lines += [("  " if many else "") + line for line in _file_lines(snap)]
    if any(s.error for s in snaps):
        icon, status, headline = "dialog-warning-symbolic", "NeedsAttention", "Sync error"
    elif snaps and not any(s.active for s in snaps):
        icon, status, headline = "media-playback-pause-symbolic", "Active", "Paused"
    elif any(s.loading or s.pending or s.uploads or s.downloads for s in snaps if s.active):
        icon, status, headline = "emblem-synchronizing-symbolic", "Active", "Syncing"
    else:
        icon, status, headline = "folder-remote-symbolic", "Active", "Everything is synced"
    return Summary(icon, status, headline, lines)


def read_snapshot(cfg: config.Config, error_until: dict[str, float], baseline: dict[str, int]) -> Snapshot:
    """Ask systemd and rclone about one account (blocking: call it off the UI thread)."""
    st = service.status(cfg.name)
    snap = Snapshot(cfg.name, str(cfg.folder), st.active, st.loading_folders, st.uploading + st.queued)
    if st.active:
        act = notifier.read_activity(config.socket_path(cfg.name))
        if act:
            snap.uploads, snap.downloads = act.uploads, act.downloads
            if cfg.name not in baseline:
                baseline[cfg.name] = act.errors  # errors from before the tray started are old news
            elif act.errors > baseline[cfg.name]:
                baseline[cfg.name] = act.errors
                error_until[cfg.name] = time.monotonic() + ERROR_SHOWN_SECONDS
                snap.error = act.last_error or "see the Drivux log"
    if not snap.error and time.monotonic() < error_until.get(cfg.name, 0):
        snap.error = "see the Drivux log"
    return snap


def _drivux_command() -> list[str]:
    found = shutil.which("drivux")
    return [found] if found else [sys.executable, "-m", "drivux"]


def run() -> int:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    state = {"summary": summarize([]), "snaps": [], "revision": 1}
    error_until: dict[str, float] = {}
    baseline: dict[str, int] = {}
    holder: dict = {}

    def open_window() -> None:
        Gio.Subprocess.new(_drivux_command(), Gio.SubprocessFlags.NONE)

    def open_folder(folder: str) -> None:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(folder).get_uri(), None)

    def toggle_pause() -> None:
        pause = any(s.active for s in state["snaps"])
        threading.Thread(target=service.stop if pause else service.start, daemon=True).start()

    # Menu

    def menu_items() -> list[dict]:
        snaps = state["snaps"]
        items = []
        for snap in snaps:
            prefix = f"{snap.name}: " if len(snaps) > 1 else ""
            items.append({"label": prefix + account_line(snap), "enabled": False})
            label = f"Open folder {snap.name}" if len(snaps) > 1 else "Open folder"
            items.append({"label": label, "action": lambda f=snap.folder: open_folder(f)})
        if snaps:
            items.append({"type": "separator"})
        items.append({"label": "Open Drivux", "action": open_window})
        if snaps:
            items.append({"label": "Pause sync" if any(s.active for s in snaps) else "Resume sync", "action": toggle_pause})
        return [{**item, "id": number} for number, item in enumerate(items, 1)]

    def item_properties(item: dict) -> dict:
        props = {"label": GLib.Variant("s", item.get("label", "")), "enabled": GLib.Variant("b", item.get("enabled", True))}
        if item.get("type"):
            props["type"] = GLib.Variant("s", item["type"])
        return props

    def layout() -> GLib.Variant:
        children = [GLib.Variant("(ia{sv}av)", (item["id"], item_properties(item), [])) for item in menu_items()]
        return GLib.Variant("(u(ia{sv}av))", (state["revision"], (0, {"children-display": GLib.Variant("s", "submenu")}, children)))

    def menu_call(_conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method == "GetLayout":
            invocation.return_value(layout())
        elif method == "GetGroupProperties":
            wanted = params.unpack()[0]
            rows = [(item["id"], item_properties(item)) for item in menu_items() if not wanted or item["id"] in wanted]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
        elif method == "GetProperty":
            invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
        elif method == "Event":
            ident, event = params.unpack()[:2]
            for item in menu_items():
                if item["id"] == ident and event == "clicked" and item.get("action"):
                    GLib.idle_add(item["action"])
            invocation.return_value(None)
        elif method == "EventGroup":
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def menu_property(_conn, _sender, _path, _iface, name):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(name)

    # Item

    def item_call(_conn, _sender, _path, _iface, method, _params, invocation) -> None:
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(open_window)
        invocation.return_value(None)

    def item_property(_conn, _sender, _path, _iface, name):
        summary: Summary = state["summary"]
        tooltip = GLib.Variant("(sa(iiay)ss)", (summary.icon, [], f"Drivux: {summary.headline}", "\n".join(summary.lines)))
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "drivux"),
            "Title": GLib.Variant("s", f"Drivux: {summary.headline}"),
            "Status": GLib.Variant("s", summary.status),
            "WindowId": GLib.Variant("u", 0),
            "IconName": GLib.Variant("s", summary.icon),
            "IconPixmap": GLib.Variant("a(iiay)", []),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", "dialog-warning-symbolic"),
            "ToolTip": tooltip,
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(name)

    def emit(path: str, iface: str, signal: str, args: GLib.Variant | None = None) -> None:
        holder["conn"].emit_signal(None, path, iface, signal, args)

    # Updating

    def apply(snaps: list[Snapshot]) -> None:
        previous = state["summary"]
        state["snaps"], state["summary"] = snaps, summarize(snaps)
        if "conn" not in holder:
            return
        summary: Summary = state["summary"]
        if (summary.icon, summary.headline) != (previous.icon, previous.headline):
            for signal in ("NewIcon", "NewTitle"):
                emit(ITEM_PATH, "org.kde.StatusNotifierItem", signal)
        if summary.status != previous.status:
            emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewStatus", GLib.Variant("(s)", (summary.status,)))
        emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip")
        state["revision"] += 1
        emit(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated", GLib.Variant("(ui)", (state["revision"], 0)))

    def poll() -> bool:
        if holder.get("busy"):
            return GLib.SOURCE_CONTINUE
        holder["busy"] = True

        def work() -> None:
            snaps = [read_snapshot(cfg, error_until, baseline) for cfg in config.load_all()]
            holder["busy"] = False
            GLib.idle_add(apply, snaps)

        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_CONTINUE

    # D-Bus

    def on_bus(conn, _name) -> None:
        holder["conn"] = conn
        item = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        conn.register_object(ITEM_PATH, item, item_call, item_property, None)
        conn.register_object(MENU_PATH, menu, menu_call, menu_property, None)
        register()

    def register() -> None:
        """Tell the tray host about the icon, once the objects exist and the host is on the bus."""
        if "conn" in holder and holder.get("watcher"):
            holder["conn"].call(
                WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (holder["bus_name"],)), None, Gio.DBusCallFlags.NONE, -1, None, None,
            )

    def on_watcher(_conn, _name, _owner) -> None:
        holder["watcher"] = True
        register()

    def on_watcher_gone(_conn, _name) -> None:
        holder["watcher"] = False

    holder["bus_name"] = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
    Gio.bus_own_name(
        Gio.BusType.SESSION, holder["bus_name"], Gio.BusNameOwnerFlags.NONE, on_bus, None, None
    )
    Gio.bus_watch_name(Gio.BusType.SESSION, WATCHER, Gio.BusNameWatcherFlags.NONE, on_watcher, on_watcher_gone)

    poll()
    GLib.timeout_add_seconds(POLL_SECONDS, poll)
    GLib.MainLoop().run()
    return 0

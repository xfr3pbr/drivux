"""Drivux GTK4/libadwaita user interface."""

import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__, config, rclone, service  # noqa: E402

APP_ID = "io.github.xfr3pbr.Drivux"
SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(text: str) -> int:
    """Convert rclone-style sizes ("10G") to bytes."""
    unit = text[-1].upper()
    if unit in SIZE_UNITS:
        return int(float(text[:-1]) * SIZE_UNITS[unit])
    return int(text)


def format_size(size: int) -> str:
    """Size as Google shows it (base 1024, labelled GB)."""
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024:
            break
        size /= 1024
    else:
        unit = "TB"
    if unit == "bytes":
        return f"{int(size)} bytes"
    return f"{size:.1f}".rstrip("0").rstrip(".") + f" {unit}"


def run_in_thread(func, callback):
    """Run func off the UI thread and hand (result, error) to callback."""

    def worker():
        try:
            result, error = func(), None
        except Exception as exc:  # noqa: BLE001 - the error is shown in the UI
            result, error = None, exc
        GLib.idle_add(callback, result, error)

    threading.Thread(target=worker, daemon=True).start()


def usage_row(title: str) -> tuple[Adw.ActionRow, Gtk.LevelBar]:
    """Row with a usage bar, Baobab style."""
    row = Adw.ActionRow(title=title)
    bar = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER, width_request=160)
    row.add_suffix(bar)
    return row, bar


class DrivuxWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Drivux", default_width=480, default_height=680)
        self.folder = config.DEFAULT_FOLDER
        self.cfg = None
        self.accounts: list[config.Config] = []
        self._updating = False
        self._status_busy = False

        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)

        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("About Drivux", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        self.stack.add_named(self._build_deps_page(), "deps")
        self.stack.add_named(self._build_setup_page(), "setup")
        self.stack.add_named(self._build_busy_page(), "busy")
        self.stack.add_named(self._build_main_page(), "main")

        self.show_initial_page()
        GLib.timeout_add_seconds(2, self._tick)
        GLib.timeout_add_seconds(60, self._refresh_quota)

    # Pages

    def _build_deps_page(self) -> Gtk.Widget:
        self.deps_page = Adw.StatusPage(icon_name="dialog-warning-symbolic", title="Missing programs")
        button = Gtk.Button(label="Check again", halign=Gtk.Align.CENTER, css_classes=["pill"])
        button.connect("clicked", lambda _b: self.show_initial_page())
        self.deps_page.set_child(button)
        return self.deps_page

    def _build_setup_page(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            icon_name="folder-remote-symbolic",
            title="Google Drive on your Linux",
            description="See all your Drive files in a folder.\n"
            "Each file is only downloaded when you open it.",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)

        group = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title="Account name", text=config.DEFAULT_ACCOUNT)
        self.name_row.connect("changed", self._on_name_changed)
        group.add(self.name_row)
        self.folder_row = Adw.ActionRow(title="Folder", subtitle=str(self.folder))
        choose = Gtk.Button(label="Choose", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._on_choose_folder)
        self.folder_row.add_suffix(choose)
        group.add(self.folder_row)

        advanced = Adw.ExpanderRow(
            title="Advanced",
            subtitle="Your own Google access (client ID), optional",
        )
        self.client_id_row = Adw.EntryRow(title="Client ID")
        self.client_secret_row = Adw.PasswordEntryRow(title="Client secret")
        advanced.add_row(self.client_id_row)
        advanced.add_row(self.client_secret_row)
        group.add(advanced)
        box.append(group)

        connect = Gtk.Button(
            label="Connect to Google Drive",
            halign=Gtk.Align.CENTER,
            css_classes=["pill", "suggested-action"],
        )
        connect.connect("clicked", self._on_connect)
        box.append(connect)
        self.back_button = Gtk.Button(label="Back", halign=Gtk.Align.CENTER, css_classes=["flat"])
        self.back_button.connect("clicked", lambda _b: self.show_initial_page())
        box.append(self.back_button)

        page.set_child(Adw.Clamp(child=box, maximum_size=420))
        return page

    def _build_busy_page(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            title="Waiting for sign-in in your browser",
            description="Sign in with your Google account and click Allow.",
        )
        if hasattr(Adw, "SpinnerPaintable"):  # libadwaita 1.6+
            page.set_paintable(Adw.SpinnerPaintable(widget=self))
        else:
            page.set_child(Gtk.Spinner(spinning=True, width_request=48, height_request=48))
        return page

    def _build_main_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=24)

        # Header in the style of Google Drive for desktop: icon, status and button.
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.state_icon = Gtk.Image(icon_name="folder-remote-symbolic", pixel_size=72, css_classes=["dim-label"])
        self.state_title = Gtk.Label(label="Google Drive", css_classes=["title-1"], wrap=True)
        self.state_text = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        open_button = Gtk.Button(
            label="Open folder",
            halign=Gtk.Align.CENTER,
            margin_top=12,
            css_classes=["pill", "suggested-action"],
        )
        open_button.connect("clicked", self._on_open_folder)
        for widget in (self.state_icon, self.state_title, self.state_text, open_button):
            header.append(widget)
        box.append(header)

        self.account_group = Adw.PreferencesGroup(title="Account")
        self.account_row = Adw.ComboRow(title="Google account")
        self.account_row.connect("notify::selected", self._on_account_selected)
        add_account = Gtk.Button(label="Add account", valign=Gtk.Align.CENTER)
        add_account.connect("clicked", lambda _b: self.show_setup())
        self.account_row.add_suffix(add_account)
        self.account_group.add(self.account_row)
        box.append(self.account_group)

        space = Adw.PreferencesGroup(title="Storage")
        self.cloud_row, self.cloud_bar = usage_row("Google Drive")
        self.cloud_row.set_subtitle("Calculating…")
        self.local_row, self.local_bar = usage_row("Cached")
        space.add(self.cloud_row)
        space.add(self.local_row)
        box.append(space)

        prefs = Adw.PreferencesGroup(title="Preferences")
        self.connected_row = Adw.SwitchRow(title="Drive connected", subtitle="Turn off to pause")
        self.connected_row.connect("notify::active", self._on_connected_toggled)
        self.startup_row = Adw.SwitchRow(title="Start with the computer")
        self.startup_row.connect("notify::active", self._on_startup_toggled)
        self.notify_row = Adw.SwitchRow(title="Notifications", subtitle="Show what is still syncing")
        self.notify_row.connect("notify::active", self._on_notify_toggled)
        self.tray_row = Adw.SwitchRow(title="Tray icon", subtitle="Show the sync status next to the clock")
        self.tray_row.connect("notify::active", self._on_tray_toggled)
        self.index_row = Adw.ActionRow(title="Folder list")
        self.main_folder_row = Adw.ActionRow(title="Folder")
        prefs.add(self.connected_row)
        prefs.add(self.startup_row)
        prefs.add(self.notify_row)
        prefs.add(self.tray_row)
        prefs.add(self.index_row)
        prefs.add(self.main_folder_row)
        box.append(prefs)

        disconnect = Gtk.Button(
            label="Disconnect account",
            halign=Gtk.Align.CENTER,
            css_classes=["pill", "destructive-action"],
        )
        disconnect.connect("clicked", self._on_disconnect)
        box.append(disconnect)

        clamp = Adw.Clamp(child=box, maximum_size=480, margin_start=12, margin_end=12)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

    # Navigation

    def show_initial_page(self) -> None:
        issues = rclone.problems()
        if issues:
            self.deps_page.set_description("\n\n".join(issues) + "\n\nThen click Check again.")
            self.stack.set_visible_child_name("deps")
            return
        self.accounts = [cfg for cfg in config.load_all() if service.unit_path(service.unit_name("folder", cfg.name)).exists()]
        if self.accounts:
            self.show_main(self.cfg if self.cfg in self.accounts else self.accounts[0])
        else:
            self.show_setup()

    def show_setup(self) -> None:
        taken = {cfg.name for cfg in config.load_all()}
        name = config.DEFAULT_ACCOUNT
        number = 1
        while name in taken:  # suggest work, then account2, account3...
            number += 1
            name = "work" if number == 2 else f"account{number}"
        self.name_row.set_text(name)
        self.back_button.set_visible(bool(self.accounts))
        self.stack.set_visible_child_name("setup")

    def show_main(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self._updating = True
        names = Gtk.StringList()
        for account in self.accounts:
            names.append(f"{account.name} · {account.folder}" if len(self.accounts) > 1 else str(account.folder))
        self.account_row.set_model(names)
        self.account_row.set_selected(next(i for i, a in enumerate(self.accounts) if a.name == cfg.name))
        self._updating = False
        self.cache_max = parse_size(cfg.cache_max_size)
        self.main_folder_row.set_subtitle(str(cfg.folder))
        self.cloud_row.set_subtitle("Calculating…")
        self.stack.set_visible_child_name("main")
        self._refresh_quota()
        self._tick()

    def _on_account_selected(self, row, _pspec) -> None:
        if self._updating or not self.accounts:
            return
        selected = row.get_selected()
        if 0 <= selected < len(self.accounts) and self.accounts[selected].name != self.cfg.name:
            self.show_main(self.accounts[selected])

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=6))

    # Setup

    def _on_name_changed(self, row) -> None:
        name = row.get_text().strip()
        if config.NAME_PATTERN.fullmatch(name):
            self.folder = config.default_folder(name)
            self.folder_row.set_subtitle(str(self.folder))

    def _on_choose_folder(self, _button) -> None:
        dialog = Gtk.FileDialog(title="Google Drive folder", initial_folder=Gio.File.new_for_path(str(Path.home())))
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        self.folder = Path(folder.get_path())
        self.folder_row.set_subtitle(str(self.folder))

    def _on_connect(self, _button) -> None:
        folder = self.folder
        name = self.name_row.get_text().strip()
        client_id = self.client_id_row.get_text().strip()
        client_secret = self.client_secret_row.get_text().strip()

        def work() -> config.Config:
            config.check_name(name)
            if config.load(name):
                raise ValueError(f"The account {name} is already connected.")
            service.check_folder(folder)
            remote = config.default_remote(name)
            rclone.create_remote(remote, client_id, client_secret)
            cfg = config.Config(remote=remote, folder=folder, name=name)
            config.save(cfg)
            service.install(cfg)
            return cfg

        self.stack.set_visible_child_name("busy")
        run_in_thread(work, self._on_connect_done)

    def _on_connect_done(self, cfg, error) -> None:
        if error:
            self.stack.set_visible_child_name("setup")
            self.toast(f"Could not connect: {error}")
            return
        self.accounts = [*self.accounts, cfg]
        self.show_main(cfg)
        self.toast("Google Drive connected")

    # Status

    def _tick(self) -> bool:
        if self.stack.get_visible_child_name() == "main" and not self._status_busy:
            self._status_busy = True
            name = self.cfg.name
            run_in_thread(lambda: service.status(name), self._show_status)
        return GLib.SOURCE_CONTINUE

    def _show_status(self, st: service.Status, error) -> None:
        self._status_busy = False
        if error or st.name != self.cfg.name:
            return
        pending = st.uploading + st.queued
        if not st.active:
            title, text, icon = "Paused", "The folder is not linked to Drive.", "media-playback-pause-symbolic"
        elif st.loading_folders:
            title, text, icon = "Loading folders…", "Fetching the file list from Drive.", "content-loading-symbolic"
        elif pending:
            title = f"Uploading {pending} file{'s' if pending > 1 else ''}…"
            text, icon = "Your changes are going up to Drive.", "folder-remote-symbolic"
        else:
            title, text, icon = "Everything is synced", "Files open on demand.", "object-select-symbolic"
        if st.active and st.fast_listing and st.last_scan:
            text += f" Checked Drive {self._ago(st.last_scan)}."
        self.state_title.set_label(title)
        self.state_text.set_label(text)
        self.state_icon.set_from_icon_name(icon)

        self._updating = True
        self.connected_row.set_active(st.active)
        self.startup_row.set_active(st.enabled)
        self.notify_row.set_active(st.notifications)
        self.tray_row.set_active(service.is_enabled(service.TRAY_UNIT_NAME))
        self._updating = False
        self.index_row.set_subtitle(
            f"Instant, kept on this computer · {st.items:,} items" if st.fast_listing else "Read from Google Drive each time"
        )

        self.local_bar.set_value(min(st.cache_used / self.cache_max, 1))
        self.local_row.set_subtitle(
            f"{format_size(st.cache_used)} of {format_size(self.cache_max)} · recently opened files"
        )

    @staticmethod
    def _ago(moment: float) -> str:
        seconds = max(0, int(time.time() - moment))
        if seconds < 60:
            return "just now"
        return f"{seconds // 60} min ago" if seconds < 3600 else f"{seconds // 3600} h ago"

    def _refresh_quota(self) -> bool:
        if self.stack.get_visible_child_name() == "main":
            remote = self.cfg.remote
            run_in_thread(lambda: rclone.about(remote), self._show_quota)
        return GLib.SOURCE_CONTINUE

    def _show_quota(self, info: dict, error) -> None:
        if error or not info.get("total"):
            self.cloud_row.set_subtitle("Could not read the account storage")
            return
        used = info["total"] - info.get("free", 0)
        self.cloud_bar.set_value(used / info["total"])
        self.cloud_row.set_subtitle(
            f"{format_size(used)} of {format_size(info['total'])} used · includes Gmail and Photos"
        )

    # Actions

    def _on_open_folder(self, _button) -> None:
        Gtk.FileLauncher(file=Gio.File.new_for_path(str(self.cfg.folder))).launch(self, None, None)

    def _on_connected_toggled(self, row, _pspec) -> None:
        if self._updating:
            return
        name = self.cfg.name
        run_in_thread(lambda: (service.start if row.get_active() else service.stop)(name), self._on_action_done)

    def _on_startup_toggled(self, row, _pspec) -> None:
        if self._updating:
            return
        enabled, name = row.get_active(), self.cfg.name
        run_in_thread(lambda: service.set_enabled(enabled, name), self._on_action_done)

    def _on_notify_toggled(self, row, _pspec) -> None:
        if self._updating:
            return
        enabled, name = row.get_active(), self.cfg.name
        run_in_thread(lambda: service.set_notifications(enabled, name), self._on_action_done)

    def _on_tray_toggled(self, row, _pspec) -> None:
        if self._updating:
            return
        enabled = row.get_active()
        run_in_thread(lambda: service.set_tray(enabled), self._on_action_done)

    def _on_action_done(self, _result, error) -> None:
        if error:
            self.toast(f"Error: {error}")
        self._tick()

    def _on_disconnect(self, _button) -> None:
        dialog = Adw.AlertDialog(
            heading=f"Disconnect {self.cfg.name if len(self.accounts) > 1 else 'Google Drive'}?",
            body="The folder will stop showing your Drive and the files cached on this "
            "computer will be deleted. Nothing is deleted from Google Drive.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("disconnect", "Disconnect")
        dialog.set_response_appearance("disconnect", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_disconnect_response)
        dialog.present(self)

    def _on_disconnect_response(self, _dialog, response: str) -> None:
        if response != "disconnect":
            return
        cfg = self.cfg

        def work() -> None:
            service.uninstall(cfg.name)
            rclone.delete_remote(cfg.remote)
            config.remove(cfg.name)

        run_in_thread(work, self._on_disconnect_done)

    def _on_disconnect_done(self, _result, error) -> None:
        if error:
            self.toast(f"Error while disconnecting: {error}")
            return
        self.accounts = [cfg for cfg in self.accounts if cfg.name != self.cfg.name]
        self.cfg = None
        self.show_initial_page()
        self.toast("Google Drive disconnected")


class DrivuxApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        about = Gio.SimpleAction(name="about")
        about.connect("activate", self._on_about)
        self.add_action(about)

    def do_activate(self) -> None:
        window = self.get_active_window() or DrivuxWindow(self)
        window.present()

    def _on_about(self, *_args) -> None:
        Adw.AboutDialog(
            application_name="Drivux",
            application_icon="folder-remote-symbolic",
            version=__version__,
            comments="On-demand Google Drive for Linux, powered by rclone.",
            developer_name="xfr3p",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/xfr3pbr/drivux",
        ).present(self.get_active_window())


def main() -> int:
    return DrivuxApp().run(None)

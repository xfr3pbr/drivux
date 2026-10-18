"""Notificações do sistema com o que ainda está sendo sincronizado."""

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import rclone

POLL_SECONDS = 2
# Só avisa depois de N leituras seguidas com algo pendente (~4 s), para que
# salvamentos rápidos não fiquem piscando na tela.
BUSY_TICKS_BEFORE_NOTIFY = 2
MAX_LISTED = 4


@dataclass
class Activity:
    uploads: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    errors: int = 0
    last_error: str = ""


def read_activity(socket: Path) -> Activity | None:
    """Lê do rclone o que está pendente. None se a pasta não estiver montada."""
    try:
        stats = rclone.rc(socket, "core/stats")
    except rclone.RcloneError:
        return None
    try:
        uploads = [item["name"] for item in rclone.rc(socket, "vfs/queue").get("queue", [])]
    except rclone.RcloneError:
        uploads = []  # rclone antigo, sem vfs/queue: fica só com as transferências
    downloads = [t["name"] for t in stats.get("transferring", []) if t["name"] not in uploads]
    return Activity(uploads, downloads, stats.get("errors", 0), stats.get("lastError", ""))


def describe(act: Activity) -> tuple[str, str]:
    """Título e corpo da notificação de progresso."""
    names = [f"↑ {Path(n).name}" for n in act.uploads] + [f"↓ {Path(n).name}" for n in act.downloads]
    total = len(names)
    title = f"Sincronizando {total} arquivo{'s' if total > 1 else ''}"
    body = "\n".join(names[:MAX_LISTED])
    if total > MAX_LISTED:
        body += f"\ne mais {total - MAX_LISTED}"
    return title, body


class Notifier:
    """Decide quando mostrar, atualizar e fechar as notificações."""

    def __init__(self, send):
        # send(title, body, *, replaces, persistent, critical) -> id da notificação
        self.send = send
        self.progress_id = 0
        self.last_shown: tuple[str, str] | None = None
        self.busy_ticks = 0
        self.errors_seen: int | None = None

    def update(self, act: Activity | None) -> None:
        if act is None:
            self.busy_ticks = 0
            return

        if self.errors_seen is None:
            self.errors_seen = act.errors  # ignora erros de antes do Drivux abrir
        elif act.errors > self.errors_seen:
            self.errors_seen = act.errors
            self.send("Erro ao sincronizar", act.last_error or "Veja o log do Drivux.", critical=True)

        if act.uploads or act.downloads:
            self.busy_ticks += 1
            if self.busy_ticks < BUSY_TICKS_BEFORE_NOTIFY:
                return
            message = describe(act)
            if message != self.last_shown:
                self.progress_id = self.send(*message, replaces=self.progress_id, persistent=True)
                self.last_shown = message
            return

        self.busy_ticks = 0
        if self.last_shown:
            self.send("Tudo sincronizado", "Seus arquivos estão em dia com o Google Drive.", replaces=self.progress_id)
            self.progress_id = 0
            self.last_shown = None


def dbus_sender():
    """Envia notificações pelo padrão freedesktop (funciona em qualquer desktop)."""
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


def run(socket: Path) -> None:
    notifier = Notifier(dbus_sender())
    while True:
        notifier.update(read_activity(socket))
        time.sleep(POLL_SECONDS)

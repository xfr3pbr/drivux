"""Serviço systemd do usuário que monta o Google Drive sob demanda."""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import rclone
from .config import Config, cache_home, config_home, runtime_dir

UNIT_NAME = "drivux.service"
NOTIFY_UNIT_NAME = "drivux-notify.service"
SOCKET_NAME = "drivux.sock"


def unit_path(name: str = UNIT_NAME) -> Path:
    return config_home() / "systemd" / "user" / name


def socket_path() -> Path:
    return runtime_dir() / SOCKET_NAME


def cache_dir() -> Path:
    return cache_home() / "drivux"


def _quote(value: Path | str) -> str:
    """Valor entre aspas e com % escapado, como o systemd espera."""
    return '"' + str(value).replace("%", "%%") + '"'


def _which(name: str) -> str:
    """Caminho absoluto de um programa (varia entre distribuições)."""
    return shutil.which(name) or f"/usr/bin/{name}"


def render_unit(cfg: Config) -> str:
    folder = _quote(cfg.folder)
    cache = _quote(cache_dir())
    log = _quote(cache_dir() / "drivux.log")
    rclone_bin = _quote(_which("rclone"))
    return f"""[Unit]
Description=Drivux: Google Drive sob demanda em {cfg.folder}
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStartPre={_quote(_which("mkdir"))} -p {folder} {cache}
ExecStart={rclone_bin} mount {cfg.remote}: {folder} \\
  --vfs-cache-mode full \\
  --vfs-cache-max-size {cfg.cache_max_size} \\
  --vfs-cache-max-age {cfg.cache_max_age} \\
  --vfs-write-back 1s \\
  --vfs-fast-fingerprint \\
  --dir-cache-time 1000h \\
  --poll-interval 10s \\
  --attr-timeout 1h \\
  --fast-list \\
  --drive-skip-gdocs \\
  --cache-dir {cache} \\
  --rc --rc-addr unix://%t/{SOCKET_NAME} --rc-no-auth \\
  --log-file {log} --log-level NOTICE
# Carrega a árvore inteira de pastas de uma vez (só nomes, não baixa arquivos).
ExecStartPost=-{rclone_bin} rc --unix-socket %t/{SOCKET_NAME} vfs/refresh recursive=true _async=true
ExecStop={_quote(_which("fusermount3"))} -uz {folder}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def render_notify_unit(python: str = sys.executable) -> str:
    # PYTHONPATH aponta para onde o pacote está, para funcionar também
    # rodando direto do código-fonte.
    package_root = Path(__file__).resolve().parent.parent
    return f"""[Unit]
Description=Drivux: notificações do que está sincronizando
After={UNIT_NAME}

[Service]
Environment={_quote(f"PYTHONPATH={package_root}")}
ExecStart={_quote(python)} -m drivux notify
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def check_folder(folder: Path) -> None:
    """Garante que a pasta pode receber o Drive (vazia e sem nada montado)."""
    if os.path.ismount(folder):
        raise ValueError(f"{folder} já está em uso por outra montagem. Escolha outra pasta.")
    if folder.exists() and any(folder.iterdir()):
        raise ValueError(f"{folder} precisa estar vazia. Escolha outra pasta.")


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=check)


def install(cfg: Config) -> None:
    unit = unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_unit(cfg))
    unit_path(NOTIFY_UNIT_NAME).write_text(render_notify_unit())
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", UNIT_NAME, NOTIFY_UNIT_NAME)


def uninstall() -> None:
    _systemctl("disable", "--now", UNIT_NAME, NOTIFY_UNIT_NAME, check=False)
    unit_path().unlink(missing_ok=True)
    unit_path(NOTIFY_UNIT_NAME).unlink(missing_ok=True)
    _systemctl("daemon-reload")
    shutil.rmtree(cache_dir(), ignore_errors=True)


def start() -> None:
    _systemctl("start", UNIT_NAME)


def stop() -> None:
    _systemctl("stop", UNIT_NAME)


def set_enabled(enabled: bool) -> None:
    _systemctl("enable" if enabled else "disable", UNIT_NAME)


def set_notifications(enabled: bool) -> None:
    _systemctl("enable" if enabled else "disable", "--now", NOTIFY_UNIT_NAME)


def is_active(name: str = UNIT_NAME) -> bool:
    return _systemctl("is-active", name, check=False).stdout.strip() == "active"


def is_enabled(name: str = UNIT_NAME) -> bool:
    return _systemctl("is-enabled", name, check=False).stdout.strip() == "enabled"


@dataclass
class Status:
    active: bool
    enabled: bool
    notifications: bool = False
    loading_folders: bool = False
    uploading: int = 0
    queued: int = 0
    cache_used: int = 0


def status() -> Status:
    st = Status(active=is_active(), enabled=is_enabled(), notifications=is_enabled(NOTIFY_UNIT_NAME))
    if not st.active:
        return st
    sock = socket_path()
    try:
        disk = rclone.rc(sock, "vfs/stats").get("diskCache", {})
        st.uploading = disk.get("uploadsInProgress", 0)
        st.queued = disk.get("uploadsQueued", 0)
        st.cache_used = disk.get("bytesUsed", 0)
    except rclone.RcloneError:
        pass
    # O carregamento da árvore é o primeiro job assíncrono (id 1). Depois de
    # terminar ele expira e o rc responde "job not found".
    try:
        st.loading_folders = not rclone.rc(sock, "job/status", jobid=1).get("finished", True)
    except rclone.RcloneError:
        pass
    return st

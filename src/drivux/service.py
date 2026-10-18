"""systemd user services that mount Google Drive on demand."""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config, index, offline, rclone
from .config import DEFAULT_ACCOUNT, Config, cache_dir, config_home, runtime_dir, socket_path

# Unit names of the first ("default") account. The other accounts use
# drivux@NAME.service, drivux-front@NAME.service and so on (see unit_name).
UNIT_NAME = "drivux.service"
NOTIFY_UNIT_NAME = "drivux-notify.service"
FRONT_UNIT_NAME = "drivux-front.service"
OFFLINE_UNIT_NAME = "drivux-offline.service"
OFFLINE_TIMER_NAME = "drivux-offline.timer"
TRAY_UNIT_NAME = "drivux-tray.service"  # one for all accounts

_UNIT_BASES = {"folder": "drivux", "notify": "drivux-notify", "front": "drivux-front", "offline": "drivux-offline"}


def unit_name(kind: str, name: str = DEFAULT_ACCOUNT, suffix: str = "service") -> str:
    base = _UNIT_BASES[kind]
    return f"{base}.{suffix}" if name == DEFAULT_ACCOUNT else f"{base}@{name}.{suffix}"


def unit_path(name: str = UNIT_NAME) -> Path:
    return config_home() / "systemd" / "user" / name


def _account_args(name: str) -> str:
    return "" if name == DEFAULT_ACCOUNT else f" --account {name}"


def _account_names(name: str | None) -> list[str]:
    """The account asked for, or all of them."""
    if name:
        return [name]
    return [cfg.name for cfg in config.load_all()] or [DEFAULT_ACCOUNT]


def _quote(value: Path | str) -> str:
    """Quote a value and escape % the way systemd expects."""
    return '"' + str(value).replace("%", "%%") + '"'


def _which(name: str) -> str:
    """Absolute path of a program (it varies between distributions)."""
    return shutil.which(name) or f"/usr/bin/{name}"


def mount_folder(cfg: Config) -> Path:
    """Where rclone mounts the Drive. With fast listing it is hidden behind the folder the user sees."""
    if not cfg.fast_listing:
        return cfg.folder
    return runtime_dir() / "drivux" / ("raw" if cfg.name == DEFAULT_ACCOUNT else f"raw-{cfg.name}")


def render_unit(cfg: Config) -> str:
    folder = _quote(mount_folder(cfg))
    cache = _quote(cache_dir(cfg.name))
    log = _quote(cache_dir(cfg.name) / "drivux.log")
    sock = socket_path(cfg.name).name
    label = "" if cfg.name == DEFAULT_ACCOUNT else f" ({cfg.name})"
    rclone_bin = _quote(_which("rclone"))
    return f"""[Unit]
Description=Drivux{label}: on-demand Google Drive at {mount_folder(cfg)}
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
  --rc --rc-addr unix://%t/{sock} --rc-no-auth \\
  --log-file {log} --log-level NOTICE
# Load the whole folder tree at once (names only, no file downloads).
ExecStartPost=-{rclone_bin} rc --unix-socket %t/{sock} vfs/refresh recursive=true _async=true
ExecStop={_quote(_which("fusermount3"))} -uz {folder}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _drivux_command(subcommand: str, drivux_bin: str | None, python: str) -> str:
    # When installed as a package, the /usr/bin/drivux launcher exports
    # DRIVUX_BIN and the service calls it (survives distro Python upgrades).
    # When running from source, call Python with PYTHONPATH set to the package.
    drivux_bin = drivux_bin or os.environ.get("DRIVUX_BIN")
    if drivux_bin:
        return f"ExecStart={_quote(drivux_bin)} {subcommand}"
    package_root = Path(__file__).resolve().parent.parent
    return (
        f"Environment={_quote(f'PYTHONPATH={package_root}')}\n"
        f"ExecStart={_quote(python)} -m drivux {subcommand}"
    )


def render_notify_unit(drivux_bin: str | None = None, python: str = sys.executable, name: str = DEFAULT_ACCOUNT) -> str:
    exec_lines = _drivux_command("notify" + _account_args(name), drivux_bin, python)
    return f"""[Unit]
Description=Drivux ({name}): notifications about what is syncing
After={unit_name("folder", name)}

[Service]
{exec_lines}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def render_front_unit(cfg: Config, drivux_bin: str | None = None, python: str = sys.executable) -> str:
    folder = _quote(cfg.folder)
    exec_lines = _drivux_command("front" + _account_args(cfg.name), drivux_bin, python)
    return f"""[Unit]
Description=Drivux ({cfg.name}): instant listing of the Drive at {cfg.folder}
# No After=: the list does not need rclone, so the folder is ready while rclone is still starting.
Requires={unit_name("folder", cfg.name)}

[Service]
ExecStartPre={_quote(_which("mkdir"))} -p {folder}
{exec_lines}
ExecStopPost=-{_quote(_which("fusermount3"))} -uz {folder}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def render_offline_unit(drivux_bin: str | None = None, python: str = sys.executable, name: str = DEFAULT_ACCOUNT) -> str:
    exec_lines = _drivux_command("offline sync" + _account_args(name), drivux_bin, python)
    return f"""[Unit]
Description=Drivux ({name}): keep the offline files in the cache
After={unit_name("folder", name)}

[Service]
Type=oneshot
{exec_lines}
"""


def render_offline_timer(name: str = DEFAULT_ACCOUNT) -> str:
    return f"""[Unit]
Description=Drivux ({name}): refresh the offline files every day

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
"""


def render_tray_unit(drivux_bin: str | None = None, python: str = sys.executable) -> str:
    exec_lines = _drivux_command("tray", drivux_bin, python)
    return f"""[Unit]
Description=Drivux: sync status icon in the system tray
After=graphical-session.target

[Service]
{exec_lines}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical-session.target
"""


def check_folder(folder: Path) -> None:
    """Make sure the folder can host the Drive (empty, nothing mounted on it)."""
    if os.path.ismount(folder):
        raise ValueError(f"{folder} is already used by another mount. Choose another folder.")
    if folder.exists() and any(folder.iterdir()):
        raise ValueError(f"{folder} must be empty. Choose another folder.")


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=check)


def install(cfg: Config) -> None:
    name = cfg.name
    folder_unit, notify_unit, front_unit = (unit_name(kind, name) for kind in ("folder", "notify", "front"))
    unit = unit_path(folder_unit)
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_unit(cfg))
    unit_path(notify_unit).write_text(render_notify_unit(name=name))
    units = [folder_unit, notify_unit]
    if cfg.fast_listing:
        unit_path(front_unit).write_text(render_front_unit(cfg))
        units.append(front_unit)
    else:
        unit_path(front_unit).unlink(missing_ok=True)
    first_tray = not unit_path(TRAY_UNIT_NAME).exists()  # once: a later "off" by the user is kept
    if first_tray:
        unit_path(TRAY_UNIT_NAME).write_text(render_tray_unit())
        units.append(TRAY_UNIT_NAME)
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", *units)


def set_fast_listing(cfg: Config, enabled: bool) -> Config:
    """Switch the folder between rclone listing and the on-disk index. Restarts the mount."""
    _systemctl("disable", "--now", unit_name("front", cfg.name), check=False)
    _systemctl("stop", unit_name("folder", cfg.name), check=False)
    cfg.fast_listing = enabled
    check_folder(cfg.folder)  # must be empty and unmounted before it is used again
    config.save(cfg)
    install(cfg)
    return cfg


def ensure_offline_timer(name: str = DEFAULT_ACCOUNT) -> None:
    """Install the daily refresh of the offline files (first time something is kept offline)."""
    unit_path(unit_name("offline", name)).write_text(render_offline_unit(name=name))
    timer = unit_name("offline", name, "timer")
    unit_path(timer).write_text(render_offline_timer(name))
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", timer)


def uninstall(name: str = DEFAULT_ACCOUNT) -> None:
    timer = unit_name("offline", name, "timer")
    running = [unit_name(kind, name) for kind in ("front", "folder", "notify")] + [timer]  # the folder after its front
    _systemctl("disable", "--now", *running, check=False)
    for unit in running + [unit_name("offline", name)]:
        unit_path(unit).unlink(missing_ok=True)
    if not any(cfg.name != name for cfg in config.load_all()):  # last account: the tray has nothing to show
        _systemctl("disable", "--now", TRAY_UNIT_NAME, check=False)
        unit_path(TRAY_UNIT_NAME).unlink(missing_ok=True)
    offline.list_path(name).unlink(missing_ok=True)
    _systemctl("daemon-reload")
    shutil.rmtree(cache_dir(name), ignore_errors=True)


def _folder_units(name: str) -> list[str]:
    cfg = config.load(name)
    units = [unit_name("folder", name)]
    return units + [unit_name("front", name)] if cfg and cfg.fast_listing else units


def start(name: str | None = None) -> None:
    for account in _account_names(name):
        _systemctl("start", *_folder_units(account))


def stop(name: str | None = None) -> None:
    for account in _account_names(name):
        _systemctl("stop", *reversed(_folder_units(account)))


def set_enabled(enabled: bool, name: str | None = None) -> None:
    for account in _account_names(name):
        _systemctl("enable" if enabled else "disable", *_folder_units(account))


def set_notifications(enabled: bool, name: str | None = None) -> None:
    for account in _account_names(name):
        _systemctl("enable" if enabled else "disable", "--now", unit_name("notify", account))


def set_tray(enabled: bool) -> None:
    if enabled:
        unit_path(TRAY_UNIT_NAME).write_text(render_tray_unit())
        _systemctl("daemon-reload")
    _systemctl("enable" if enabled else "disable", "--now", TRAY_UNIT_NAME)


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
    fast_listing: bool = False
    name: str = DEFAULT_ACCOUNT
    last_scan: float | None = None  # when the index was last made equal to the Drive
    items: int = 0


def status(name: str = DEFAULT_ACCOUNT) -> Status:
    cfg = config.load(name)
    fast = bool(cfg and cfg.fast_listing)
    folder_unit = unit_name("folder", name)
    st = Status(
        active=is_active(folder_unit) and (not fast or is_active(unit_name("front", name))),
        enabled=is_enabled(folder_unit),
        notifications=is_enabled(unit_name("notify", name)),
        fast_listing=fast,
        name=name,
    )
    if fast and (info := index.scan_info(index.index_path(name))):
        st.last_scan, st.items = info
    if not is_active(folder_unit):
        return st
    sock = socket_path(name)
    try:
        disk = rclone.rc(sock, "vfs/stats").get("diskCache", {})
        st.uploading = disk.get("uploadsInProgress", 0)
        st.queued = disk.get("uploadsQueued", 0)
        st.cache_used = disk.get("bytesUsed", 0)
    except rclone.RcloneError:
        pass
    # Loading the tree is the first async job (id 1). Once finished it
    # expires and rc answers "job not found".
    try:
        st.loading_folders = not rclone.rc(sock, "job/status", jobid=1).get("finished", True)
    except rclone.RcloneError:
        pass
    return st

"""Drivux configuration and default (XDG) paths.

Several Google accounts can be connected. Each has a name; the first one is
called "default" and keeps the original file names, so a single-account setup
(and one created by an older version) works unchanged.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def config_path() -> Path:
    return config_home() / "drivux" / "config.json"


DEFAULT_FOLDER = Path.home() / "GoogleDrive"
DEFAULT_ACCOUNT = "default"
NOTIFY_TYPES = ("uploads", "downloads", "done", "errors", "updates")  # what a notification can be about
NAME_PATTERN = re.compile(r"[a-z0-9]{1,20}")  # no "-" or "@": they separate the name in unit and file names


def check_name(name: str) -> str:
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("The account name must have 1 to 20 lowercase letters or digits (for example: work).")
    return name


def default_folder(name: str = DEFAULT_ACCOUNT) -> Path:
    return DEFAULT_FOLDER if name == DEFAULT_ACCOUNT else DEFAULT_FOLDER.with_name(f"GoogleDrive-{name}")


def default_remote(name: str = DEFAULT_ACCOUNT) -> str:
    return "drivux" if name == DEFAULT_ACCOUNT else f"drivux-{name}"


def cache_dir(name: str = DEFAULT_ACCOUNT) -> Path:
    return cache_home() / ("drivux" if name == DEFAULT_ACCOUNT else f"drivux-{name}")


def socket_path(name: str = DEFAULT_ACCOUNT) -> Path:
    return runtime_dir() / ("drivux.sock" if name == DEFAULT_ACCOUNT else f"drivux-{name}.sock")


@dataclass
class Config:
    remote: str
    folder: Path
    cache_max_size: str = "10G"
    cache_max_age: str = "720h"
    fast_listing: bool = False  # folder listing from the on-disk index (see front.py)
    name: str = DEFAULT_ACCOUNT
    notify_types: list[str] = field(default_factory=lambda: list(NOTIFY_TYPES))  # which notifications are shown


UPDATE_MODES = ("notify", "ask", "auto")  # only tell · download and ask · install by itself


def settings_path() -> Path:
    return config_home() / "drivux" / "settings.json"


def load_settings() -> dict:
    """Preferences that belong to the app, not to an account."""
    try:
        data = json.loads(settings_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    mode = data.get("update_mode")
    return {"update_mode": mode if mode in UPDATE_MODES else "ask"}


def save_settings(settings: dict) -> None:
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(settings, indent=2) + "\n")


def _read() -> list[dict]:
    try:
        data = json.loads(config_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and "accounts" in data:
        return [item for item in data["accounts"] if isinstance(item, dict)]
    return [data] if isinstance(data, dict) and data else []  # file written before multiple accounts


def _config(data: dict) -> Config:
    return Config(**{**data, "folder": Path(data["folder"])})


def load_all() -> list[Config]:
    return [_config(item) for item in _read()]


def load(name: str | None = None) -> Config | None:
    """The account called `name`; without a name, the first one."""
    accounts = load_all()
    if name is None:
        return accounts[0] if accounts else None
    return next((cfg for cfg in accounts if cfg.name == name), None)


def _write(accounts: list[dict]) -> None:
    path = config_path()
    if not accounts:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accounts": accounts}, indent=2) + "\n")


def save(cfg: Config) -> None:
    """Add the account, or replace the one with the same name."""
    data = {**cfg.__dict__, "folder": str(cfg.folder)}
    accounts = [item for item in _read() if item.get("name", DEFAULT_ACCOUNT) != cfg.name]
    accounts.append(data)
    accounts.sort(key=lambda item: (item.get("name", DEFAULT_ACCOUNT) != DEFAULT_ACCOUNT, item.get("name", "")))
    _write(accounts)


def remove(name: str = DEFAULT_ACCOUNT) -> None:
    _write([item for item in _read() if item.get("name", DEFAULT_ACCOUNT) != name])


def account_of(path: str | Path) -> Config | None:
    """The account whose folder contains `path`."""
    absolute = Path(os.path.abspath(Path(path).expanduser()))
    for cfg in load_all():
        if absolute == cfg.folder or cfg.folder in absolute.parents:
            return cfg
    return None

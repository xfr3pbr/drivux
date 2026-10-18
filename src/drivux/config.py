"""Drivux configuration and default (XDG) paths."""

import json
import os
from dataclasses import dataclass
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


@dataclass
class Config:
    remote: str
    folder: Path
    cache_max_size: str = "10G"
    cache_max_age: str = "720h"
    fast_listing: bool = False  # folder listing from the on-disk index (see front.py)


def load() -> Config | None:
    try:
        data = json.loads(config_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    data["folder"] = Path(data["folder"])
    return Config(**data)


def save(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**cfg.__dict__, "folder": str(cfg.folder)}
    path.write_text(json.dumps(data, indent=2) + "\n")


def remove() -> None:
    config_path().unlink(missing_ok=True)

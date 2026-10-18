"""Finds a newer Drivux on the Releases page, downloads it, checks its hash and installs it.

Installing a package needs root, so it goes through pkexec (the usual password prompt of the
desktop); nothing here runs as root by itself.
"""

import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__, config

RELEASE_API = "https://api.github.com/repos/xfr3pbr/drivux/releases/latest"
CHECK_SECONDS = 6 * 3600
SUMS_NAME = "SHA256SUMS"
# package manager -> (file suffix, install command)
MANAGERS = {
    "pacman": (".pkg.tar.zst", ["pacman", "-U", "--noconfirm"]),
    "apt-get": (".deb", ["apt-get", "install", "-y"]),
    "dnf": (".rpm", ["dnf", "install", "-y"]),
    "zypper": (".rpm", ["zypper", "--non-interactive", "install", "--allow-unsigned-rpm"]),
}
SERVICES = ("drivux-front.service", "drivux-tray.service", "drivux-notify.service")


@dataclass(frozen=True)
class Release:
    version: str
    page: str
    assets: dict[str, str]  # file name -> download address


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text.split("-", 1)[0]))


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def package_manager() -> str | None:
    return next((name for name in MANAGERS if shutil.which(name)), None)


def asset_for(manager: str, release: Release) -> str | None:
    suffix = MANAGERS[manager][0]
    return next((name for name in release.assets if name.endswith(suffix)), None)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"drivux/{__version__}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def latest_release() -> Release:
    data = json.loads(_get(RELEASE_API))
    assets = {item["name"]: item["browser_download_url"] for item in data.get("assets", [])}
    return Release(data["tag_name"].lstrip("v"), data.get("html_url", ""), assets)


def update_dir() -> Path:
    return config.cache_home() / "drivux" / "update"


def _state_path() -> Path:
    return update_dir() / "state.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state))


def check(force: bool = False) -> Release | None:
    """The release to update to, or None when this one is current (or it was checked a moment ago)."""
    state = load_state()
    if not force and time.time() - state.get("checked", 0) < CHECK_SECONDS and not state.get("newer"):
        return None
    release = latest_release()
    state.update(checked=time.time(), newer=release.version if is_newer(release.version) else "")
    save_state(state)
    return release if state["newer"] else None


def checksum_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_checksum(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def download(release: Release, manager: str) -> Path:
    """Fetch the package for this distribution and check it against the published hashes."""
    name = asset_for(manager, release)
    if not name:
        raise RuntimeError(f"Release {release.version} has no package for {manager}.")
    if SUMS_NAME not in release.assets:
        raise RuntimeError(f"Release {release.version} has no {SUMS_NAME}: it will not be installed.")
    wanted = expected_checksum(_get(release.assets[SUMS_NAME]).decode(), name)
    if not wanted:
        raise RuntimeError(f"{SUMS_NAME} does not list {name}.")
    target = update_dir() / release.version / name
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(name + ".part")
    partial.write_bytes(_get(release.assets[name]))
    if checksum_of(partial) != wanted:
        partial.unlink(missing_ok=True)
        raise RuntimeError("The downloaded package does not match its hash: not installed.")
    partial.replace(target)
    return target


def install(path: Path, manager: str) -> None:
    """Install the package (the password is asked by pkexec), then restart Drivux's background parts."""
    command = ["pkexec", *MANAGERS[manager][1], str(path)]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "The installation was cancelled.")
    subprocess.run(["systemctl", "--user", "restart", *SERVICES], capture_output=True)
    state = load_state()
    state["newer"] = ""
    save_state(state)

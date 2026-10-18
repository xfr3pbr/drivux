"""Thin wrapper around the rclone binary."""

import json
import shutil
import subprocess
from pathlib import Path

REMOTE = "drivux"


class RcloneError(RuntimeError):
    pass


MIN_VERSION = (1, 60)
OFFICIAL_INSTALL = "sudo -v ; curl https://rclone.org/install.sh | sudo bash"

# Package manager -> install command. Package names (rclone, fuse3) are the
# same across the major distributions.
INSTALL_COMMANDS = {
    "apt": "sudo apt install",
    "dnf": "sudo dnf install",
    "pacman": "sudo pacman -S",
    "zypper": "sudo zypper install",
    "apk": "sudo apk add",
    "xbps-install": "sudo xbps-install",
    "emerge": "sudo emerge",
}


def version() -> tuple[int, int] | None:
    try:
        first = _run(["version"]).splitlines()[0]  # "rclone v1.75.2"
        major, minor = first.split("v", 1)[1].split(".")[:2]
        return int(major), int(minor)
    except (RcloneError, IndexError, ValueError, FileNotFoundError):
        return None


def missing_dependencies() -> list[str]:
    """System packages Drivux needs that are missing."""
    missing = []
    if not shutil.which("rclone"):
        missing.append("rclone")
    if not shutil.which("fusermount3"):
        missing.append("fuse3")
    return missing


def install_hint(packages: list[str]) -> str:
    """Command to install the packages with this distribution's package manager."""
    for manager, command in INSTALL_COMMANDS.items():
        if shutil.which(manager):
            return f"{command} {' '.join(packages)}"
    return f"Install with your distribution's package manager: {', '.join(packages)}"


def problems() -> list[str]:
    """What prevents Drivux from working on this system, as user-facing text."""
    if not shutil.which("systemctl"):
        return ["Drivux needs systemd, which this distribution does not use."]
    issues = []
    missing = missing_dependencies()
    if missing:
        issues.append(f"Install in a terminal:\n\n{install_hint(missing)}")
    elif (found := version()) and found < MIN_VERSION:
        need = ".".join(map(str, MIN_VERSION))
        issues.append(
            f"This distribution's rclone is too old (v{found[0]}.{found[1]}); "
            f"Drivux needs v{need} or newer. Install the official version:\n\n{OFFICIAL_INSTALL}"
        )
    return issues


def _run(args: list[str], timeout: float | None = None) -> str:
    try:
        proc = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:  # a mount busy loading its folders can leave rc unanswered
        raise RcloneError(f"rclone {args[0]} did not answer in {timeout:g} s") from None
    if proc.returncode != 0:
        lines = [line for line in proc.stderr.splitlines() if "client_id" not in line]
        raise RcloneError("\n".join(lines).strip() or f"rclone {args[0]} failed")
    return proc.stdout


def has_remote(name: str = REMOTE) -> bool:
    return f"{name}:" in _run(["listremotes"]).split()


def create_remote(name: str = REMOTE, client_id: str = "", client_secret: str = "") -> None:
    """Open the browser for Google sign-in and wait for authorization."""
    args = ["config", "create", name, "drive", "scope=drive"]
    if client_id:
        args += [f"client_id={client_id}", f"client_secret={client_secret}"]
    _run(args, timeout=600)


def delete_remote(name: str = REMOTE) -> None:
    _run(["config", "delete", name])


def about(name: str = REMOTE) -> dict:
    """Account storage: total, used, free, trashed, other (in bytes)."""
    return json.loads(_run(["about", f"{name}:", "--json"], timeout=60))


def rc(socket: Path, command: str, **params) -> dict:
    """Call the rclone mount remote control over the local socket."""
    args = ["rc", "--unix-socket", str(socket), command, *(f"{k}={v}" for k, v in params.items())]
    return json.loads(_run(args, timeout=10) or "{}")


def list_tree(name: str = REMOTE) -> list[dict]:
    """Every file and folder of the Drive with its ID, size and date (Docs and Sheets excluded)."""
    return json.loads(_run(["lsjson", "-R", "--fast-list", "--drive-skip-gdocs", f"{name}:"], timeout=1800))

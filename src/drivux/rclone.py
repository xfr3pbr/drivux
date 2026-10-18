"""Wrapper fino em volta do binário rclone."""

import json
import shutil
import subprocess
from pathlib import Path

REMOTE = "drivux"


class RcloneError(RuntimeError):
    pass


MIN_VERSION = (1, 60)
OFFICIAL_INSTALL = "sudo -v ; curl https://rclone.org/install.sh | sudo bash"

# Gerenciador de pacotes -> comando de instalação. Os nomes dos pacotes
# (rclone, fuse3) são os mesmos nas principais distribuições.
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
    """Pacotes do sistema que faltam para o Drivux funcionar."""
    missing = []
    if not shutil.which("rclone"):
        missing.append("rclone")
    if not shutil.which("fusermount3"):
        missing.append("fuse3")
    return missing


def install_hint(packages: list[str]) -> str:
    """Comando para instalar os pacotes no gerenciador desta distribuição."""
    for manager, command in INSTALL_COMMANDS.items():
        if shutil.which(manager):
            return f"{command} {' '.join(packages)}"
    return f"Instale pelo gerenciador de pacotes da sua distribuição: {', '.join(packages)}"


def problems() -> list[str]:
    """O que impede o Drivux de funcionar neste sistema, em texto para o usuário."""
    if not shutil.which("systemctl"):
        return ["O Drivux precisa do systemd, que esta distribuição não usa."]
    issues = []
    missing = missing_dependencies()
    if missing:
        issues.append(f"Instale no terminal:\n\n{install_hint(missing)}")
    elif (found := version()) and found < MIN_VERSION:
        need = ".".join(map(str, MIN_VERSION))
        issues.append(
            f"O rclone desta distribuição é antigo (v{found[0]}.{found[1]}); "
            f"o Drivux precisa da v{need} ou mais nova. Instale a versão oficial:\n\n{OFFICIAL_INSTALL}"
        )
    return issues


def _run(args: list[str], timeout: float | None = None) -> str:
    proc = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        lines = [line for line in proc.stderr.splitlines() if "client_id" not in line]
        raise RcloneError("\n".join(lines).strip() or f"rclone {args[0]} falhou")
    return proc.stdout


def has_remote(name: str = REMOTE) -> bool:
    return f"{name}:" in _run(["listremotes"]).split()


def create_remote(name: str = REMOTE, client_id: str = "", client_secret: str = "") -> None:
    """Abre o navegador para o login no Google e espera a autorização."""
    args = ["config", "create", name, "drive", "scope=drive"]
    if client_id:
        args += [f"client_id={client_id}", f"client_secret={client_secret}"]
    _run(args, timeout=600)


def delete_remote(name: str = REMOTE) -> None:
    _run(["config", "delete", name])


def about(name: str = REMOTE) -> dict:
    """Espaço da conta: total, used, free, trashed, other (em bytes)."""
    return json.loads(_run(["about", f"{name}:", "--json"], timeout=60))


def rc(socket: Path, command: str, **params) -> dict:
    """Chama o controle remoto do rclone mount pelo socket local."""
    args = ["rc", "--unix-socket", str(socket), command, *(f"{k}={v}" for k, v in params.items())]
    return json.loads(_run(args, timeout=10) or "{}")

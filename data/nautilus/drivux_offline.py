"""Nautilus right-click menu for Drivux: Keep offline / Remove offline copy.

Needs nautilus-python. It only reads Drivux's two JSON files and calls the
`drivux offline` command, so it does not depend on where Drivux's code lives.
"""

import json
import os
import shutil
from pathlib import Path

from gi import require_version

require_version("Nautilus", "4.1")

from gi.repository import Gio, GObject, Nautilus


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "drivux"


def _drive_folder() -> Path | None:
    try:
        return Path(json.loads((_config_dir() / "config.json").read_text())["folder"])
    except (OSError, ValueError, KeyError):
        return None


def _kept() -> list[str]:
    try:
        return [item for item in json.loads((_config_dir() / "offline.json").read_text()) if isinstance(item, str)]
    except (OSError, ValueError):
        return []


def _is_kept(kept: list[str], rel: str) -> bool:
    return any(rel == item or rel.startswith(item + "/") for item in kept)


class DrivuxOfflineAction(GObject.GObject, Nautilus.MenuProvider):
    def _relative_paths(self, files, folder: Path) -> list[tuple[str, str]]:
        """(absolute path, path inside the Drive folder) of each selected item."""
        paths = []
        for file in files:
            location = file.get_location()
            path = location.get_path() if location else None
            if not path:
                return []
            try:
                rel = Path(path).relative_to(folder).as_posix()
            except ValueError:
                return []
            if rel == ".":
                return []
            paths.append((path, rel))
        return paths

    def _run(self, _menu, action: str, paths: list[str]) -> None:
        drivux = shutil.which("drivux")
        if drivux:
            Gio.Subprocess.new([drivux, "offline", action, *paths], Gio.SubprocessFlags.NONE)

    def get_file_items(self, *args):
        # nautilus-python passes (files) or (window, files) depending on the version.
        files = args[0] if len(args) == 1 else args[1]
        folder = _drive_folder()
        if not folder or not files:
            return []
        selected = self._relative_paths(files, folder)
        if not selected:
            return []

        kept = _kept()
        if all(_is_kept(kept, rel) for _, rel in selected):
            label, action = "Remove offline copy", "remove"
            # a file kept through its folder can only be removed together with the folder
            if any(rel not in kept for _, rel in selected):
                return []
        else:
            label, action = "Keep offline", "add"

        item = Nautilus.MenuItem(name=f"Drivux::offline_{action}", label=label, icon="folder-download-symbolic")
        item.connect("activate", self._run, action, [path for path, _ in selected])
        return [item]

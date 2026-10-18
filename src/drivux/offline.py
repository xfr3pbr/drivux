"""Files and folders kept available offline.

rclone has no "pin" for its cache: a file stays only while it is used, up to
--vfs-cache-max-age. Keeping a file offline means (1) reading it through the
mount so it is downloaded now, and (2) reading it again periodically so it is
never "unused for too long" (`drivux offline sync`, run by a daily timer).
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import DEFAULT_ACCOUNT, config_home

CHUNK = 1024 * 1024


def list_path(name: str = DEFAULT_ACCOUNT) -> Path:
    return config_home() / "drivux" / ("offline.json" if name == DEFAULT_ACCOUNT else f"offline-{name}.json")


def load(name: str = DEFAULT_ACCOUNT) -> list[str]:
    """Paths kept offline, relative to the Drive folder of the account."""
    try:
        data = json.loads(list_path(name).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, str)]


def save(items: list[str], name: str = DEFAULT_ACCOUNT) -> None:
    path = list_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(items), indent=2) + "\n")


def relative(folder: Path, path: str | Path) -> str:
    """Path inside the Drive folder, relative to it (without following symlinks)."""
    absolute = Path(os.path.abspath(Path(path).expanduser()))
    try:
        rel = absolute.relative_to(folder).as_posix()
    except ValueError:
        raise ValueError(f"{absolute} is not inside the Drive folder ({folder}).") from None
    if rel == ".":
        raise ValueError("Choose a file or folder inside the Drive folder, not the folder itself.")
    return rel


def covering(items: list[str], rel: str) -> str | None:
    """The entry that keeps `rel` offline: itself or a folder above it."""
    for item in items:
        if rel == item or rel.startswith(item + "/"):
            return item
    return None


def add(items: list[str], rels: list[str]) -> list[str]:
    """New list with `rels` added. Entries inside an added folder are dropped."""
    result = list(items)
    for rel in rels:
        if covering(result, rel):
            continue
        result = [item for item in result if not item.startswith(rel + "/")]
        result.append(rel)
    return result


def remove(items: list[str], rels: list[str]) -> list[str]:
    """New list without `rels`. A file kept through its folder cannot be removed alone."""
    result = list(items)
    for rel in rels:
        owner = covering(result, rel)
        if owner is None:
            continue
        if owner != rel:
            raise ValueError(f"{rel} is kept offline because {owner} is. Remove {owner} instead.")
        result.remove(rel)
    return result


@dataclass
class Result:
    files: int = 0
    size: int = 0
    failed: list[str] = field(default_factory=list)


def _files_under(target: Path):
    if target.is_dir():
        yield from (p for p in sorted(target.rglob("*")) if p.is_file())
    elif target.is_file():
        yield target


def _read_all(path: Path) -> int:
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            size += len(chunk)
    return size


def fetch(folder: Path, rels: list[str]) -> Result:
    """Read every file so it is in the cache. A file already cached is read from disk."""
    result = Result()
    for rel in rels:
        target = folder / rel
        if not target.exists():
            result.failed.append(f"{rel}: not found")
            continue
        for path in _files_under(target):
            try:
                result.size += _read_all(path)
                result.files += 1
            except OSError as exc:
                result.failed.append(f"{path.relative_to(folder).as_posix()}: {exc.strerror or exc}")
    return result

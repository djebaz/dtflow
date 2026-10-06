"""Bounded disposable-cache inspection and purge operations.

Only <state>/cache categories are owned here. Durable outputs, attempts, queues, settings, presets, credentials, assets,
catalogue metadata and retained test evidence are outside this boundary and are never traversed or removed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = ("inventory", "thumbnails", "derived")


@dataclass(frozen=True, slots=True)
class CacheCategory:
    name: str
    files: int
    bytes: int


def _category(state: Path, name: str) -> Path:
    if name not in CATEGORIES:
        raise ValueError("Unknown disposable cache category.")
    return Path(state) / "cache" / name


def cache_usage(state: Path) -> tuple[CacheCategory, ...]:
    result = []
    for name in CATEGORIES:
        root = _category(state, name)
        files = size = 0
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    try:
                        files += 1
                        size += path.stat().st_size
                    except OSError:
                        continue
        result.append(CacheCategory(name, files, size))
    return tuple(result)


def purge_cache(state: Path, categories: tuple[str, ...]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(categories))
    for name in selected:
        root = _category(state, name)
        if root.exists():
            shutil.rmtree(root)
    cache_root = Path(state) / "cache"
    try:
        cache_root.rmdir()
    except OSError:
        pass
    return selected

"""Bounded disposable-cache inspection/purge; durable data is never owned here."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dtflow.persistence.cache_coordination import (
    CacheCleanupError,
    CacheSafetyError,
    cache_access,
    category_path,
    check_path,
)

CATEGORIES = ("inventory", "thumbnails", "derived")
MAX_CACHE_ENTRIES = 100_000


@dataclass(frozen=True, slots=True)
class CacheCategory:
    name: str
    files: int
    bytes: int


def _tree(root: Path) -> list[Path]:
    """Preflight without following symlinks or Windows reparse points."""
    check_path(root)
    if not root.exists():
        return []
    if not root.is_dir():
        raise CacheSafetyError("Cache category must be a directory.")
    paths, pending = [], [root]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)
                check_path(path)
                paths.append(path)
                if len(paths) > MAX_CACHE_ENTRIES:
                    raise CacheSafetyError("Cache inspection exceeds 100,000 entries per category.")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return paths


def cache_usage(state: Path) -> tuple[CacheCategory, ...]:
    with cache_access(state):
        result = []
        for name in CATEGORIES:
            files = size = 0
            for path in _tree(category_path(state, name)):
                if path.is_file():
                    files += 1
                    size += path.stat().st_size
            result.append(CacheCategory(name, files, size))
        return tuple(result)


def purge_cache(state: Path, categories: tuple[str, ...]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(categories))
    with cache_access(state):
        roots = [(name, category_path(state, name)) for name in selected]
        # Validate every selection before deleting anything, including nested redirects.
        for _, root in roots:
            _tree(root)
        completed = []
        for name, root in roots:
            try:
                if root.exists():
                    shutil.rmtree(root)
                completed.append(name)
            except OSError as exc:
                done = ", ".join(completed) or "none"
                raise CacheCleanupError(
                    f"Cache cleanup stopped at {name}; completed categories: {done}. Partial cleanup may have occurred; refresh sizes and retry."
                ) from exc
        try:
            (Path(state).resolve() / "cache").rmdir()
        except OSError:
            pass
        return selected

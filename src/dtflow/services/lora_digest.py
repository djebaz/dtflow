"""Background sha256 of a LoRA file, remembered by (size, mtime) so Prepare never reads the bytes twice."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path

CHUNK = 1024 * 1024
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[int, int, str]] = {}  # resolved path -> (size, mtime_ns, sha256)
_MAX_ENTRIES = 256


class DigestCancelled(Exception):
    """The caller stopped the read (editor closed or replaced)."""


def _key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path))


def cached_digest(path: Path, stat: os.stat_result) -> str | None:
    """The remembered digest when the file's size and mtime still match, else ``None``."""
    with _LOCK:
        hit = _CACHE.get(_key(path))
    if hit is not None and hit[0] == stat.st_size and hit[1] == stat.st_mtime_ns:
        return hit[2]
    return None


def remember(path: Path, stat: os.stat_result, digest: str) -> None:
    with _LOCK:
        if len(_CACHE) >= _MAX_ENTRIES:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[_key(path)] = (stat.st_size, stat.st_mtime_ns, digest)


def hash_file(
    path: Path, cancelled: Callable[[], bool] | None = None, on_progress: Callable[[int], None] | None = None
) -> tuple[str, int]:
    """Return ``(sha256, size)``, remembering them; raises ``DigestCancelled`` between chunks.

    Raises ``OSError`` when unreadable and ``ValueError`` when empty or changed while read.
    """
    path = Path(path)
    before = path.stat()
    if not path.is_file() or before.st_size < 1:
        raise ValueError("not a non-empty regular file")
    known = cached_digest(path, before)
    if known is not None:
        return known, before.st_size
    digest, total = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            if cancelled is not None and cancelled():
                raise DigestCancelled
            digest.update(chunk)
            total += len(chunk)
            if on_progress is not None:
                on_progress(total)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or total != before.st_size:
        raise ValueError("file changed while it was hashed")
    remember(path, before, digest.hexdigest())
    return digest.hexdigest(), total

"""Background sha256 of a LoRA file, remembered by (size, mtime) so Prepare never reads the bytes twice."""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

CHUNK = 1024 * 1024
_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[int, int, str, str]] = OrderedDict()  # path -> (size, mtime_ns, edge, sha256)
_MAX_ENTRIES = 4096  # least recently used leaves first; a large batch never evicts its own rows
EDGE = 64 * 1024


class DigestCancelled(Exception):
    """The caller stopped the read (editor closed or replaced)."""


def _key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path))


def _edge(path: Path, size: int) -> str:
    """Digest of the first and last 64 KB: catches a same-size replacement whose modification time was kept."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(EDGE))
        if size > EDGE:
            handle.seek(max(size - EDGE, EDGE))
            digest.update(handle.read(EDGE))
    return digest.hexdigest()[:16]


def cached_digest(path: Path, stat: os.stat_result) -> str | None:
    """The remembered digest when size, mtime and the file's first/last 64 KB still match, else ``None``."""
    key = _key(path)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
    if hit is None or hit[0] != stat.st_size or hit[1] != stat.st_mtime_ns:
        return None
    try:
        return hit[3] if _edge(path, stat.st_size) == hit[2] else None
    except OSError:
        return None


def remember(path: Path, stat: os.stat_result, digest: str) -> None:
    try:
        edge = _edge(path, stat.st_size)
    except OSError:
        return
    key = _key(path)
    with _LOCK:
        _CACHE[key] = (stat.st_size, stat.st_mtime_ns, edge, digest)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_ENTRIES:
            _CACHE.popitem(last=False)


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

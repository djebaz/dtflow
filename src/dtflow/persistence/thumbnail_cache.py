"""Disposable on-disk gallery thumbnails under ``<state>/cache/thumbnails``.

Entries are keyed by the saved output identity plus the source file's size and
modification time, so a replaced, removed or restored original never reuses a
stale thumbnail. The cache only stores bytes; encoding/decoding stays in the Qt
layer. Every failure is a miss: callers fall back to decoding the original.
"""

import hashlib
import os
import shutil
import tempfile
import threading
from pathlib import Path

from dtflow.persistence.cache_coordination import (
    CacheBusyError,
    CacheSafetyError,
    cache_access,
    category_path,
    check_path,
)

VERSION = 1
SUFFIX = ".webp"  # small, as fast to decode as JPEG here, and keeps transparency
_enabled = True


def configure(*, no_cache: bool) -> None:
    """``-nc/--no-cache`` bypasses thumbnail cache reads and writes for the run."""
    global _enabled
    _enabled = not no_cache


def entry_path(state: Path, identity: str, size: int, mtime_ns: int, side: int) -> Path | None:
    if not _enabled:
        return None
    digest = hashlib.sha256(f"{VERSION}\0{identity}\0{size}\0{mtime_ns}\0{side}".encode()).hexdigest()
    try:
        return category_path(state, "thumbnails") / f"{digest}{SUFFIX}"
    except (OSError, ValueError):
        return None


def read(state: Path, path: Path) -> bytes | None:
    """Return the cached bytes, or None on a miss, a busy cache or any error."""
    try:
        with cache_access(state, timeout=0.05):
            check_path(path)
            return path.read_bytes()
    except (CacheBusyError, CacheSafetyError, OSError):
        return None


def store(state: Path, path: Path, write) -> bool:
    """Encode with ``write(scratch_path) -> bool`` outside the cache lease, then publish atomically.

    Encoding happens in the system temporary directory so the shared cache lease
    is held only for a small copy and rename; a busy cache skips the write.
    """
    descriptor, name = tempfile.mkstemp(prefix="dtflow-thumb-", suffix=SUFFIX)
    os.close(descriptor)
    scratch = Path(name)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        if not write(scratch):
            return False
        with cache_access(state, timeout=0.05):
            path.parent.mkdir(parents=True, exist_ok=True)
            check_path(path.parent)
            check_path(path)
            try:
                shutil.copyfile(scratch, temporary)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return True
    except (CacheBusyError, CacheSafetyError, OSError):
        return False
    finally:
        scratch.unlink(missing_ok=True)


def discard(state: Path, path: Path) -> None:
    """Drop an unreadable entry so the next view re-creates it."""
    try:
        with cache_access(state, timeout=0.05):
            check_path(path)
            path.unlink()
    except (CacheBusyError, CacheSafetyError, OSError):
        pass

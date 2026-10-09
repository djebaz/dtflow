"""Nonblocking cache coordination, independent of durable dispatch ownership."""

import os
import stat
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

_registry_guard = threading.Lock()
_locks = {}
_local = threading.local()


class CacheBusyError(OSError):
    """Another cache operation owns the shared coordination boundary."""


class CacheSafetyError(OSError):
    """A disposable cache path redirects or exceeds the traversal budget."""


class CacheCleanupError(OSError):
    """Cleanup stopped after potentially removing part of a category."""


def check_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise CacheSafetyError("Cache contains a redirected path; cleanup/access refused.")


def category_path(state: Path, category: str) -> Path:
    if category not in ("inventory", "thumbnails", "derived"):
        raise ValueError("Unknown disposable cache category.")
    root = Path(state).resolve() / "cache"
    check_path(root)
    target = root / category
    check_path(target)
    return target


@contextmanager
def cache_access(state: Path):
    """Serialize cache transactions across threads/processes; never wait on the GUI."""
    root = Path(state).resolve()
    key = os.path.normcase(str(root))
    with _registry_guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(blocking=False):
        raise CacheBusyError("Cache is busy; wait for active cache operations and try again.")
    depths = getattr(_local, "depths", None)
    if depths is None:
        depths = _local.depths = {}
    handle = None
    try:
        if not depths.get(key):
            root.mkdir(parents=True, exist_ok=True)
            lock_path = root / "cache.lock"
            check_path(lock_path)
            handle = lock_path.open("a+b")
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    if handle.read(1) == b"":
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CacheBusyError("Cache is busy; wait for active cache operations and try again.") from exc
        depths[key] = depths.get(key, 0) + 1
        try:
            yield
        finally:
            depths[key] -= 1
            if not depths[key]:
                del depths[key]
    finally:
        try:
            if handle is not None:
                handle.close()
        finally:
            lock.release()


def cache_io(category: str):
    """Coordinate a synchronous persistence function whose first argument is state."""

    def decorate(function):
        @wraps(function)
        def guarded(state, *args, **kwargs):
            with cache_access(state):
                category_path(state, category)
                return function(state, *args, **kwargs)

        return guarded

    return decorate

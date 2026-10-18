"""Disposable on-disk gallery thumbnails under ``<state>/cache/thumbnails``.

Entries live at ``<shard>/<identity-hash>.<fingerprint-hash>.jpg|webp``: the shard and
identity hash come from the saved output identity, the fingerprint hash from the
original's size, modification time and file ID, so a replaced original never
reuses a stale thumbnail and every entry for one output can be found without
listing the whole cache. The cache only stores bytes; encoding/decoding stays in
the Qt layer. Every failure is a miss: callers fall back to the original.

Reads take no cache lease: entries are published with an atomic rename, a
vanished entry is an ordinary miss, and lease-free reads keep thumbnails from
contending with export, size checks and cleanup. Writes, eviction and discards
take the shared lease only around the short copy/rename/unlink steps.
"""

import hashlib
import os
import queue
import shutil
import tempfile
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from dtflow.persistence.cache_coordination import (
    CacheBusyError,
    CacheSafetyError,
    cache_access,
    category_path,
    check_path,
)

VERSION = 2
SUFFIXES = (".jpg", ".webp")
MAX_ENTRIES = 20_000  # entry-count bound, not a byte budget; below cleanup's 100,000-entry cap
PRUNE_TO = 16_000  # oldest-written entries are evicted down to this count
PRUNE_EVERY = 500  # writes between eviction checks, plus one check per writer start
PENDING_WRITES = 256  # bounded backlog; a full queue drops the write, the next view retries
_enabled = True
_format = "jpg"
_writes: queue.Queue = queue.Queue(PENDING_WRITES)
_writer: threading.Thread | None = None
_writer_guard = threading.Lock()


def configure(*, no_cache: bool, thumbnail_format: str = "jpg") -> None:
    """``-nc/--no-cache`` bypasses thumbnail cache reads and writes for the run."""
    global _enabled, _format
    if thumbnail_format not in ("jpg", "webp"):
        raise ValueError("Thumbnail format must be jpg or webp.")
    _enabled = not no_cache
    _format = thumbnail_format


def _identity_hash(identity: str) -> str:
    return hashlib.sha256(f"{VERSION}\0{identity}".encode()).hexdigest()[:32]


def _folder(state: Path, identity_hash: str) -> Path:
    return category_path(state, "thumbnails") / identity_hash[:2]


def entry_path(state: Path, identity: str, info: os.stat_result, side: int) -> Path | None:
    """Return the entry for this output and original fingerprint, or None with ``--no-cache``."""
    if not _enabled:
        return None
    fingerprint = f"{info.st_size}\0{info.st_mtime_ns}\0{info.st_ino}\0{side}\0{_format}"
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
    owner = _identity_hash(identity)
    try:
        path = _folder(state, owner) / f"{owner}.{digest}.{_format}"
        # JPEG cannot preserve alpha. Its key may hold an explicitly WebP-encoded
        # transparency fallback; do not reuse the separate WebP-comparison key.
        if _format == "jpg" and not path.exists() and path.with_suffix(".webp").exists():
            return path.with_suffix(".webp")
        return path
    except (CacheSafetyError, OSError, ValueError):
        return None


def read(path: Path) -> bytes | None:
    """Return the cached bytes, or None on a miss, a redirected path or any error."""
    try:
        check_path(path.parent)
        check_path(path)
        return path.read_bytes()
    except (CacheSafetyError, OSError):
        return None


def store(state: Path, path: Path, write) -> bool:
    """Encode with ``write(scratch_path) -> bool`` outside the cache, then publish atomically.

    Older entries for the same output (a replaced original) are removed in the
    same step, so outdated thumbnails do not accumulate.
    """
    descriptor, name = tempfile.mkstemp(prefix="dtflow-thumb-", suffix=path.suffix)
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
            owner = path.name.split(".", 1)[0]
            for stale in path.parent.glob(f"{owner}.*"):
                if stale != path and stale.suffix in SUFFIXES:
                    check_path(stale)
                    stale.unlink(missing_ok=True)
        return True
    except (CacheBusyError, CacheSafetyError, OSError):
        return False
    finally:
        scratch.unlink(missing_ok=True)


def store_later(state: Path, path: Path, write, done=None) -> bool:
    """Queue ``store`` on one background daemon writer so the caller returns immediately.

    One writer keeps encodes from competing with thumbnail decodes.
    ``done(stored: bool, elapsed_s: float)`` runs on the writer. Unfinished
    writes are abandoned at exit; entries are published atomically, so an
    abandoned write leaves no partial entry.
    """
    global _writer
    try:
        _writes.put_nowait((state, path, write, done))
    except queue.Full:
        return False
    with _writer_guard:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_drain, name="dtflow-thumbnail-cache", daemon=True)
            _writer.start()
    return True


def _drain() -> None:
    written = 0
    checked: set[str] = set()
    while True:
        state, path, write, done = _writes.get()
        started = time.monotonic()
        stored = store(state, path, write)
        if done is not None:
            try:
                done(stored, time.monotonic() - started)
            except Exception:  # noqa: BLE001 - diagnostics must never stop the writer
                pass
        written += stored
        key = os.path.normcase(str(state))
        if stored and (key not in checked or written % PRUNE_EVERY == 0):
            checked.add(key)
            prune(state)


def prune(state: Path, *, limit: int = MAX_ENTRIES, keep: int = PRUNE_TO) -> int:
    """Evict the oldest-written entries once the cache exceeds ``limit``; return how many."""
    try:
        root = category_path(state, "thumbnails")
        entries = []
        with os.scandir(root) as shards:
            for shard in shards:
                if not shard.is_dir(follow_symlinks=False):
                    continue
                with os.scandir(shard.path) as files:
                    for entry in files:
                        if entry.name.endswith(SUFFIXES) and entry.is_file(follow_symlinks=False):
                            entries.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.path))
    except (CacheSafetyError, OSError, ValueError):
        return 0
    if len(entries) <= limit:
        return 0
    entries.sort()
    removed = 0
    victims = [Path(path) for _, path in entries[: len(entries) - keep]]
    for start in range(0, len(victims), 200):
        try:
            with cache_access(state, timeout=0.05):
                for victim in victims[start : start + 200]:
                    check_path(victim)
                    victim.unlink(missing_ok=True)
                    removed += 1
        except (CacheBusyError, CacheSafetyError, OSError):
            break
    return removed


def discard(state: Path, path: Path) -> None:
    """Drop an unreadable entry so the next view re-creates it."""
    try:
        with cache_access(state, timeout=0.05):
            check_path(path)
            path.unlink()
    except (CacheBusyError, CacheSafetyError, OSError):
        pass


def forget(state: Path, identities: Iterable[str]) -> int:
    """Remove every cached thumbnail of removed outputs; best effort, return how many.

    Runs regardless of ``--no-cache`` so a removal never leaves an earlier
    run's preview behind.
    """
    removed = 0
    try:
        with cache_access(state, timeout=2.0):
            for identity in identities:
                owner = _identity_hash(identity)
                folder = _folder(state, owner)
                if not folder.is_dir():
                    continue
                for entry in folder.glob(f"{owner}.*"):
                    if entry.suffix not in SUFFIXES:
                        continue
                    check_path(entry)
                    entry.unlink(missing_ok=True)
                    removed += 1
    except (CacheBusyError, CacheSafetyError, OSError, ValueError):
        pass
    return removed

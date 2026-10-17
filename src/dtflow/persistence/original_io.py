"""Cross-process advisory ownership for DTFlow-managed original-image operations.

The lock is independent of disposable cache ownership. All participating exports
and recycle operations take this lock before reading/mutating committed originals.
It is deliberately exclusive and is held off the Qt thread.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

log = logging.getLogger(__name__)
# Only report waits worth noticing; an uncontended lease is per-image routine.
_SLOW_LEASE_SECONDS = 0.5
# The first Windows-boundary lease starts the long-lived helper (~1 s on WSL1),
# so only waits well beyond that startup cost indicate a real competing owner.
_SLOW_WINDOWS_LEASE_SECONDS = 5.0


class OriginalImagesBusyError(RuntimeError):
    """The shared original-image operation lease could not be acquired."""


class OriginalImagesUnavailableError(RuntimeError):
    """Required Windows/WSL original-image locking support is unavailable."""


def windows_originals_bridge(state: Path):
    """Check static bridge prerequisites without starting any helper or taking a lock."""
    if os.name == "nt":
        return None
    from dtflow.persistence.windows_original import WindowsBridgeError, bridge

    try:
        return bridge(Path(state))
    except (OSError, UnicodeError, ValueError) as exc:
        log.debug(
            "original-image readiness refused reason=%s",
            exc.reason if isinstance(exc, WindowsBridgeError) else type(exc).__name__,
        )
        if isinstance(exc, WindowsBridgeError):
            reason = {
                "interpreter": "This checkout needs a Windows .venv/Scripts/python.exe for shared originals.",
                "mapping": "The Windows/WSL drive mapping is unavailable or ambiguous.",
                "redirect": "Redirected shared Windows paths are unsupported.",
                "mount": "The Windows-drive mount has no verified shared-lock support.",
            }[exc.reason]
        else:
            reason = "The required Windows original-file bridge is unavailable."
        raise OriginalImagesUnavailableError(
            f"{reason} Use a WSL Linux (ext4) state directory or restore the required Windows bridge."
        ) from exc


def require_originals_ready(state: Path) -> None:
    """Cheap static pre-dispatch check; never acquire a lock or spawn a process."""
    windows_originals_bridge(state)


@contextmanager
def _file_lock(state: Path, filename: str, *, timeout: float):
    """Acquire an OS-released file lock; never delete the lock file during cleanup."""
    if os.name != "nt":
        from dtflow.persistence.windows_original import shared_lock

        # Capability checks are static. Actual I/O still requires a real lease.
        connection = windows_originals_bridge(state)
        if connection is not None:
            command, native_state = connection
            started = time.monotonic()
            with ExitStack() as stack:
                try:
                    stack.enter_context(shared_lock(command, native_state, filename, timeout))
                except (OSError, UnicodeError, ValueError) as exc:
                    log.debug(
                        "original-file lease refused lease=%s boundary=windows reason=%s waited_ms=%d",
                        filename.removesuffix(".lock"),
                        type(exc).__name__,
                        (time.monotonic() - started) * 1000,
                    )
                    raise OriginalImagesBusyError("Shared Windows original-file lease could not be acquired.") from exc
                _log_wait(filename, "windows", started)
                yield
            return
    directory = Path(state).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / filename).open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
        else:
            import fcntl

        started = time.monotonic()
        deadline = started + timeout
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    log.debug(
                        "original-file lease timed out lease=%s boundary=native waited_ms=%d",
                        filename.removesuffix(".lock"),
                        (time.monotonic() - started) * 1000,
                    )
                    raise OriginalImagesBusyError(
                        "Original images are busy; wait for active export to finish."
                    ) from exc
                time.sleep(0.05)
        _log_wait(filename, "native", started)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _log_wait(filename: str, boundary: str, started: float) -> None:
    waited = time.monotonic() - started
    if waited >= (_SLOW_WINDOWS_LEASE_SECONDS if boundary == "windows" else _SLOW_LEASE_SECONDS):
        log.debug(
            "original-file lease contended lease=%s boundary=%s waited_ms=%d",
            filename.removesuffix(".lock"),
            boundary,
            waited * 1000,
        )


# Foreground lease requests (saves, removal, export) waiting in this process.
# Background display reads stand aside for them so a page of thumbnails
# can never starve a generation save into save_failed.
_foreground_waiting = 0
_foreground_guard = threading.Lock()


@contextmanager
def original_images_lock(state: Path, *, timeout: float = 5.0, background: bool = False):
    """Serialize real original-file reads and writes across participating processes.

    ``background`` reads (thumbnails) yield to foreground requests of this process.
    """
    global _foreground_waiting
    if background:
        deadline = time.monotonic() + timeout
        while _foreground_waiting:
            if time.monotonic() >= deadline:
                raise OriginalImagesBusyError("Original images are busy with a save or removal.")
            time.sleep(0.02)
        with _file_lock(state, "original_images.lock", timeout=max(0.0, deadline - time.monotonic())):
            yield
        return
    with _foreground_guard:
        _foreground_waiting += 1
    try:
        stack = ExitStack()
        stack.enter_context(_file_lock(state, "original_images.lock", timeout=timeout))
    finally:
        with _foreground_guard:
            _foreground_waiting -= 1
    with stack:
        yield


@contextmanager
def recycle_batch_lock(state: Path, *, timeout: float = 5.0):
    """Serialize recycle operations without monopolizing generation saves per batch."""
    with _file_lock(state, "image_recycle_operation.lock", timeout=timeout):
        yield

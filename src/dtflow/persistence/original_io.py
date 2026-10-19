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

from dtflow.diagnostics import event as diagnostic_event

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
        diagnostic_event(
            log,
            "originals.readiness_refused",
            level=logging.DEBUG,
            data={
                "reason": exc.reason if isinstance(exc, WindowsBridgeError) else type(exc).__name__,
            },
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
                    diagnostic_event(
                        log,
                        "originals.lease_refused",
                        level=logging.DEBUG,
                        data={
                            "lease": filename.removesuffix(".lock"),
                            "reason": type(exc).__name__,
                            "waited_ms": (time.monotonic() - started) * 1000,
                            "boundary": "windows",
                        },
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
                    diagnostic_event(
                        log,
                        "originals.lease_timed_out",
                        level=logging.DEBUG,
                        data={
                            "lease": filename.removesuffix(".lock"),
                            "waited_ms": (time.monotonic() - started) * 1000,
                            "boundary": "native",
                        },
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
        diagnostic_event(
            log,
            "originals.lease_contended",
            level=logging.DEBUG,
            data={
                "lease": filename.removesuffix(".lock"),
                "boundary": boundary,
                "waited_ms": waited * 1000,
            },
        )


class _ReadGroup:
    """Display reads of one process share a single original-file lease.

    The first background reader takes the cross-process lease and the last one
    releases it; readers never queue behind each other. A foreground request
    (save, removal, export, user action) stops new readers from joining, then
    takes the lease itself once the current readers finish, so a page of
    thumbnails can never starve a generation save into save_failed. Other
    processes cannot signal the group, so it also stops admitting readers
    after a bounded hold and pauses before taking the lease again.
    """

    def __init__(self):
        self.cond = threading.Condition()
        self.readers = 0
        self.writers_waiting = 0
        self.acquiring = False
        self.stack: ExitStack | None = None
        self.held_since = 0.0
        self.draining = False
        self.resume_at = 0.0


# Longest stretch display reads keep the lease from other processes, and the
# pause before retaking it (other processes poll the lock every 50 ms).
_GROUP_HOLD_SECONDS = 2.0
_GROUP_PAUSE_SECONDS = 0.1
_groups: dict[str, _ReadGroup] = {}
_groups_guard = threading.Lock()


def _group(state: Path) -> _ReadGroup:
    with _groups_guard:
        return _groups.setdefault(str(Path(state).resolve()), _ReadGroup())


@contextmanager
def _shared_read(state: Path, timeout: float):
    group = _group(state)
    deadline = time.monotonic() + timeout
    with group.cond:
        while True:
            now = time.monotonic()
            if group.readers and now - group.held_since >= _GROUP_HOLD_SECONDS:
                group.draining = True
            blocked = group.writers_waiting or group.acquiring or group.draining
            if not blocked and not group.readers and now < group.resume_at:
                blocked = True
            if not blocked:
                break
            remaining = deadline - now
            if remaining <= 0:
                raise OriginalImagesBusyError("Original images are busy with a save or removal.")
            # Wake for the pause end even without a notification.
            group.cond.wait(min(remaining, max(group.resume_at - now, 0.02)))
        joined = group.readers > 0
        if joined:
            group.readers += 1
        else:
            group.acquiring = True
    if not joined:
        stack = ExitStack()
        try:
            stack.enter_context(
                _file_lock(state, "original_images.lock", timeout=max(0.0, deadline - time.monotonic()))
            )
        except BaseException:
            with group.cond:
                group.acquiring = False
                group.cond.notify_all()
            raise
        with group.cond:
            group.stack, group.acquiring, group.readers = stack, False, 1
            group.held_since = time.monotonic()
            group.cond.notify_all()
    try:
        yield
    finally:
        with group.cond:
            group.readers -= 1
            release = group.stack if group.readers == 0 else None
            if release is not None:
                group.stack = None
                if group.draining:
                    group.draining = False
                    group.resume_at = time.monotonic() + _GROUP_PAUSE_SECONDS
            group.cond.notify_all()
        if release is not None:
            release.close()


@contextmanager
def original_images_lock(state: Path, *, timeout: float = 5.0, background: bool = False):
    """Serialize real original-file reads and writes across participating processes.

    ``background`` display reads of this process share one lease and yield to
    foreground requests; foreground holders are exclusive as before.
    """
    if background:
        with _shared_read(state, timeout):
            yield
        return
    group = _group(state)
    with group.cond:
        group.writers_waiting += 1
    try:
        stack = ExitStack()
        stack.enter_context(_file_lock(state, "original_images.lock", timeout=timeout))
    finally:
        with group.cond:
            group.writers_waiting -= 1
            group.cond.notify_all()
    with stack:
        yield


@contextmanager
def recycle_batch_lock(state: Path, *, timeout: float = 5.0):
    """Serialize recycle operations without monopolizing generation saves per batch."""
    with _file_lock(state, "image_recycle_operation.lock", timeout=timeout):
        yield

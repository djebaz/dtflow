"""Cross-process advisory ownership for DTFlow-managed original-image operations.

The lock is independent of disposable cache ownership. All participating exports
and recycle operations take this lock before reading/mutating committed originals.
It is deliberately exclusive and is held off the Qt thread.
"""

from __future__ import annotations

import os
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path


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
            with ExitStack() as stack:
                try:
                    stack.enter_context(shared_lock(command, native_state, filename, timeout))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise OriginalImagesBusyError("Shared Windows original-file lease could not be acquired.") from exc
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

        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise OriginalImagesBusyError(
                        "Original images are busy; wait for active export to finish."
                    ) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def original_images_lock(state: Path, *, timeout: float = 5.0):
    """Serialize real original-file reads and writes across participating processes."""
    with _file_lock(state, "original_images.lock", timeout=timeout):
        yield


@contextmanager
def recycle_batch_lock(state: Path, *, timeout: float = 5.0):
    """Serialize recycle operations without monopolizing generation saves per batch."""
    with _file_lock(state, "image_recycle_operation.lock", timeout=timeout):
        yield

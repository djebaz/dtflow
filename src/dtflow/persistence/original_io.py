"""Cross-process advisory ownership for DTFlow-managed original-image operations.

The lock is independent of disposable cache ownership. All participating exports
and recycle operations take this lock before reading/mutating committed originals.
It is deliberately exclusive and is held off the Qt thread.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


class OriginalImagesBusyError(RuntimeError):
    """The shared original-image operation lease could not be acquired."""


@contextmanager
def original_images_lock(state: Path, *, timeout: float = 5.0):
    """Acquire an OS-released file lock; never delete the lock file during cleanup."""
    directory = Path(state).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "original_images.lock").open("a+b") as handle:
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
                    raise OriginalImagesBusyError("Original images are busy; wait for active export to finish.") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

"""Small file leases for asset ownership, independent of SQLite transactions."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dtflow.persistence.store import PersistenceError

_CAPACITY = {"conversion": threading.BoundedSemaphore(2), "prepare": threading.BoundedSemaphore(3)}


@asynccontextmanager
async def capacity(kind: str, *, waiting=None, cancel=None, timeout: float = 300.0):
    """Bound CPU/upload work across event loops without occupying worker threads."""
    slots = _CAPACITY[kind]
    deadline = time.monotonic() + timeout
    while not slots.acquire(blocking=False):
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        if waiting is not None:
            waiting()
            waiting = None
        if time.monotonic() >= deadline:
            raise AssetsBusyError(f"Waiting for {kind} capacity timed out. Retry when current work finishes.")
        await asyncio.sleep(0.1)
    try:
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        yield
    finally:
        slots.release()


class AssetsBusyError(PersistenceError):
    """A bounded asset wait expired; the caller may retry explicitly."""


def lock_file(path: Path):
    handle = path.open("a+b")
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
        handle.close()
        raise AssetsBusyError("This asset is still in use. Wait for it to finish, then Retry.") from exc
    return handle


def resource_path(state: Path, resource: str) -> Path:
    directory = Path(state) / "asset_leases"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (hashlib.sha256(resource.encode()).hexdigest() + ".lock")


def wait_lock(path: Path, *, timeout: float = 30.0):
    """Bounded blocking wait for short metadata work, called only off Qt."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return lock_file(path)
        except AssetsBusyError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


@asynccontextmanager
async def resource_lease(state: Path, resource: str, *, waiting=None, cancel=None, timeout: float = 300.0):
    """Wait cancellably outside transactions; lease files are never unlinked."""
    path = resource_path(state, resource)
    deadline = time.monotonic() + timeout
    reported = False
    while True:
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        try:
            handle = lock_file(path)
            break
        except AssetsBusyError:
            if not reported and waiting is not None:
                waiting()
                reported = True
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.1)
    try:
        yield
    finally:
        handle.close()

"""Small, worker-only ownership boundary for disposable Prepare conversion jobs."""

from __future__ import annotations

import logging
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from dtflow.diagnostics import event

log = logging.getLogger(__name__)
JOB_NAME = re.compile(r"job-[0-9a-f]{32}\Z")
MAX_ENTRIES = 128
MAX_JOBS = 16
MAX_FILES = 32
MAX_BYTES = 8 * 1024**3
MAX_SECONDS = 0.5


def _checked(path: Path):
    """Reject redirects in the path or any ancestor, including Windows junctions."""
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("Conversion staging is redirected; access refused.")
    return path.lstat()


def _roots(directory: Path) -> tuple[Path, Path]:
    directory = directory.absolute()
    # Inspect existing ancestors before mkdir can follow a redirected parent.
    for part in reversed((directory, *directory.parents)):
        if os.path.lexists(part):
            _checked(part)
    directory.mkdir(parents=True, exist_ok=True)
    root = directory / ".convert"
    owners = root / ".owners"
    for part in (root, owners):
        if os.path.lexists(part):
            _checked(part)
        part.mkdir(exist_ok=True)
        if not stat.S_ISDIR(_checked(part).st_mode):
            raise OSError("Conversion staging is not a directory.")
    return root, owners


def _owner(path: Path, *, create: bool = False):
    """Stable lock files are never removed; unknown ownership is not inactivity."""
    _checked(path.parent)
    if not create:
        named = _checked(path)
        if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1 or named.st_size != 1:
            raise OSError("Unknown conversion ownership metadata.")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    handle = os.fdopen(os.open(path, flags, 0o600), "r+b", buffering=0)
    try:
        named, opened = _checked(path), os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OSError("Conversion ownership is not a regular file.")
        if create:
            handle.write(b"0")
        elif opened.st_size != 1:
            raise OSError("Unknown conversion ownership metadata.")
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise


def _remove(folder: Path, *, byte_limit: int, counts: dict | None = None) -> tuple[int, bool]:
    """Preflight a small flat job before unlinking; never recursively traverse it."""
    before = _checked(folder)
    if not stat.S_ISDIR(before.st_mode):
        raise OSError("Conversion job is not a directory.")
    files = []
    size = 0
    with os.scandir(folder) as entries:
        for entry in entries:
            if len(files) >= MAX_FILES:
                return 0, False
            path = folder / entry.name
            info = _checked(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Conversion job contains unexpected entries.")
            size += info.st_size
            if size > byte_limit:
                return 0, False
            files.append((path, info))
    for path, info in files:
        current = _checked(folder)
        observed = _checked(path)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
        ) != (info.st_dev, info.st_ino, info.st_size):
            raise OSError("Conversion staging changed during cleanup.")
        path.unlink()
        if counts is not None:
            counts["bytes_removed"] += info.st_size
    current = _checked(folder)
    if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
        raise OSError("Conversion staging changed during cleanup.")
    folder.rmdir()
    return size, True


def cleanup_staging(directory: Path) -> None:
    """Best-effort bounded sweep; a busy/missing owner never authorizes removal."""
    started = time.monotonic()
    counts = {"examined": 0, "removed": 0, "bytes_removed": 0, "skipped": 0, "errors": 0, "bounded": False}
    try:
        root, owners = _roots(directory)
        jobs = 0
        with os.scandir(root) as entries:
            for entry in entries:
                if counts["examined"] >= MAX_ENTRIES or jobs >= MAX_JOBS or time.monotonic() - started >= MAX_SECONDS:
                    counts["bounded"] = True
                    break
                counts["examined"] += 1
                if entry.name == ".owners":
                    continue
                if not JOB_NAME.fullmatch(entry.name):
                    counts["skipped"] += 1
                    continue
                jobs += 1
                try:
                    handle = _owner(owners / f"{entry.name}.owner")
                except OSError:
                    counts["skipped"] += 1
                    continue
                try:
                    _, removed = _remove(
                        root / entry.name, byte_limit=MAX_BYTES - counts["bytes_removed"], counts=counts
                    )
                    counts["removed"] += int(removed)
                    counts["bounded"] |= not removed
                except OSError:
                    counts["errors"] += 1
                finally:
                    handle.close()
    except OSError:
        counts["errors"] += 1
    event(log, "lora_convert.staging_cleanup", level=logging.WARNING if counts["errors"] else logging.INFO, data=counts)


@contextmanager
def conversion_job(directory: Path):
    """Publish only an already-owned job; retain ownership through final cleanup."""
    root, owners = _roots(directory)
    name = f"job-{uuid.uuid4().hex}"
    handle = _owner(owners / f"{name}.owner", create=True)
    folder = root / name
    created = False
    try:
        folder.mkdir()
        created = True
        yield folder
    finally:
        try:
            if created:
                _, removed = _remove(folder, byte_limit=MAX_BYTES)
                if not removed:
                    event(log, "lora_convert.staging_cleanup_pending", level=logging.WARNING, data={"reason": "budget"})
        except OSError:
            event(log, "lora_convert.staging_cleanup_pending", level=logging.WARNING, data={"reason": "io_or_safety"})
        finally:
            handle.close()

"""Nonblocking session ownership for safe diagnostic retention; no Qt/state imports."""

import os
import stat


def ownership(path, *, create=False):
    """Return a locked handle, or raise OSError; never remove stable lock files.

    Unknown ownership (including sessions predating this policy) is not proof
    of inactivity. Lock files are tiny coordination metadata, not evidence.
    """
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        info = os.fstat(handle.fileno())
        named = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or getattr(named, "st_file_attributes", 0) & 0x400
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OSError("Diagnostic ownership is redirected or unavailable.")
        if os.name == "nt":
            import msvcrt

            if create:
                handle.write(b"0")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise

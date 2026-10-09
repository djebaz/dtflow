"""Qt-only system Trash adapter, injected into the backend-neutral image service.

Create QFile instances in the calling worker. Never permanently delete, retry a
failed trash operation, or expose Qt error strings / trash receipt paths in logs.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from PySide6.QtCore import QFile, QStorageInfo

from dtflow.services.image_recycle import RecycleUnavailable

TRASH_NAME = "Recycle Bin" if os.name == "nt" else "Trash"
# Unknown, network and FUSE mounts fail closed. Extend only with owner acceptance.
_LOCAL_POSIX_FILESYSTEMS = {b"ext2", b"ext3", b"ext4", b"btrfs", b"xfs", b"f2fs", b"zfs", b"apfs", b"hfs"}


def trash_supported() -> bool:
    """OS capability only; individual files may still be ineligible or fail."""
    return sys.platform in ("win32", "linux", "darwin") and QFile.supportsMoveToTrash()


def check_trash_source(path: Path) -> None:
    """Preserve local-volume restrictions without changing managed-root policy."""
    if not trash_supported():
        raise RecycleUnavailable("System Trash is unavailable.", stage="trash_unsupported")
    source = Path(path)
    if not source.is_absolute() or not stat.S_ISREG(source.lstat().st_mode):
        raise RecycleUnavailable("The original is not a regular local file.", stage="source_not_regular")
    if os.name == "nt":
        # Retain the existing fixed-drive restriction; this is not a deletion API.
        import ctypes

        get_drive_type = ctypes.WinDLL("kernel32").GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        if not source.anchor or get_drive_type(source.anchor) != 3:
            raise RecycleUnavailable("The original is not on a local fixed drive.", stage="source_not_fixed_drive")
    else:
        storage = QStorageInfo(str(source))
        if not storage.isValid() or not storage.isReady():
            raise RecycleUnavailable("The original volume is unavailable.", stage="source_volume_unavailable")
        if bytes(storage.fileSystemType()).lower() not in _LOCAL_POSIX_FILESYSTEMS:
            raise RecycleUnavailable(
                "The original filesystem is not approved for trashing.", stage="source_filesystem_unsupported"
            )


def recycle_file(path: Path) -> None:
    """Move one validated original to system Trash, requiring a file receipt."""
    source = Path(path)
    check_trash_source(source)
    before = source.stat()
    file = QFile(str(source))
    if not file.moveToTrash():
        # Qt's errorString can include private paths. Report only its numeric code.
        raise RecycleUnavailable("System Trash operation failed.", stage=f"qt_trash_error_{file.error().value}")
    if source.exists():
        raise RecycleUnavailable("The original remains at its source location.", stage="source_still_present")
    receipt_name = file.fileName()
    if not receipt_name:
        raise RecycleUnavailable("System Trash did not supply a verifiable receipt.", stage="trash_receipt_missing")
    receipt = Path(receipt_name)
    if not receipt.is_absolute():
        raise RecycleUnavailable("System Trash supplied an invalid receipt.", stage="trash_receipt_invalid")
    info = receipt.lstat()
    if not stat.S_ISREG(info.st_mode) or (info.st_size, info.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise RecycleUnavailable("System Trash receipt could not be verified.", stage="trash_receipt_unverified")

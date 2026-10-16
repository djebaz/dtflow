"""Qt-only system Trash adapter, injected into the backend-neutral image service.

Create QFile instances in the calling worker. Never permanently delete, retry a
failed trash operation, or expose Qt error strings / trash receipt paths in logs.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from PySide6.QtCore import QFile

from dtflow.services.image_recycle import RecycleUnavailable

TRASH_NAME = "Recycle Bin" if os.name == "nt" else "Trash"


def trash_supported() -> bool:
    """OS capability only; individual files may still be ineligible or fail."""
    supports = getattr(QFile, "supportsMoveToTrash", None)
    return bool(supports()) if callable(supports) else False


def check_trash_source(path: Path) -> None:
    """Check Qt capability and regular-file shape; managed ownership stays in the service."""
    if not trash_supported():
        raise RecycleUnavailable("System Trash is unavailable.", stage="trash_unsupported")
    source = Path(path)
    if not source.is_absolute() or not stat.S_ISREG(source.lstat().st_mode):
        raise RecycleUnavailable("The original is not a regular file.", stage="source_not_regular")


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
    try:
        info = receipt.lstat()
    except OSError as exc:
        raise RecycleUnavailable(
            "Qt reported success, but the Trash destination could not be verified.",
            stage="trash_receipt_unavailable",
        ) from exc
    if not stat.S_ISREG(info.st_mode) or (info.st_size, info.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise RecycleUnavailable("System Trash receipt could not be verified.", stage="trash_receipt_unverified")

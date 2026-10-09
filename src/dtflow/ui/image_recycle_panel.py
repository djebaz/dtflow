"""Qt presentation for confirmed, durable Windows Recycle Bin operations."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from dtflow.core.gallery import GalleryQuery
from dtflow.persistence.original_io import OriginalImagesBusyError
from dtflow.services.image_recycle import prepare_recycle, recycle_outputs
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)


class ImageRecyclePanel(QObject):
    """Confirm checked originals on the GUI thread and run Shell operations off-thread."""

    progress = Signal(int, int)

    def __init__(self, parent: QWidget, *, runner: AsyncRunner, state_dir: Path, gallery, busy_source):
        super().__init__(parent)
        self.parent_widget = parent
        self.runner = runner
        self.state_dir = Path(state_dir)
        self.gallery = gallery
        self.busy_source = busy_source
        self._running = False
        self._stop = threading.Event()
        self._dialog: QProgressDialog | None = None
        self.progress.connect(self._update_progress, Qt.ConnectionType.QueuedConnection)

    @property
    def running(self) -> bool:
        return self._running

    def request(self, identities: tuple[str, ...], hidden: int, query: GalleryQuery) -> None:
        """Freeze scope before confirmation; refuse concurrent destructive actions."""
        if self._running or not identities:
            return
        if self.busy_source():
            QMessageBox.information(
                self.parent_widget,
                "Images are busy",
                "Wait for generation, queue dispatch or image export to finish before moving originals.",
            )
            return
        self._running = True
        self.gallery.set_recycle_busy(True)
        self.runner.submit(
            asyncio.to_thread(prepare_recycle, self.state_dir, identities, hidden, query),
            self._prepared,
        )

    def _prepared(self, plan, error) -> None:
        if error is not None:
            self._finish_with_error(error)
            return
        if self.busy_source():
            self._finish_with_error(OriginalImagesBusyError("An original-image operation started. Try again later."))
            return
        eligible = len(plan.targets)
        if not eligible:
            self._running = False
            self.gallery.set_recycle_busy(False)
            QMessageBox.information(
                self.parent_widget,
                "Nothing to recycle",
                f"No eligible managed original images. {plan.missing} missing; "
                f"{plan.ineligible} ineligible or require manual review.",
            )
            return
        message = (
            f"Move {eligible} selected original images to Windows Recycle Bin?\n\n"
            f"{plan.hidden} checked images are hidden by the current filters and included "
            f"in this request. {plan.missing} originals are missing and "
            f"{plan.ineligible} are ineligible; these will not be moved.\n\n"
            "Windows normally lets you restore recycled files from Recycle Bin. "
            "DTFlow does not provide Undo and cannot guarantee restoration. "
            "Exported copies and generation history will be preserved."
        )
        box = QMessageBox(self.parent_widget)
        box.setWindowTitle("Move images to Recycle Bin")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(message)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        move = box.addButton(f"Move {eligible} images to Recycle Bin", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is not move:
            self._running = False
            self.gallery.set_recycle_busy(False)
            return
        # Recheck confirmed records and fingerprints under the original-file
        # lease in the worker; preflight is not authorization to skip revalidation.
        self._stop = threading.Event()
        self._dialog = QProgressDialog(
            "Moving eligible originals to Windows Recycle Bin…",
            "Stop after current image",
            0,
            eligible,
            self.parent_widget,
        )
        self._dialog.setWindowTitle("Image recycling")
        self._dialog.setMinimumDuration(0)
        self._dialog.setWindowModality(Qt.WindowModality.NonModal)
        self._dialog.canceled.connect(self._stop.set)
        self._dialog.show()
        self.runner.submit(
            asyncio.to_thread(
                recycle_outputs,
                self.state_dir,
                plan,
                self._stop,
                self.progress.emit,
            ),
            self._completed,
        )

    def _update_progress(self, done: int, total: int) -> None:
        if self._dialog is not None:
            self._dialog.setMaximum(total)
            self._dialog.setValue(done)

    def _completed(self, result, error) -> None:
        if self._dialog is not None:
            self._dialog.blockSignals(True)
            self._dialog.close()
            self._dialog = None
        self._running = False
        self.gallery.set_recycle_busy(False)
        if error is not None:
            self._finish_with_error(error)
            self.gallery.refresh(preserve=True)
            return
        self.gallery.recycle_completed(result.recycled)
        summary = (
            f"{len(result.recycled)} moved to Windows Recycle Bin.\n"
            f"{len(result.failed)} failed; {len(result.uncertain)} uncertain (manual review required).\n"
            f"{result.missing} missing; {result.ineligible} ineligible; "
            f"{result.unprocessed} unprocessed."
        )
        if result.failure_details:
            # Display only stable output IDs and sanitized operation stages.
            # Shell/OS exception messages may contain private paths.
            summary += "\n\nFailure details:"
            for identity, reason in result.failure_details[:5]:
                summary += f"\n{identity}: {reason}"
            if len(result.failure_details) > 5:
                summary += f"\n… and {len(result.failure_details) - 5} more (see DTFlow logs)."
        if result.canceled:
            summary += "\nStopped after the current image; completed moves remain."
        if result.failed or result.uncertain:
            QMessageBox.warning(self.parent_widget, "Recycle Bin result", summary)
        else:
            QMessageBox.information(self.parent_widget, "Recycle Bin result", summary)

    def _finish_with_error(self, error) -> None:
        self._running = False
        self.gallery.set_recycle_busy(False)
        log.warning("image recycling failed reason=%s", type(error).__name__)
        if isinstance(error, OriginalImagesBusyError):
            message = str(error)
        else:
            message = (
                "The operation could not be completed or its durable outcome could not be verified. "
                "Inspect the original files and retry only after checking their status."
            )
        QMessageBox.warning(self.parent_widget, "Recycle Bin unavailable", message)

    def cancel(self) -> None:
        """Request a safe stop between files without undoing successful moves."""
        self._stop.set()

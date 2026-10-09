"""Qt presentation for confirmed, durable system Trash operations."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from dtflow.core.gallery import GalleryQuery
from dtflow.persistence.original_io import OriginalImagesBusyError
from dtflow.services.image_recycle import prepare_recycle, recycle_outputs
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import InlineNotice, confirm_action, feedback
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.system_trash import TRASH_NAME, check_trash_source, recycle_file

log = logging.getLogger(__name__)


class ImageRecyclePanel(QObject):
    """Confirm checked originals on the GUI thread and run system Trash operations off-thread."""

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
        self._confirmation_revision = None
        self.progress.connect(self._update_progress, Qt.ConnectionType.QueuedConnection)

    @property
    def running(self) -> bool:
        return self._running

    def request(self, identities: tuple[str, ...], hidden: int, query: GalleryQuery) -> None:
        """Freeze scope before confirmation; refuse concurrent destructive actions."""
        if feedback(self.parent_widget).commit(self.gallery.delete_button):
            return
        if self._running or not identities:
            return
        if self.busy_source():
            InlineNotice.information(
                self.parent_widget,
                "Images are busy",
                "Wait for generation, queue dispatch or image export to finish before moving originals.",
            )
            return
        self._confirmation_revision = self.gallery.recycle_scope_revision
        self._running = True
        self.gallery.set_recycle_busy(True)
        self.runner.submit(
            asyncio.to_thread(
                prepare_recycle, self.state_dir, identities, hidden, query, trash_check=check_trash_source
            ),
            self._prepared,
        )

    def _prepared(self, plan, error) -> None:
        if error is not None:
            self._finish_with_error(error)
            return
        if self.busy_source():
            self._finish_with_error(OriginalImagesBusyError("An original-image operation started. Try again later."))
            return
        if self._confirmation_revision != self.gallery.recycle_scope_revision:
            self._cancel_confirmation()
            return
        eligible = len(plan.targets)
        if not eligible:
            self._running = False
            self.gallery.set_recycle_busy(False)
            InlineNotice.information(
                self.parent_widget,
                "Nothing to recycle",
                f"No eligible managed original images. {plan.missing} missing; "
                f"{plan.ineligible} ineligible or require manual review.",
            )
            return
        message = (
            f"Move {eligible} selected original images to {TRASH_NAME}?\n\n"
            f"{plan.hidden} checked images are hidden by the current filters and included "
            f"in this request. {plan.missing} originals are missing and "
            f"{plan.ineligible} are ineligible; these will not be moved.\n\n"
            f"Files can normally be recovered manually from your system {TRASH_NAME}. "
            "DTFlow does not provide Undo and cannot guarantee restoration. "
            "Recovered files stay hidden in DTFlow until content-verified recovery is supported. "
            "Exported copies and generation history will be preserved."
        )
        # Keep scope editable while awaiting consent, but invalidate it on any
        # selection/filter/navigation change. The destructive worker still revalidates.
        self._running = False
        self.gallery.set_recycle_busy(False)
        confirm_action(
            self.parent_widget,
            self.gallery.delete_button,
            f"Move images to {TRASH_NAME}",
            message,
            lambda: self._execute(plan),
            self._cancel_confirmation,
        )

    def _cancel_confirmation(self):
        self._confirmation_revision = None
        self._running = False
        self.gallery.set_recycle_busy(False)

    def _execute(self, plan):
        if self.busy_source() or self._confirmation_revision != self.gallery.recycle_scope_revision:
            self._cancel_confirmation()
            InlineNotice.warning(
                self.parent_widget, "Images changed or busy", "Review the checked scope and try again."
            )
            return
        self._running = True
        self.gallery.set_recycle_busy(True)
        self._stop = threading.Event()
        panel = feedback(self.parent_widget)
        panel.show("Image recycling", f"Moving eligible originals to {TRASH_NAME}…")
        activity_strip(self.parent_widget).start("trash", "Image recycling", self.cancel, total=len(plan.targets))
        self.runner.submit(
            asyncio.to_thread(
                recycle_outputs,
                self.state_dir,
                plan,
                self._stop,
                self.progress.emit,
                recycle_adapter=recycle_file,
            ),
            self._completed,
        )

    def _update_progress(self, done: int, total: int) -> None:
        activity_strip(self.parent_widget).update("trash", done, total)

    def _completed(self, result, error) -> None:
        activity_strip(self.parent_widget).finish("trash")
        self._running = False
        self.gallery.set_recycle_busy(False)
        if error is not None:
            self._finish_with_error(error)
            self.gallery.refresh(preserve=True)
            return
        self.gallery.recycle_completed(result.recycled)
        summary = (
            f"{len(result.recycled)} confirmed moved to {TRASH_NAME}.\n"
            f"{len(result.failed)} failed; {len(result.uncertain)} uncertain (manual review required).\n"
            f"{result.missing} missing; {result.ineligible} ineligible; "
            f"{result.unprocessed} unprocessed."
        )
        if result.failure_details:
            # Display only stable output IDs and sanitized operation stages.
            # Shell/OS exception messages may contain private paths.
            summary += "\n\nFailure / uncertainty details:"
            for identity, reason in result.failure_details[:5]:
                if reason.startswith("trash_receipt_"):
                    detail = (
                        f"Qt reported a successful move to {TRASH_NAME}, but the destination could not "
                        "be verified. Inspect system Trash; do not retry automatically."
                    )
                else:
                    detail = reason
                summary += f"\n{identity}: {detail}"
            if len(result.failure_details) > 5:
                summary += f"\n… and {len(result.failure_details) - 5} more (see DTFlow logs)."
        if result.canceled:
            summary += "\nStopped after the current image; completed moves remain."
        if result.failed or result.uncertain:
            InlineNotice.warning(self.parent_widget, f"{TRASH_NAME} result", summary)
        else:
            InlineNotice.information(self.parent_widget, f"{TRASH_NAME} result", summary)

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
        InlineNotice.warning(self.parent_widget, f"{TRASH_NAME} unavailable", message)

    def cancel(self) -> None:
        """Request a safe stop between files without undoing successful moves."""
        self._stop.set()

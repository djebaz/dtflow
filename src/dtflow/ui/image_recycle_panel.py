"""Qt presentation for explicit, confirmed Trash or permanent removal."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from dtflow.core.gallery import GalleryQuery
from dtflow.persistence.image_removal_preferences import load_mode, save_mode
from dtflow.persistence.original_io import OriginalImagesBusyError, OriginalImagesUnavailableError
from dtflow.services.image_recycle import RecycleUnavailable, prepare_recycle, recycle_outputs
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import InlineNotice, confirm_action, feedback
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.system_trash import TRASH_NAME, check_trash_source, recycle_file

log = logging.getLogger(__name__)


class ImageRecyclePanel(QObject):
    """Keep mode/consent on the GUI thread and removal off-thread."""

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
        self._action = "trash"
        self._phase = "idle"
        gallery._permanent_callback = self.request
        gallery.removal_ready = False
        self.menu_button = gallery._widget("galleryRemovalMenuButton", QPushButton)
        menu = QMenu(self.menu_button)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._mode_actions = {}
        for mode, label in (("trash", f"Move to {TRASH_NAME}"), ("permanent", "Delete permanently")):
            if mode == "trash" and gallery._recycle_callback is None:
                label += " (unavailable)"
            action = menu.addAction(label)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, mode=mode: self._choose_mode(mode))
            self._mode_actions[mode] = action
        menu.aboutToShow.connect(lambda: feedback(self.parent_widget).dismiss())
        self.menu_button.setMenu(menu)
        gallery._sync_selection_ui()
        self.progress.connect(self._update_progress, Qt.ConnectionType.QueuedConnection)
        self.runner.submit(asyncio.to_thread(load_mode, self.state_dir), self._mode_loaded)

    def _mode_loaded(self, mode, error):
        self.gallery.removal_ready = True
        self._apply_mode(mode if error is None else "trash")
        if error is not None:
            InlineNotice.warning(self.parent_widget, "Removal preferences unavailable", str(error))

    def _apply_mode(self, mode):
        feedback(self.parent_widget).dismiss()
        self.gallery.recycle_scope_revision += 1
        self._confirmation_revision = None
        self.gallery.removal_mode = mode
        label = "Delete permanently" if mode == "permanent" else f"Move to {TRASH_NAME}"
        button = self.gallery.delete_button
        button.setText(label)
        button.setAccessibleName(label)
        button.setToolTip(
            "Irreversibly delete checked managed originals after explicit confirmation; no Undo."
            if mode == "permanent"
            else f"Move eligible checked originals to {TRASH_NAME} after confirmation."
        )
        button.setMinimumWidth(button.fontMetrics().horizontalAdvance("Delete permanently") + 42)
        for key, action in self._mode_actions.items():
            action.setChecked(key == mode)
        self.gallery._sync_selection_ui()

    def _choose_mode(self, mode):
        if self._running or not self.gallery.removal_ready:
            return
        feedback(self.parent_widget).dismiss()
        self.gallery.recycle_scope_revision += 1
        self._confirmation_revision = None
        self.gallery.removal_ready = False
        self.gallery._sync_selection_ui()

        def saved(_result, error):
            self.gallery.removal_ready = True
            if error is None:
                self._apply_mode(mode)
            else:
                self._apply_mode(self.gallery.removal_mode)
                InlineNotice.warning(self.parent_widget, "Removal mode not saved", str(error))

        self.runner.submit(asyncio.to_thread(save_mode, self.state_dir, mode), saved)

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
                "Wait for generation, queue dispatch or image export to finish before removing originals.",
            )
            return
        self._confirmation_revision = self.gallery.recycle_scope_revision
        action = self.gallery.removal_mode
        self._action = action
        if action == "trash" and self.gallery._recycle_callback is None:
            return
        self._running = True
        self._phase = "preflight"
        self.gallery.set_recycle_busy(True)
        self.runner.submit(
            asyncio.to_thread(
                prepare_recycle,
                self.state_dir,
                identities,
                hidden,
                query,
                trash_check=check_trash_source,
                action=action,
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
        if (
            self._confirmation_revision != self.gallery.recycle_scope_revision
            or plan.action != self.gallery.removal_mode
        ):
            self._cancel_confirmation()
            return
        eligible = len(plan.targets)
        if not eligible:
            self._running = False
            self.gallery.set_recycle_busy(False)
            InlineNotice.information(
                self.parent_widget,
                "Nothing to remove",
                f"No eligible managed original images. {plan.missing} missing; "
                f"{plan.ineligible} ineligible or require manual review.",
            )
            return
        permanent = plan.action == "permanent"
        message = (
            f"{eligible} eligible · {plan.hidden} checked images hidden by filters included · "
            f"{plan.missing} missing · {plan.ineligible} ineligible (untouched). "
        )
        message += (
            "PERMANENT AND IRREVERSIBLE: originals will be destroyed, bypassing Trash/Recycle Bin. "
            "No Undo or recovery guarantee. Metadata, exports and generation history are preserved."
            if permanent
            else f"Restore manually through {TRASH_NAME}; no DTFlow Undo or restoration guarantee. "
            "Recovered files stay hidden pending content-verified recovery. Exports and generation history preserved."
        )
        # Keep scope editable while awaiting consent, but invalidate it on any
        # selection/filter/navigation change. The destructive worker still revalidates.
        self._running = False
        self.gallery.set_recycle_busy(False)
        confirm_action(
            self.parent_widget,
            self.gallery.delete_button,
            f"Permanently delete {eligible} selected originals?"
            if permanent
            else f"Move {eligible} selected originals to {TRASH_NAME}?",
            message,
            lambda: self._execute(plan),
            self._cancel_confirmation,
            danger=True,
            confirm_text="Delete permanently" if permanent else "Confirm",
        )

    def _cancel_confirmation(self):
        self._confirmation_revision = None
        self._running = False
        self.gallery.set_recycle_busy(False)

    def _execute(self, plan):
        if (
            self.busy_source()
            or self._confirmation_revision != self.gallery.recycle_scope_revision
            or plan.action != self.gallery.removal_mode
        ):
            self._cancel_confirmation()
            InlineNotice.warning(
                self.parent_widget, "Images changed or busy", "Review the checked scope and try again."
            )
            return
        self._running = True
        self._phase = "executing"
        self._action = plan.action
        self.gallery.set_recycle_busy(True)
        self._stop = threading.Event()
        activity_strip(self.parent_widget).start(
            "trash",
            "Deleting originals permanently" if self._action == "permanent" else f"Moving originals to {TRASH_NAME}",
            self.cancel,
            total=len(plan.targets),
        )
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
        self.gallery.recycle_completed(result.recycled + result.deleted)
        label = "Permanent deletion" if self._action == "permanent" else TRASH_NAME
        summary = (
            (
                f"{len(result.deleted)} permanently deleted.\n"
                if self._action == "permanent"
                else f"{len(result.recycled)} confirmed moved to {TRASH_NAME}.\n"
            )
            + f"{len(result.failed)} failed; {len(result.uncertain)} uncertain (manual review required).\n"
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
                elif reason == "delete_refused":
                    detail = "Delete refused before mutation; original unchanged, so this item may be retried."
                elif reason == "delete_uncertain":
                    detail = "Delete outcome unconfirmed; inspect the original before any manual action."
                elif reason == "delete_unsupported":
                    detail = "Windows mutation support is unavailable for this original."
                elif reason == "source_changed":
                    detail = "Original changed or became unsafe; removal was refused."
                else:
                    detail = reason
                summary += f"\n{identity}: {detail}"
            if len(result.failure_details) > 5:
                summary += f"\n… and {len(result.failure_details) - 5} more (see DTFlow logs)."
        if self._action == "permanent" and result.uncertain:
            summary += (
                "\nSome permanent deletions may or may not have occurred. "
                "Inspect the originals manually; no automatic retry will take place."
            )
        if result.canceled:
            summary += "\nStopped after the current image; completed removals remain."
        state = "partial" if result.failed or result.uncertain else "stopped" if result.canceled else "success"
        activity_strip(self.parent_widget).complete("trash", f"{label} result", summary, state)

    def _finish_with_error(self, error) -> None:
        phase = self._phase
        self._phase = "idle"
        self._running = False
        self.gallery.set_recycle_busy(False)
        log.warning("image recycling failed phase=%s reason=%s", phase, type(error).__name__)
        if phase == "preflight":
            if isinstance(error, RecycleUnavailable):
                reason = str(error)
            elif isinstance(error, (OriginalImagesBusyError, OriginalImagesUnavailableError)):
                reason = str(error)
            else:
                reason = "Image-removal prerequisites could not be verified."
            message = f"{reason} This request changed no originals."
            title = "Removal preflight refused"
        else:
            # A previously settled image must not be described as untouched.
            message = (
                "The removal run stopped without verifying every result. "
                "Some originals may already have changed; review the durable outcomes "
                "and inspect affected files before taking further action."
            )
            title = "Removal result needs review"
        activity_strip(self.parent_widget).complete("trash", title, message, "error")

    def cancel(self) -> None:
        """Request a safe stop between files without undoing successful moves."""
        self._stop.set()

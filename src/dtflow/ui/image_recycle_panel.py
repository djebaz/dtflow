"""Qt presentation for explicit, confirmed Trash or permanent removal."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QActionGroup, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QMenu, QPushButton, QWidget

from dtflow.core.gallery import GalleryQuery
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.image_removal_preferences import load_mode, save_mode
from dtflow.persistence.original_io import OriginalImagesBusyError, OriginalImagesUnavailableError
from dtflow.services.image_recycle import RecycleUnavailable, prepare_recycle, recycle_outputs
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.inline_feedback import InlineNotice, confirm_action, feedback
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.system_trash import TRASH_NAME, check_trash_source, recycle_file

log = logging.getLogger(__name__)
ORPHAN_HINT = "Cards whose original file is missing can only be cleared with Delete."


def _spaced_button_icon(source: QIcon) -> QIcon:
    """Keep the glyph at 16px, with a 6px transparent gap before button text."""
    icon = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled):
        canvas = QPixmap(22, 16)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, source.pixmap(QSize(16, 16), mode))
        painter.end()
        icon.addPixmap(canvas, mode)
    return icon


class _RemovalModeMenu(QMenu):
    """Anchor the popup to the whole split control, not only its arrow button."""

    def __init__(self, control: QFrame, parent: QWidget):
        super().__init__(parent)
        self._control = control

    def showEvent(self, event):
        # Recalculate for responsive gallery widths and font/DPI changes.
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.setFixedWidth(max(self._control.width(), self.sizeHint().width()))
        super().showEvent(event)
        below = self._control.mapToGlobal(QPoint(0, self._control.height() - 1))
        screen = QGuiApplication.screenAt(below)
        x, y = below.x(), below.y()
        if screen is not None:
            available = screen.availableGeometry()
            x = min(max(x, available.left()), max(available.left(), available.right() - self.width() + 1))
            if y + self.height() > available.bottom():
                top = self._control.mapToGlobal(QPoint(0, 0)).y()
                y = max(available.top(), top - self.height() + 1)
        self.move(x, y)


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
        self._processed_count = 0
        gallery._permanent_callback = self.request
        gallery.removal_ready = False
        self.menu_button = gallery._widget("galleryRemovalMenuButton", QPushButton)
        menu = _RemovalModeMenu(gallery._widget("galleryRemovalControl", QFrame), self.menu_button)
        menu.setObjectName("imageRemovalMenu")
        group = QActionGroup(menu)
        group.setExclusive(True)
        self._mode_actions = {}
        icon_root = Path(__file__).resolve().parents[1] / "resources" / "images" / "gallery"
        self._mode_icons = {}
        self._button_icons = {}
        for mode, normal, disabled in (
            ("trash", "delete.svg", "delete-disabled.svg"),
            ("permanent", "circle-x.svg", "circle-x-disabled.svg"),
        ):
            icon = QIcon(str(icon_root / normal))
            icon.addFile(str(icon_root / disabled), QSize(16, 16), QIcon.Mode.Disabled)
            self._mode_icons[mode] = icon
            self._button_icons[mode] = _spaced_button_icon(icon)
        for mode, label in (("trash", "Trash"), ("permanent", "Delete")):
            action = menu.addAction(self._mode_icons[mode], label)
            if mode == "permanent":
                action.setToolTip("Permanently delete selected images after confirmation; no Undo.")
            if mode == "trash" and gallery._recycle_callback is None:
                action.setToolTip(
                    "System Trash is unavailable. Selecting Trash disables the main removal action; you can still choose permanent deletion."
                )
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, mode=mode: self._choose_mode(mode))
            self._mode_actions[mode] = action
        menu.aboutToShow.connect(lambda: feedback(self.parent_widget).dismiss())
        self.menu_button.setMenu(menu)
        self.menu_button.setAccessibleName("Choose image removal mode")
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
        label = "Delete" if mode == "permanent" else "Trash"
        button = self.gallery.delete_button
        button.setText(label)
        button.setIcon(self._button_icons[mode])
        button.setIconSize(QSize(22, 16))
        button.setAccessibleName(label)
        button.setToolTip(
            "Irreversibly delete checked managed originals after explicit confirmation; no Undo."
            if mode == "permanent"
            else f"Move eligible checked originals to {TRASH_NAME} after confirmation. {ORPHAN_HINT}"
        )
        button.setMinimumWidth(button.fontMetrics().horizontalAdvance("Delete") + 58)
        for key, action in self._mode_actions.items():
            action.setChecked(key == mode)
        menu = self.menu_button.menu()
        menu.setProperty("permanentSelected", mode == "permanent")
        menu.style().unpolish(menu)
        menu.style().polish(menu)
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
        eligible = len(plan.targets) + len(plan.absent_targets)
        permanent = plan.action == "permanent"
        if not eligible:
            self._running = False
            self.gallery.set_recycle_busy(False)
            InlineNotice.information(
                self.parent_widget,
                "Nothing to remove",
                (
                    f"No eligible managed originals or confirmed missing cards. {plan.ineligible} ineligible "
                    "or require manual review."
                    if permanent
                    else f"No eligible managed original images. {plan.missing} missing; "
                    f"{plan.ineligible} ineligible or require manual review. {ORPHAN_HINT}"
                ),
            )
            return
        # Delete mode proves absence per card instead of counting a loose "missing" figure.
        raw_note = f" + {plan.raw_count} raw provider files" if plan.raw_count else ""
        message = (
            f"{len(plan.targets)} originals{raw_note} · {len(plan.absent_targets)} confirmed missing cards · "
            f"{plan.hidden} checked images hidden by filters included · "
            + ("" if permanent else f"{plan.missing} missing · ")
            + f"{plan.ineligible} ineligible (untouched). "
        )
        message += (
            "PERMANENT AND IRREVERSIBLE: existing originals will be destroyed, bypassing Trash/Recycle Bin; "
            "confirmed missing cards will be hidden without claiming physical deletion. "
            "No Undo. Metadata, exports and generation history are preserved."
            if permanent
            else f"Restore manually through {TRASH_NAME}; no DTFlow Undo or restoration guarantee. "
            "Recovered files stay hidden pending content-verified recovery. Exports and generation history preserved. "
            + ORPHAN_HINT
        )
        # Keep scope editable while awaiting consent, but invalidate it on any
        # selection/filter/navigation change. The destructive worker still revalidates.
        self._running = False
        self.gallery.set_recycle_busy(False)
        confirm_action(
            self.parent_widget,
            self.gallery.delete_button,
            f"Delete {eligible} selected gallery images?"
            if permanent
            else f"Move {eligible} selected originals to {TRASH_NAME}?",
            message,
            lambda: self._execute(plan),
            self._cancel_confirmation,
            danger=True,
            confirm_text="Delete" if permanent else "Trash",
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
        self._processed_count = 0
        self._action = plan.action
        self.gallery.set_recycle_busy(True)
        self._stop = threading.Event()
        activity_strip(self.parent_widget).start(
            "trash",
            "Deleting originals permanently" if self._action == "permanent" else f"Moving originals to {TRASH_NAME}",
            self.cancel,
            total=len(plan.targets) + len(plan.absent_targets),
        )
        self.runner.submit(
            asyncio.to_thread(
                recycle_outputs,
                self.state_dir,
                plan,
                self._stop,
                self._record_progress,
                recycle_adapter=recycle_file,
            ),
            self._completed,
        )

    def _record_progress(self, done: int, total: int) -> None:
        """Called by the worker before GUI event delivery, including on early failures."""
        self._processed_count = max(self._processed_count, done)
        self.progress.emit(done, total)

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
        self._phase = "idle"
        self.gallery.recycle_completed(result.recycled + result.deleted + result.absent)
        label = "Permanent deletion" if self._action == "permanent" else TRASH_NAME
        finished = {*result.recycled, *result.deleted}
        stranded = [identity for identity in result.raw if identity not in finished]
        raw_note = f" {len(result.raw)} raw files included." if result.raw else ""
        summary = (
            (
                f"{len(result.deleted)} originals permanently deleted;{raw_note} "
                f"{len(result.absent)} confirmed missing cards removed.\n"
                if self._action == "permanent"
                else f"{len(result.recycled)} confirmed moved to {TRASH_NAME}.{raw_note}\n"
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
                is_raw = reason.startswith("raw_")
                reason = reason.removeprefix("raw_")
                if reason.startswith("trash_receipt_"):
                    detail = (
                        f"Qt reported a successful move to {TRASH_NAME}, but the destination could not "
                        "be verified. Inspect system Trash; do not retry automatically."
                    )
                elif reason == "delete_refused":
                    detail = (
                        "Delete refused before mutation; the original was not confirmed unchanged. "
                        "Do not retry without manual review."
                        if identity in result.uncertain
                        else "Delete refused before mutation; the unchanged original may be retried with fresh consent."
                    )
                elif reason == "delete_uncertain":
                    detail = "Delete outcome unconfirmed; inspect the original before any manual action."
                elif reason == "delete_unsupported":
                    detail = "Windows mutation support is unavailable for this original."
                elif reason == "source_changed":
                    detail = (
                        "Original changed; removal outcome needs manual review."
                        if identity in result.uncertain
                        else "Original changed and the operation was refused."
                    )
                else:
                    detail = reason
                if is_raw:
                    detail = f"raw-1.png: {detail} The final image was left in place."
                summary += f"\n{identity}: {detail}"
            if len(result.failure_details) > 5:
                summary += f"\n… and {len(result.failure_details) - 5} more (see DTFlow logs)."
        if self._action == "permanent" and result.uncertain:
            summary += (
                "\nSome permanent deletions may or may not have occurred. "
                "Inspect the originals manually; no automatic retry will take place."
            )
        if stranded:
            summary += (
                f"\n{len(stranded)} raw files were removed but their final images remain; "
                "check them before removing again."
            )
        if result.canceled:
            summary += "\nStopped after the current image; completed removals remain."
        state = (
            "partial" if result.failed or result.uncertain or stranded else "stopped" if result.canceled else "success"
        )
        activity_strip(self.parent_widget).complete("trash", f"{label} result", summary, state)

    def _finish_with_error(self, error) -> None:
        phase = self._phase
        self._phase = "idle"
        self._running = False
        self.gallery.set_recycle_busy(False)
        diagnostic_event(
            log,
            "images.recycle.failed",
            level=logging.WARNING,
            data={
                "phase": phase,
                "reason": type(error).__name__,
            },
        )
        if phase == "preflight":
            reason = (
                str(error)
                if isinstance(error, (RecycleUnavailable, OriginalImagesBusyError, OriginalImagesUnavailableError))
                else "Image-removal prerequisites could not be verified."
            )
            message = f"{reason} This request changed no originals."
            title = "Removal preflight refused"
        elif self._processed_count == 0 and isinstance(
            error, (OriginalImagesBusyError, OriginalImagesUnavailableError, RecycleUnavailable)
        ):
            # Known lock/prerequisite refusal before the first file was processed.
            message = f"{error} No originals were changed by this request."
            title = "Removal not started"
        else:
            # A failure during/after a file cannot establish a zero-mutation result.
            message = (
                "The removal run stopped without verifying every result. "
                "Originals may already have changed; review the durable outcomes "
                "and inspect affected files before taking further action."
            )
            title = "Removal result needs review"
        activity_strip(self.parent_widget).complete("trash", title, message, "error")

    def cancel(self) -> None:
        """Request a safe stop between files without undoing successful moves."""
        self._stop.set()

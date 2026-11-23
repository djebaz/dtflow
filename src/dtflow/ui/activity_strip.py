"""Qt-owned feedback: running jobs and routine endings in the status bar, everything that needs reading as an overlay."""

import math
from dataclasses import dataclass
from time import monotonic
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QFile,
    QIODevice,
    QObject,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QWidget
from shiboken6 import isValid

from dtflow import startup
from dtflow.ui.app_filters import install_app_filter, remove_app_filter
from dtflow.ui.activity_bar import QUEUE_PALETTE, ActivityBar, BarModel, clock
from dtflow.ui.progress_model import ProgressSnapshot
from dtflow.ui.progress_text import aggregate_text
from dtflow.ui.trash_icon import trash_icon_pixmap


def _load(name, parent):
    file = QFile(str(Path(__file__).with_name("forms") / name))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"{name} unavailable")
    try:
        form = QUiLoader().load(file, parent)
    finally:
        file.close()
    if form is None:
        raise OSError(f"{name} could not be loaded")
    return form


# Running jobs and routine endings live in the status-bar bar; errors, warnings, partial results,
# notices and persistent conditions keep the overlay card, which needs room, actions and no expiry.
_RUN_RESULTS = {"generation-result", "queue-result", "queue-message", "queue-quick"}
_BAR_STATES = {"progress", "success", "stopped"}
_RESULT_STATES = {"success", "info", "warning", "partial", "stopped", "error"}
# Routine queue/Generate successes leave quickly; every other expiring notice keeps the longer reading time.
QUEUE_SUCCESS_SECONDS = 3.0
NOTICE_SECONDS = 6.0

_OPERATION_ICONS = {
    "export": "⇧",
    "asset": "⇧",
    "models": "⇩",
    "download": "⇩",
    "cache": "▤",
    "generation": "✦",
    "queue": "▦",
    "selection": "☑",
    "q8p": "▤",
}


@dataclass(frozen=True, slots=True)
class Aggregate:
    """What the global bar shows while any queue of this window (or another process) runs."""

    snapshot: ProgressSnapshot
    elapsed: str = ""
    eta: str = ""
    stop_target: str = ""  # names what Stop reaches when several queues are active; empty for a single queue


class _OutsidePress(QObject):
    """Application filter that only reports mouse presses; it never consumes them."""

    def __init__(self, parent, callback):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            self._callback(event.globalPosition().toPoint(), watched)
        return False


class ActivityStrip(QObject):
    operations_changed = Signal()

    def active_operations(self) -> tuple[str, ...]:
        """Bounded operation categories, never user labels, prompts or execution identities."""
        return tuple(
            sorted(
                {
                    str(key).split("-", 1)[0][:32]
                    for key, row in self.rows.items()
                    if isValid(row) and row.property("feedbackState") == "progress"
                }
            )
        )[:16]

    def __init__(self, window):
        super().__init__(window)
        startup.mark("activity strip: QObject initialized")
        self.window = window
        self.host = window.centralWidget()
        startup.mark("activity strip: initialization entered")
        self.form = _load("activity_strip.ui", self.host)
        startup.mark("activity strip: Designer form loaded")
        # A sibling scrim covers the workspace, not the feedback overlay. It is
        # Qt-owned, never part of the main layout and intercepts background clicks.
        self.scrim = QFrame(self.host)
        self.scrim.setObjectName("inlineEditorScrim")
        self.scrim.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scrim.hide()
        startup.mark("activity strip: scrim constructed")
        # Filters and deferred callbacks must disappear with the overlay frame.
        self.setParent(self.form)
        self.rows = {}
        self.status_bar = window.statusBar()
        startup.mark("activity strip: status bar acquired")
        self.bar = ActivityBar(self.status_bar)
        startup.mark("activity strip: painted bar constructed")
        self.bar.stop_requested.connect(self._bar_stop)
        self.bar.dismiss_requested.connect(self._bar_dismiss)
        self._bar_key = None
        self._aggregate: Aggregate | None = None
        self._palette: dict[str, int] = {}  # queue id -> palette slot, kept while the queue stays active
        self._palette_reuse = 0
        self._last_run = None
        self.status_bar.installEventFilter(self)
        self.status_bar.messageChanged.connect(lambda _text: QTimer.singleShot(0, self, self._position))
        self._expiry = {}
        self._positioning = False
        self._retired = False
        self.feedback = None
        self.form.hide()
        self.form.destroyed.connect(self._retire)
        self.host.installEventFilter(self)
        self.form.installEventFilter(self)
        self._outside = _OutsidePress(self.form, self._outside_press)
        self._outside_installed = False
        self._notice_timer = QTimer(self.form)
        self._notice_timer.setInterval(100)
        self._notice_timer.timeout.connect(self._tick_notices)
        # Elapsed time and ETA move once a second while a job runs.
        self._bar_timer = QTimer(self.form)
        self._bar_timer.setInterval(1000)
        self._bar_timer.timeout.connect(self._sync_bar)
        window._activity_strip = self
        startup.mark("activity strip: wiring and timers ready")

    def mount_feedback(self, panel):
        self.feedback = panel
        panel.form.destroyed.connect(self._feedback_destroyed)
        self.form.activityRowsLayout.insertWidget(0, panel.form)
        panel.form.hide()

    @Slot()
    def _feedback_destroyed(self):
        """Forget the child before its parent receives more layout events."""
        self.feedback = None

    def _live(self) -> bool:
        """Qt can invalidate a child wrapper before its parent's destroyed signal."""
        return not self._retired and all(
            isValid(widget) for widget in (self.window, self.host, self.form, self.status_bar, self.bar)
        )

    @property
    def retired(self) -> bool:
        return self._retired

    @Slot()
    def _retire(self):
        """Mark the strip retired before its Qt children are destroyed."""
        self._retired = True
        # Qt removes event filters and stops child timers as their owners die.
        # The destroyed signal must not call methods on a dying host or form.
        self._expiry.clear()
        self.rows.clear()
        self.feedback = None
        if self._outside_installed:
            self._outside_installed = False
            remove_app_filter(self._outside)

    def eventFilter(self, watched, event):
        if not self._live():
            return False
        if (
            watched.objectName() == "activityStatusDismissButton"
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            watched.click()
            return True
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            if watched is self.status_bar:
                # Child geometry is stale until the status bar has laid itself out.
                QTimer.singleShot(0, self, self._position)
            else:
                self._position()
        return False

    def set_editor_dimming(self, enabled: bool) -> None:
        """Dim and block only the workspace behind an active inline input editor."""
        if not self._live() or not isValid(self.scrim):
            return
        if enabled:
            self.scrim.setGeometry(self.host.rect())
            self.scrim.show()
            self.scrim.raise_()
            self._position()
        else:
            self.scrim.hide()

    @staticmethod
    def _in_bar(row) -> bool:
        return row.property("feedbackState") in _BAR_STATES and not row.property("compactStatus")

    def _bar_row(self):
        """The row the bar shows: the newest running job, else the newest routine ending."""
        rows = [(key, row) for key, row in self.rows.items() if isValid(row) and self._in_bar(row)]
        running = [item for item in rows if item[1].property("feedbackState") == "progress"]
        if self._aggregate is not None:
            # The aggregate bar keeps priority over newer unrelated operation rows; those stay in the overlay.
            owner = [item for item in running if item[0] == "run"]
            if owner:
                return owner[0]
        chosen = running or rows
        return chosen[-1] if chosen else (None, None)

    def _sync_bar(self):
        """Present one eligible row in the status bar; every other row stays in the overlay.

        The chosen row remains the data model but leaves the overlay layout. If the bar has no room
        (a long temporary message, a narrow window) nothing is chosen and the overlay shows it all.
        """
        if not self._live():
            return
        rect = self._bar_rect()
        key, chosen = self._bar_row() if rect is not None else (None, None)
        layout = self.form.activityRowsLayout
        for row in self.rows.values():
            if not isValid(row):
                continue
            if row is chosen and not row.property("inBar"):
                layout.removeWidget(row)
                row.hide()
                row.setProperty("inBar", True)
            elif row is not chosen and row.property("inBar"):
                row.setProperty("inBar", False)
                layout.addWidget(row)
                row.show()
        if chosen is None:
            self._bar_key = None
            self._bar_timer.stop()
            if isValid(self.bar):
                self.bar.hide()
            return
        self._bar_key = key
        state = chosen.property("feedbackState")
        running = state == "progress"
        now = monotonic()
        started = chosen.property("startedAt")
        ended = chosen.property("endedAt") or now
        # A result whose job was started under another key has no known duration: show none.
        elapsed = ((now if running else ended) - started) if started is not None else None
        total = chosen.activityProgress.maximum()
        done = chosen.activityProgress.value()
        known = running and total > 0
        suffix = " total" if str(key).startswith("queue") else ""
        elapsed_text = "" if elapsed is None else clock(elapsed) if running else clock(elapsed) + suffix
        eta = ""
        if known and elapsed is not None and 0 < done < total and elapsed >= 3:
            eta = f"~{clock(elapsed * (total - done) / done)} left"
        # A job that reports its own timing (queue) overrides the generic elapsed and linear estimate.
        reported_elapsed, reported_eta = chosen.property("elapsedText") or "", chosen.property("etaText") or ""
        if running and (reported_elapsed or reported_eta):
            elapsed_text = reported_elapsed.removeprefix("Elapsed ")
            eta = reported_eta.split(" (")[0]
        stop = chosen.activityStopButton
        stopping = running and not stop.isHidden() and not stop.isEnabled()
        detail = chosen.activityDetail.text()
        stage = detail if not (running and detail in ("Preparing…", "")) else ("Preparing" if not known else "")
        reported_stage = chosen.property("stageText") or ""
        if running and not stopping and reported_stage:
            stage = reported_stage
        label = chosen.activityLabel.text()
        model = BarModel(
            kind={"progress": "running"}.get(state, state),
            title=label,
            count=""
            if chosen.property("fillOnly")
            else (chosen.property("countText") or f"{done}/{total}")
            if known
            else "",
            queued=(chosen.property("queuedCount") or 0) if running else 0,
            stage=stage,
            wide=(chosen.property("wideText") or "") if running and not stopping else "",
            fraction=(done / total) if known else None,
            elapsed=elapsed_text,
            eta=eta,
            stopping=stopping,
            lifetime=(chosen.activityLifetimeBar.value() / max(1, chosen.activityLifetimeBar.maximum()))
            if not running and not chosen.activityLifetimeBar.isHidden()
            else None,
            tooltip=" · ".join(part for part in (label, detail, reported_eta) if part),
            stop_label=stop.text(),
            stop_visible=not stop.isHidden(),
            stop_action=chosen.property("stopActionText") or "Stop",
        )
        if self._aggregate is not None and key == "run" and running:
            model = self._aggregate_model(model, stopping)
        effect = chosen.graphicsEffect()
        self.bar.present(
            model,
            stop_enabled=stop.isEnabled(),
            opacity=effect.opacity() if isinstance(effect, QGraphicsOpacityEffect) else 1.0,
        )
        self.bar.setGeometry(rect)
        self.bar.show()
        self.bar.raise_()
        if running:
            self._bar_timer.start()
        else:
            self._bar_timer.stop()

    def set_aggregate(self, aggregate: Aggregate | None) -> None:
        """Show (or stop showing) the aggregate over every active queue in the status bar's "run" row."""
        if not self._live():
            return
        self._aggregate = aggregate
        active = {queue.totals.queue_id for queue in aggregate.snapshot.queues} if aggregate is not None else set()
        for queue_id in [queue_id for queue_id in self._palette if queue_id not in active]:
            del self._palette[queue_id]
        if aggregate is not None:
            for queue in aggregate.snapshot.queues:
                self._palette_slot(queue.totals.queue_id)
        self._sync_bar()

    def _palette_slot(self, queue_id: str) -> int:
        """A stable slot per active queue: the lowest free colour, then deterministic reuse above six queues."""
        if queue_id not in self._palette:
            free = [slot for slot in range(len(QUEUE_PALETTE)) if slot not in self._palette.values()]
            if free:
                self._palette[queue_id] = free[0]
            else:
                self._palette[queue_id] = self._palette_reuse % len(QUEUE_PALETTE)
                self._palette_reuse += 1
        return self._palette[queue_id]

    def _aggregate_model(self, base: BarModel, stopping: bool) -> BarModel:
        aggregate = self._aggregate
        snapshot = aggregate.snapshot
        segments, end = [], 0.0
        for queue in snapshot.queues:
            end += queue.contribution / snapshot.total if snapshot.total else 0.0
            segments.append((min(1.0, end), QUEUE_PALETTE[self._palette_slot(queue.totals.queue_id)]))
        text = aggregate_text(snapshot)  # elapsed/ETA are painted at the right edge, not in the left text
        full = aggregate_text(snapshot, elapsed=aggregate.elapsed, eta=aggregate.eta).full
        stop_label = base.stop_label
        if aggregate.stop_target:
            stop_label = f"{base.stop_label} · {aggregate.stop_target}".strip(" ·")
        return BarModel(
            kind="running",
            title="Queue progress",
            fraction=snapshot.fill,
            elapsed=aggregate.elapsed,
            eta=aggregate.eta,
            stopping=stopping,
            tooltip=full,
            stop_label=stop_label,
            stop_visible=base.stop_visible,
            stop_action=base.stop_action,
            segments=tuple(segments),
            texts=text.candidates,
            timed=text.timed,
        )

    def _bar_rect(self):
        """Space between the version text and the right-hand controls, without displacing either."""
        if not self._live():
            return None
        status = self.status_bar
        message = status.currentMessage()
        left = status.fontMetrics().horizontalAdvance(message) + 30 if message else 12
        right = status.width() - 12
        for child in status.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if child is self.bar or not child.isVisible() or child.width() <= 0:
                continue
            if child.x() > status.width() // 3:
                right = min(right, child.x() - 24)
        width = right - left
        if width < 240 or status.height() < 1:
            return None
        return QRect(left, max(0, (status.height() - self.bar.height()) // 2), width, self.bar.height())

    @Slot()
    def _bar_stop(self):
        row = self.rows.get(self._bar_key)
        if row is not None and isValid(row):
            row.activityStopButton.click()

    @Slot()
    def _bar_dismiss(self):
        row = self.rows.get(self._bar_key)
        if row is not None and isValid(row):
            row.activityDismissButton.click()

    @Slot()
    def _position(self):
        if not self._live() or self._positioning:
            return
        self._positioning = True
        try:
            if isValid(self.scrim) and self.scrim.isVisible():
                self.scrim.setGeometry(self.host.rect())
                self.scrim.raise_()
            self._sync_bar()
            overlay_rows = {k: r for k, r in self.rows.items() if isValid(r) and not r.property("inBar")}
            panel = self.feedback
            if panel is not None and not isValid(panel.form):
                self.feedback = None
                panel = None
            visible_feedback = panel is not None and not panel.form.isHidden()
            if not overlay_rows and not visible_feedback:
                self.form.hide()
                return
            width = max(1, self.host.width() - 16)
            self.form.resize(width, self.form.height())
            if visible_feedback:
                panel.fit_text(width)
            layout = self.form.activityRowsLayout
            height = layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
            compact_only = (
                not visible_feedback
                and overlay_rows
                and all(row.property("compactStatus") for row in overlay_rows.values())
            )
            height = max(1 if compact_only else 48, height)
            # Prepare is a batch editor, not a short notice. Use the available
            # workspace height so many rows can be inspected without a tiny viewport.
            # Other inline editors and progress notices retain their natural size.
            editor = getattr(panel, "_editor", None) if visible_feedback else None
            if editor is not None and isValid(editor) and editor.property("expandEditorHeight"):
                height = min(max(height, self.host.height() - 80), max(1, self.host.height() - 16))
            rect = QRect(8, max(0, self.host.height() - height - 8), width, height)
            if self.form.geometry() != rect:
                self.form.setGeometry(rect)
            self.form.show()
            self.form.raise_()
        finally:
            self._positioning = False

    def status(self, key, text, dismissed):
        """One persistent single-line condition, independent of feedback/editors.

        The caller owns episode/dismissal policy; retries update this keyed row
        in place and never arm the transient-result expiry timer.
        """
        if not self._live():
            return
        row = self.rows.get(key)
        if row is None or not isValid(row):
            row = _load("activity_status_row.ui", self.form)
            row.setProperty("compactStatus", True)
            row.setProperty("feedbackState", "warning")
            line = max(
                row.activityStatusText.fontMetrics().height(), row.activityStatusDismissButton.fontMetrics().height()
            )
            row.setFixedHeight(line + 6)
            row.activityStatusDismissButton.setFixedSize(line, line)

            def close_status():
                dismissed()
                self.finish(key)

            row.activityStatusDismissButton.clicked.connect(close_status)
            # Local key handling never competes with an active editor's Escape.
            row.activityStatusDismissButton.installEventFilter(self)
            self.rows[key] = row
            self.form.activityRowsLayout.addWidget(row)
        if row.activityStatusText.text() != text:
            row.activityStatusText.setText(text)
            row.activityStatusText.setToolTip(text)
            row.activityStatusText.setAccessibleName(text)
        self._position()

    def start(
        self, key, label, stop=None, stop_text="Stop after current image", total=0, detail="", *, action_text="Stop"
    ):
        if not self._live():
            return
        self.finish(key)
        row = _load("activity_progress_row.ui", self.form)
        row.setProperty("feedbackState", "progress")
        row.setProperty("startedAt", monotonic())
        row.setProperty("stopActionText", action_text)
        if key.split("-")[0] == "trash":
            row.activitySymbol.setPixmap(trash_icon_pixmap())
        else:
            symbol = _OPERATION_ICONS.get(key.split("-")[0])
            if symbol is not None:
                row.activitySymbol.setText(symbol)
            else:
                # A font ellipsis can trigger a costly first-use fallback during session restoration.
                icon = Path(__file__).parents[1] / "resources" / "images" / "gallery" / "more.svg"
                row.activitySymbol.setPixmap(QIcon(str(icon)).pixmap(24, 24))
        row.activityLabel.setText(label)
        row.activityDetail.setText(detail or "Preparing…")
        row.activityProgress.setAccessibleName(label + " progress")
        row.activityProgress.setRange(0, total)
        row.activityCount.setText(f"0 / {total}" if total else "")
        row.activityStopButton.setVisible(stop is not None)
        row.activityDismissButton.hide()
        row.activityLifetime.hide()
        row.activityLifetimeBar.hide()
        if stop is not None:
            row.activityStopButton.setText(stop_text)

            def request_stop():
                row.activityStopButton.setEnabled(False)
                row.activityDetail.setText(
                    "Cancelling before admission; nothing was submitted."
                    if stop_text == "Cancel before start"
                    else "Cancelling download; the partial file is removed, completed models stay installed."
                    if stop_text == "Cancel download"
                    else "Stopping local conversion; no partial output will be published."
                    if stop_text == "Cancel conversion"
                    else "Cancelling downloads; partial files are removed, converted LoRAs stay."
                    if stop_text == "Cancel downloads"
                    else "Stopping after the current file; completed files remain and no upload is cut off."
                    if stop_text == "Stop after current file"
                    else "Stopping local wait; remote cancellation is not confirmed."
                    if stop_text == "Stop waiting locally"
                    else "Stopping after the current item; completed work remains."
                )
                self._sync_bar()
                stop()

            row.activityStopButton.clicked.connect(request_stop)
        self.rows[key] = row
        self.form.activityRowsLayout.addWidget(row)
        self.operations_changed.emit()
        self._position()
        QTimer.singleShot(0, self, self._position)

    def set_stop_visible(self, key, visible: bool) -> None:
        """Show or hide the Stop button of a running row (a stopping row keeps its disabled button)."""
        row = self.rows.get(key)
        if not self._live() or row is None or not isValid(row) or row.property("feedbackState") != "progress":
            return
        button = row.activityStopButton
        if button.isEnabled() and button.isHidden() == visible:
            button.setVisible(visible)
            self._sync_bar()

    def update(
        self,
        key,
        done,
        total,
        *,
        detail=None,
        elapsed="",
        eta="",
        stage="",
        wide="",
        queued=0,
        count=None,
        fill_only=False,
    ):
        """``count`` replaces the default "done / total" text (e.g. transferred MiB when done/total are percent)."""
        if not self._live():
            return
        row = self.rows.get(key)
        if row is not None and not isValid(row):
            self.rows.pop(key, None)
            self._expiry.pop(key, None)
            return
        if row is not None and row.property("feedbackState") == "progress":
            row.activityProgress.setRange(0, max(1, total) if total else 0)
            row.activityProgress.setValue(done)
            row.activityCount.setText(count if count is not None else f"{done} / {total}" if total else "")
            row.setProperty("countText", count or "")
            row.setProperty("fillOnly", fill_only)
            if row.activityStopButton.isEnabled() or row.activityStopButton.isHidden():
                row.activityDetail.setText(
                    detail if detail is not None else f"{done} of {total} processed" if total else "Working…"
                )
            row.setProperty("elapsedText", elapsed)
            row.setProperty("etaText", eta)
            row.setProperty("stageText", stage)
            row.setProperty("wideText", wide)
            row.setProperty("queuedCount", queued)
            self._sync_bar()

    def complete(self, key, title, text, state="success"):
        """Show a semantic result: routine queue successes expire after three seconds, amber outcomes after six."""
        if not self._live():
            return
        previous = self.rows.get(key)
        started = previous.property("startedAt") if previous is not None and isValid(previous) else None
        if (
            started is None
            and key in _RUN_RESULTS
            and self._last_run is not None
            and monotonic() - self._last_run[1] < 30
        ):
            started = self._last_run[0]  # a result right after the "run" job ended reports that job's duration
        self.start(key, title)
        row = self.rows[key]
        row.setProperty("startedAt", started)  # unknown when the job ran under another key
        row.setProperty("endedAt", monotonic())
        row.setProperty("feedbackState", state)
        self.operations_changed.emit()
        row.activitySymbol.setText(
            {"success": "✓", "info": "i", "stopped": "■", "partial": "⚠", "warning": "⚠"}.get(state, "!")
        )
        row.activityDetail.setText(text)
        row.activityProgress.hide()
        row.activityCount.hide()
        row.activityStopButton.hide()
        row.activityDismissButton.show()
        row.activityDismissButton.clicked.connect(lambda: self.finish(key))
        row.style().unpolish(row)
        row.style().polish(row)
        for widget in (row.activitySymbol, row.activityLifetime, row.activityLifetimeBar):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if state in {"success", "warning", "partial", "stopped"}:
            seconds = self._lifetime_for(key, state)
            self._expiry[key] = seconds
            row.activityLifetime.setText(f"{math.ceil(seconds)}s")
            row.activityLifetime.show()
            row.activityLifetimeBar.setMaximum(round(seconds * 1000))
            row.activityLifetimeBar.setValue(round(seconds * 1000))
            row.activityLifetimeBar.show()
            self._notice_timer.start()
        self._sync_outside()
        self._position()

    @staticmethod
    def _lifetime_for(key: str, state: str) -> float:
        """Routine queue and Generate successes last three seconds; other expiring notices keep six."""
        queue_key = key in _RUN_RESULTS or str(key).startswith("queue-")
        return QUEUE_SUCCESS_SECONDS if state == "success" and queue_key else NOTICE_SECONDS

    @staticmethod
    def _pointer_over(widget) -> bool:
        """The cursor is inside a widget that is actually showing.

        Qt's ``underMouse`` stays true for a widget that was hidden or re-laid out while the cursor rested on it, which
        left a routine notice paused until the mouse moved. A real hit test on a visible widget has no such state.
        """
        if not isValid(widget) or not widget.isVisible():
            return False
        return widget.rect().contains(widget.mapFromGlobal(QCursor.pos()))

    def _focus_in_view(self, focused) -> bool:
        """Keyboard focus inside the showing overlay or bar pauses the countdown; a hidden holder does not."""
        if focused is None or not isValid(focused) or not focused.isVisible() or focused.window() is not self.window:
            return False
        return any(
            container.isVisible() and (focused is container or container.isAncestorOf(focused))
            for container in (self.form, self.bar)
        )

    def _result_keys(self) -> list:
        """Keys of the visible transient notices (results, not running jobs or persistent status conditions)."""
        return [
            key
            for key, row in self.rows.items()
            if isValid(row) and row.property("feedbackState") in _RESULT_STATES and not row.property("compactStatus")
        ]

    def _sync_outside(self) -> None:
        """Watch presses application-wide only while a notice is showing."""
        if not self._live():
            return
        wanted = bool(self._result_keys())
        if wanted == self._outside_installed:
            return
        self._outside_installed = wanted
        (install_app_filter if wanted else remove_app_filter)(self._outside)

    def _outside_press(self, point, target) -> None:
        """One press outside the overlay and the bar dismisses every visible notice; running work is untouched."""
        if not self._live() or not isValid(target) or target.window() is not self.window:
            return  # a popup, menu or dialog press belongs to its own window
        # The bar protects only a notice it shows; a bar that merely shows running progress is outside the notice.
        model = self.bar.strip.model if isValid(self.bar) else None
        showing_notice = model is not None and model.kind != "running"
        for widget in (self.form, self.bar if showing_notice else None):
            if widget is not None and isValid(widget) and widget.isVisible():
                if widget.rect().contains(widget.mapFromGlobal(point)):
                    return
        keys = self._result_keys()
        if keys:
            # Qt may be iterating the application's filter list: remove rows (and the filter) after this event.
            QTimer.singleShot(0, self, lambda: [self.finish(key) for key in keys if key in self.rows])

    @Slot()
    def _tick_notices(self):
        if not self._live():
            return
        focused = QApplication.focusWidget()
        for key, remaining in tuple(self._expiry.items()):
            row = self.rows.get(key)
            if row is None or not isValid(row):
                self._expiry.pop(key, None)
                self.rows.pop(key, None)
                continue
            if self._pointer_over(self.form) or self._pointer_over(self.bar) or self._focus_in_view(focused):
                continue
            remaining -= 0.1
            if remaining <= 0:
                self._expiry.pop(key, None)
                row.activityLifetimeBar.setValue(0)
                effect = QGraphicsOpacityEffect(row)
                row.setGraphicsEffect(effect)
                # Hidden backing rows still animate; mirror only the selected row onto the shared bar.
                effect.opacityChanged.connect(self._sync_bar)
                fade = QPropertyAnimation(effect, b"opacity", row)
                fade.setDuration(450)
                fade.setStartValue(1.0)
                fade.setEndValue(0.0)
                fade.setEasingCurve(QEasingCurve.Type.OutCubic)
                fade.finished.connect(lambda key=key, row=row: self._finish_if_row(key, row))
                row._lifetime_fade = fade
                fade.start()
            else:
                self._expiry[key] = remaining
                row.activityLifetime.setText(f"{math.ceil(remaining)}s")
                row.activityLifetimeBar.setValue(max(0, round(remaining * 1000)))
        if not self._expiry:
            self._notice_timer.stop()
        self._sync_bar()  # redraw the result's lifetime line

    def _finish_if_row(self, key, row):
        """Ignore an old fade callback if a new task has reused the same key."""
        if self._live() and self.rows.get(key) is row:
            self.finish(key)

    def finish(self, key):
        if not self._live():
            return
        if key == "run":
            self._aggregate = None
        self._expiry.pop(key, None)
        row = self.rows.pop(key, None)
        if key == "run" and row is not None and isValid(row) and row.property("startedAt") is not None:
            self._last_run = (row.property("startedAt"), monotonic())
        if row is not None and isValid(row):
            self.form.activityRowsLayout.removeWidget(row)
            row.hide()
            row.deleteLater()
        if not self._expiry:
            self._notice_timer.stop()
        if row is not None:
            self.operations_changed.emit()
        self._sync_outside()
        self._position()


def activity_strip(parent):
    try:
        return getattr(parent.window(), "_activity_strip", None)
    except RuntimeError:
        return None  # the widget was already deleted (application teardown)

"""One thin, Qt-owned bottom overlay for consent, editors, progress and results."""

import math
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QFile, QIODevice, QObject, QPropertyAnimation, QRect, Qt, QTimer, Slot
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect
from shiboken6 import isValid

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


_OPERATION_ICONS = {
    "export": "⇧",
    "asset": "⇧",
    "cache": "▤",
    "generation": "✦",
    "queue": "▦",
    "selection": "☑",
}


class ActivityStrip(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.host = window.centralWidget()
        self.form = _load("activity_strip.ui", self.host)
        # A sibling scrim covers the workspace, not the feedback overlay. It is
        # Qt-owned, never part of the main layout and intercepts background clicks.
        self.scrim = QFrame(self.host)
        self.scrim.setObjectName("inlineEditorScrim")
        self.scrim.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scrim.hide()
        # Filters and deferred callbacks must disappear with the overlay frame.
        self.setParent(self.form)
        self.rows = {}
        self.docks = []
        self._expiry = {}
        self._positioning = False
        self._retired = False
        self.feedback = None
        self.form.hide()
        self.form.destroyed.connect(self._retire)
        self.host.installEventFilter(self)
        self.form.installEventFilter(self)
        self._notice_timer = QTimer(self.form)
        self._notice_timer.setInterval(100)
        self._notice_timer.timeout.connect(self._tick_notices)
        window._activity_strip = self

    def mount_feedback(self, panel):
        self.feedback = panel
        panel.form.destroyed.connect(self._feedback_destroyed)
        self.form.activityRowsLayout.insertWidget(0, panel.form)
        panel.form.hide()

    def register_dock(self, dock, anchor):
        """Let a page host running-progress rows in its own bar while `anchor` is visible.

        `dock` is an empty Designer container inside `anchor`. Results, warnings and
        pages without a dock keep using the bottom overlay.
        """
        if dock.layout() is None:
            raise ValueError("Activity dock needs a layout.")
        self.docks.append((dock, anchor))
        anchor.installEventFilter(self)

    def _active_dock(self):
        self.docks = [(d, a) for d, a in self.docks if isValid(d) and isValid(a)]
        for dock, anchor in self.docks:
            if anchor.isVisible():
                return dock
        return None

    def _place_rows(self):
        """Dock progress rows into the visible page bar and move everything else to the overlay."""
        active = self._active_dock()
        for row in self.rows.values():
            if not isValid(row):
                continue
            docked = row.parentWidget() is not self.form
            want = active is not None and row.property("feedbackState") == "progress"
            if want and row.parentWidget() is active:
                continue
            if not want and not docked:
                continue
            if docked:
                row.parentWidget().layout().removeWidget(row)
            if want:
                active.layout().addWidget(row)
            else:
                self.form.activityRowsLayout.addWidget(row)
            self._style_docked(row, want)
        for dock, _anchor in self.docks:
            dock.setVisible(dock.layout().count() > 0)

    @staticmethod
    def _style_docked(row, docked):
        """A docked row is one compact line inside a page bar; the overlay row keeps its card look."""
        row.setProperty("docked", docked)
        row.activitySymbol.setVisible(not docked)
        row.activityLabel.setWordWrap(not docked)
        row.activityDetail.setWordWrap(not docked)
        row.activityDetail.setMaximumWidth(260 if docked else 16777215)
        row.activityRowLayout.setContentsMargins(*((0, 0, 0, 0) if docked else (8, 7, 8, 7)))
        row.style().unpolish(row)
        row.style().polish(row)

    @Slot()
    def _feedback_destroyed(self):
        """Forget the child before its parent receives more layout events."""
        self.feedback = None

    def _live(self) -> bool:
        """Qt can invalidate a child wrapper before its parent's destroyed signal."""
        return not self._retired and isValid(self.host) and isValid(self.form)

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
        self.docks.clear()
        self.feedback = None

    def eventFilter(self, watched, event):
        if (
            not self._retired
            and watched.objectName() == "activityStatusDismissButton"
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            watched.click()
            return True
        if not self._retired and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
        ):
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

    @Slot()
    def _position(self):
        if not self._live() or self._positioning:
            return
        self._positioning = True
        try:
            if isValid(self.scrim) and self.scrim.isVisible():
                self.scrim.setGeometry(self.host.rect())
                self.scrim.raise_()
            self._place_rows()
            overlay_rows = {k: r for k, r in self.rows.items() if isValid(r) and r.parentWidget() is self.form}
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

    def start(self, key, label, stop=None, stop_text="Stop after current image", total=0, detail=""):
        if not self._live():
            return
        self.finish(key)
        row = _load("activity_progress_row.ui", self.form)
        row.setProperty("feedbackState", "progress")
        if key.split("-")[0] == "trash":
            row.activitySymbol.setPixmap(trash_icon_pixmap())
        else:
            row.activitySymbol.setText(_OPERATION_ICONS.get(key.split("-")[0], "⋯"))
        row.activityLabel.setText(label)
        row.activityDetail.setText(detail or "Preparing…")
        row.activityDetail.setToolTip(detail or "")
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
                    "Stopping local wait; remote cancellation is not confirmed."
                    if stop_text == "Stop waiting locally"
                    else "Stopping after the current item; completed work remains."
                )
                stop()

            row.activityStopButton.clicked.connect(request_stop)
        self.rows[key] = row
        self.form.activityRowsLayout.addWidget(row)
        self._position()
        QTimer.singleShot(0, self, self._position)

    def update(self, key, done, total, *, detail=None):
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
            row.activityCount.setText(f"{done} / {total}" if total else "")
            if row.activityStopButton.isEnabled() or row.activityStopButton.isHidden():
                text = detail if detail is not None else f"{done} of {total} processed" if total else "Working…"
                row.activityDetail.setText(text)
                row.activityDetail.setToolTip(text)

    def complete(self, key, title, text, state="success"):
        """Show a semantic result: success and amber outcomes expire after six seconds."""
        if not self._live():
            return
        self.start(key, title)
        row = self.rows[key]
        row.setProperty("feedbackState", state)
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
            self._expiry[key] = 6.0
            row.activityLifetime.setText("6s")
            row.activityLifetime.show()
            row.activityLifetimeBar.setValue(6000)
            row.activityLifetimeBar.show()
            self._notice_timer.start()
        self._position()

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
            if self.form.underMouse() or (
                focused is not None and (focused is self.form or self.form.isAncestorOf(focused))
            ):
                continue
            remaining -= 0.1
            if remaining <= 0:
                self._expiry.pop(key, None)
                effect = QGraphicsOpacityEffect(row)
                row.setGraphicsEffect(effect)
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

    def _finish_if_row(self, key, row):
        """Ignore an old fade callback if a new task has reused the same key."""
        if self._live() and self.rows.get(key) is row:
            self.finish(key)

    def finish(self, key):
        if not self._live():
            return
        self._expiry.pop(key, None)
        row = self.rows.pop(key, None)
        if row is not None and isValid(row):
            self.form.activityRowsLayout.removeWidget(row)
            row.hide()
            row.deleteLater()
        if not self._expiry:
            self._notice_timer.stop()
        self._position()


def activity_strip(parent):
    return getattr(parent.window(), "_activity_strip", None)

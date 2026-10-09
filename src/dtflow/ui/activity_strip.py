"""One thin, Qt-owned bottom overlay for consent, editors, progress and results."""

import math
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QFile, QIODevice, QObject, QPropertyAnimation, QRect, Qt, QTimer, Slot
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect
from shiboken6 import isValid


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
    "trash": "♻",
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
        self.feedback = None

    def eventFilter(self, watched, event):
        if not self._retired and event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
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
            panel = self.feedback
            if panel is not None and not isValid(panel.form):
                self.feedback = None
                panel = None
            visible_feedback = panel is not None and not panel.form.isHidden()
            if not self.rows and not visible_feedback:
                self.form.hide()
                return
            width = max(1, self.host.width() - 16)
            self.form.resize(width, self.form.height())
            if visible_feedback:
                panel.fit_text(width)
            layout = self.form.activityRowsLayout
            height = layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
            height = max(48, height)
            rect = QRect(8, max(0, self.host.height() - height - 8), width, height)
            if self.form.geometry() != rect:
                self.form.setGeometry(rect)
            self.form.show()
            self.form.raise_()
        finally:
            self._positioning = False

    def start(self, key, label, stop=None, stop_text="Stop after current image", total=0, detail=""):
        if not self._live():
            return
        self.finish(key)
        row = _load("activity_progress_row.ui", self.form)
        row.setProperty("feedbackState", "progress")
        row.activitySymbol.setText(_OPERATION_ICONS.get(key.split("-")[0], "⋯"))
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
                row.activityDetail.setText("Stopping after the current item; completed work remains.")
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
                row.activityDetail.setText(
                    detail if detail is not None else f"{done} of {total} processed" if total else "Working…"
                )

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

"""One thin, Qt-owned bottom overlay for consent, editors, progress and results."""

import math
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QRect, Qt, QTimer, Slot
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QFrame


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
        self.form.activityRowsLayout.insertWidget(0, panel.form)
        panel.form.hide()

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
        if self._retired:
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
        if self._retired or self._positioning:
            return
        self._positioning = True
        try:
            if self.scrim.isVisible():
                self.scrim.setGeometry(self.host.rect())
                self.scrim.raise_()
            visible_feedback = self.feedback is not None and not self.feedback.form.isHidden()
            if not self.rows and not visible_feedback:
                self.form.hide()
                return
            width = max(1, self.host.width() - 16)
            self.form.resize(width, self.form.height())
            if visible_feedback:
                self.feedback.fit_text(width)
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
        if self._retired:
            return
        self.finish(key)
        row = _load("activity_progress_row.ui", self.form)
        row.setProperty("feedbackState", "progress")
        row.activitySymbol.setText("⋯")
        row.activityLabel.setText(label)
        row.activityDetail.setText(detail or "Preparing…")
        row.activityProgress.setAccessibleName(label + " progress")
        row.activityProgress.setRange(0, total)
        row.activityCount.setText(f"0 / {total}" if total else "")
        row.activityStopButton.setVisible(stop is not None)
        row.activityDismissButton.hide()
        row.activityLifetime.hide()
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

    def update(self, key, done, total):
        if self._retired:
            return
        row = self.rows.get(key)
        if row is not None and row.property("feedbackState") == "progress":
            row.activityProgress.setRange(0, max(1, total) if total else 0)
            row.activityProgress.setValue(done)
            row.activityCount.setText(f"{done} / {total}" if total else "")
            if row.activityStopButton.isEnabled() or row.activityStopButton.isHidden():
                row.activityDetail.setText(f"{done} of {total} processed" if total else "Working…")

    def complete(self, key, title, text, state="success"):
        """Display outcomes without disarming unrelated confirmation or replacing another task."""
        if self._retired:
            return
        self.start(key, title)
        row = self.rows[key]
        row.setProperty("feedbackState", state)
        row.activitySymbol.setText("✓" if state == "success" else "!")
        row.activityDetail.setText(text)
        row.activityProgress.hide()
        row.activityCount.hide()
        row.activityStopButton.hide()
        row.activityDismissButton.show()
        row.activityDismissButton.clicked.connect(lambda: self.finish(key))
        row.style().unpolish(row)
        row.style().polish(row)
        row.activitySymbol.style().unpolish(row.activitySymbol)
        row.activitySymbol.style().polish(row.activitySymbol)
        if state == "success":
            self._expiry[key] = 6.0
            row.activityLifetime.setText("6s")
            row.activityLifetime.show()
            self._notice_timer.start()
        self._position()

    @Slot()
    def _tick_notices(self):
        if self._retired:
            return
        focused = QApplication.focusWidget()
        for key, remaining in tuple(self._expiry.items()):
            row = self.rows[key]
            if row.underMouse() or (focused is not None and (focused is row or row.isAncestorOf(focused))):
                continue
            remaining -= 0.1
            if remaining <= 0:
                self.finish(key)
            else:
                self._expiry[key] = remaining
                row.activityLifetime.setText(f"{math.ceil(remaining)}s")
        if not self._expiry:
            self._notice_timer.stop()

    def finish(self, key):
        if self._retired:
            return
        self._expiry.pop(key, None)
        row = self.rows.pop(key, None)
        if row is not None:
            self.form.activityRowsLayout.removeWidget(row)
            row.hide()
            row.deleteLater()
        if not self._expiry:
            self._notice_timer.stop()
        self._position()


def activity_strip(parent):
    return getattr(parent.window(), "_activity_strip", None)

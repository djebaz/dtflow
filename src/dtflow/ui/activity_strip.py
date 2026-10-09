"""One main-window overlay for labelled concurrent progress; no workflow ownership."""

from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QTimer
from PySide6.QtUiTools import QUiLoader


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
        self.rows = {}
        self.form.hide()
        self.host.installEventFilter(self)
        window._activity_strip = self

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            self._position()
        return False

    def _position(self):
        if not self.rows:
            self.form.hide()
            return
        width = max(1, self.host.width() - 16)
        height = self.form.sizeHint().height()
        details = self.window._inline_feedback.form
        # Anchor above the separate details/editor panel so its Cancel/Add controls
        # stay clickable during unrelated concurrent background work.
        reserved = details.height() + self.window.rootLayout.spacing() if details.isVisible() else 0
        self.form.setGeometry(8, max(0, self.host.height() - reserved - height - 8), width, height)
        self.form.show()
        self.form.raise_()

    def start(self, key, label, stop=None, stop_text="Stop after current image", total=0):
        self.finish(key)
        row = _load("activity_progress_row.ui", self.form)
        row.activityLabel.setText(label)
        row.activityProgress.setAccessibleName(label + " progress")
        row.activityProgress.setRange(0, total)
        row.activityStopButton.setVisible(stop is not None)
        if stop is not None:
            row.activityStopButton.setText(stop_text)

            def request_stop():
                row.activityStopButton.setEnabled(False)
                row.activityLabel.setText(label + " · stopping")
                stop()

            row.activityStopButton.clicked.connect(request_stop)
        self.rows[key] = row
        self.form.activityRowsLayout.addWidget(row)
        self._position()
        QTimer.singleShot(0, self._position)

    def update(self, key, done, total):
        row = self.rows.get(key)
        if row is not None:
            row.activityProgress.setRange(0, max(1, total) if total else 0)
            row.activityProgress.setValue(done)

    def finish(self, key):
        row = self.rows.pop(key, None)
        if row is not None:
            self.form.activityRowsLayout.removeWidget(row)
            row.deleteLater()
        self._position()


def activity_strip(parent):
    return getattr(parent.window(), "_activity_strip", None)

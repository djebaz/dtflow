"""Centered, delayed Qt ring for gallery reads with no committed visible cards."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from dtflow.ui.theme import load_tokens

SHOW_DELAY_MS = 180
SPIN_INTERVAL_MS = 35


class _SpinningRing(QWidget):
    """Paint an indeterminate arc using tracked theme colors and bounded Qt timers."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        colors = load_tokens()
        self._track = QColor(colors["color.sectionBorder"])
        self._accent = QColor(colors["color.accentHover"])
        self.angle = 0
        self.setFixedSize(36, 36)
        self.timer = QTimer(self)
        self.timer.setInterval(SPIN_INTERVAL_MS)
        self.timer.timeout.connect(self._advance)
        self.setAccessibleName("Loading")

    def _advance(self) -> None:
        self.angle = (self.angle + 12) % 360
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4.5, 4.5, 27, 27)
        pen = QPen(self._track, 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        pen.setColor(self._accent)
        painter.setPen(pen)
        painter.drawArc(rect, self.angle * 16, 108 * 16)
        painter.end()


class GalleryCanvasLoading(QObject):
    """One Designer-owned overlay on the browsing stack, shared by all modes.

    Loading is delayed to avoid flicker; animation never runs while hidden.
    The caller controls state from its existing async query lifecycle.
    """

    def __init__(self, stack: QWidget, *, on_retry: Callable[[], None]) -> None:
        super().__init__(stack)
        self.stack = stack
        source = QFile(str(Path(__file__).with_name("forms") / "gallery_loading.ui"))
        if not source.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError("Gallery loading form unavailable.")
        try:
            self.form = QUiLoader().load(source, stack)
        finally:
            source.close()
        if self.form is None:
            raise OSError("Gallery loading form could not be loaded.")
        self.form.hide()
        self.form.setGeometry(stack.rect())
        self.status = self.form.findChild(QLabel, "galleryLoadingLabel")
        self.retry = self.form.findChild(QPushButton, "galleryLoadingRetryButton")
        host = self.form.findChild(QWidget, "galleryLoadingRingHost")
        if self.status is None or self.retry is None or host is None or host.layout() is None:
            raise ValueError("Gallery loading Designer form is incomplete.")
        self.ring = _SpinningRing(host)
        host.layout().addWidget(self.ring)
        self._ring_host = host
        self.retry.clicked.connect(on_retry)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(SHOW_DELAY_MS)
        self.delay.timeout.connect(self._reveal)
        self.state = "idle"
        stack.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.stack and event.type() == QEvent.Type.Resize:
            self.form.setGeometry(self.stack.rect())
        return False

    def ensure_front(self) -> None:
        if self.form.isVisible():
            self.form.setGeometry(self.stack.rect())
            self.form.raise_()

    def loading(self, *, immediate: bool = False, text: str = "Loading images…") -> None:
        self.status.setText(text)
        self.status.setProperty("state", "loading")
        self.retry.hide()
        self._ring_host.show()
        if self.state != "loading":
            self.state = "loading"
            self.form.hide()
            self.ring.timer.stop()
            self.delay.start()
        if immediate:
            self.delay.stop()
            self._reveal()

    def _reveal(self) -> None:
        if self.state != "loading":
            return
        self.form.setGeometry(self.stack.rect())
        self.form.show()
        self.form.raise_()
        self.ring.timer.start()

    def failed(self) -> None:
        self.state = "error"
        self.delay.stop()
        self.ring.timer.stop()
        self._ring_host.hide()
        self.status.setText("Saved images unavailable")
        self.status.setProperty("state", "error")
        self.retry.show()
        self.form.setGeometry(self.stack.rect())
        self.form.show()
        self.form.raise_()

    def clear(self) -> None:
        self.state = "idle"
        self.delay.stop()
        self.ring.timer.stop()
        self.form.hide()

"""One visible-only presentation clock; never polls providers or owns job state."""

from time import monotonic
from weakref import WeakSet

from PySide6.QtCore import QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QApplication, QProgressBar
from shiboken6 import isValid

from dtflow.ui.theme import load_tokens

ANIMATION_INTERVAL_MS = 40  # 25 frames per second while an animated bar is visible.
STRIPE_PITCH_PX = 16
STRIPE_HALF_PITCH_PX = STRIPE_PITCH_PX // 2
STRIPE_SPEED_PX_PER_SECOND = 24


class _Clock(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.widgets = WeakSet()
        self.timer = QTimer(self)
        self.timer.setInterval(ANIMATION_INTERVAL_MS)
        self.timer.timeout.connect(self.tick)
        parent.aboutToQuit.connect(self.timer.stop)

    def watch(self, widget):
        self.widgets.add(widget)
        if widget.isVisible() and widget.stripes_active() and not self.timer.isActive():
            self.timer.start()

    def tick(self):
        active = False
        for widget in tuple(self.widgets):
            if isValid(widget) and widget.isVisible() and widget.stripes_active():
                widget.update()
                active = True
        if not active:
            self.timer.stop()


def watch(widget: QProgressBar) -> None:
    app = QApplication.instance()
    if app is None or not isValid(widget):
        return
    clock = getattr(app, "_dtflow_progress_clock", None)
    if clock is None:
        clock = app._dtflow_progress_clock = _Clock(app)
    clock.watch(widget)


def paint_stripes(painter: QPainter, width: int, height: int, fill: QColor, stripe: QColor) -> None:
    offset = (monotonic() * STRIPE_SPEED_PX_PER_SECOND) % STRIPE_PITCH_PX
    painter.setPen(Qt.PenStyle.NoPen)
    for origin in range(-height - 2 * STRIPE_PITCH_PX, width + STRIPE_PITCH_PX, STRIPE_PITCH_PX):
        for delta, tone in ((0, fill), (STRIPE_HALF_PITCH_PX, stripe)):
            x = origin + delta + offset
            painter.setBrush(tone)
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(x, height),
                        QPointF(x + STRIPE_HALF_PITCH_PX, height),
                        QPointF(x + STRIPE_HALF_PITCH_PX + height, 0),
                        QPointF(x + height, 0),
                    ]
                )
            )


class AnimatedProgressBar(QProgressBar):
    """Use the same stripes in the overlay fallback as in the docked activity bar."""

    def stripes_active(self):
        return self.minimum() == self.maximum() == 0

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        watch(self)

    def showEvent(self, event):
        super().showEvent(event)
        watch(self)

    def paintEvent(self, event):
        if not self.stripes_active():
            return super().paintEvent(event)
        colors = load_tokens()
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        paint_stripes(
            painter,
            self.width(),
            self.height(),
            QColor(colors["color.selectionBorder"]),
            QColor(colors["color.selectionHover"]),
        )
        painter.end()

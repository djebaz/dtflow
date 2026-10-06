"""Compact segmented execution-state control for the persistent DTFlow header."""

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QAbstractButton

from dtflow.ui.theme import load_tokens

_PHASES = {"idle", "ready", "running", "paused", "complete", "error"}


class RunControl(QAbstractButton):
    """Render global execution state without owning generation or queue behavior."""

    SEGMENTS = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Execution control")
        self._phase = "idle"
        self._action: str | None = None
        self._completed: int | None = None
        self._total: int | None = None
        self._rotation = 0

        tokens = load_tokens()
        self._colors = {
            "idle": QColor(tokens["color.borderStrong"]),
            "ready": QColor(tokens["color.secondary"]),
            "running": QColor(tokens["color.accentHover"]),
            "paused": QColor(tokens["color.warning"]),
            "complete": QColor(tokens["color.success"]),
            "error": QColor(tokens["color.error"]),
            "focus": QColor(tokens["color.focus"]),
            "text": QColor(tokens["color.text"]),
            "disabled": QColor(tokens["color.disabled"]),
        }

        self._timer = QTimer(self)
        self._timer.setInterval(110)
        self._timer.timeout.connect(self._advance)
        self.set_presentation("idle", detail="No runnable queue selected.")

    def set_presentation(
        self,
        phase: str,
        *,
        action: str | None = None,
        completed: int | None = None,
        total: int | None = None,
        detail: str = "",
    ) -> None:
        """Apply a plain presentation snapshot supplied by the shared UI activity state."""
        self._phase = phase if phase in _PHASES else "idle"
        self._action = action
        self._completed = completed if type(completed) is int and completed >= 0 else None
        self._total = total if type(total) is int and total > 0 else None
        if self._completed is not None and self._total is not None:
            self._completed = min(self._completed, self._total)

        if self._phase == "running":
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._rotation = 0

        hints = {"start": "Click to start.", "pause": "Click to pause.", "resume": "Click to resume."}
        tooltip = " ".join(part for part in (detail.strip(), hints.get(action, "")) if part)
        self.setToolTip(tooltip)
        self.setAccessibleDescription(tooltip)
        self.setEnabled(action is not None)
        self.setCursor(Qt.CursorShape.PointingHandCursor if action is not None else Qt.CursorShape.ArrowCursor)
        self.update()

    def _advance(self) -> None:
        self._rotation = (self._rotation + 1) % self.SEGMENTS
        self.update()

    @staticmethod
    def _draw_play(painter: QPainter, color: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(
            QPolygonF(
                (
                    QPointF(12.0, 9.5),
                    QPointF(12.0, 20.5),
                    QPointF(20.0, 15.0),
                )
            )
        )

    @staticmethod
    def _draw_pause(painter: QPainter, color: QColor) -> None:
        pen = QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(12.5, 11.0), QPointF(12.5, 19.0))
        painter.drawLine(QPointF(17.5, 11.0), QPointF(17.5, 19.0))

    def _draw_center(self, painter: QPainter) -> None:
        color = self._colors["text"] if self._phase != "idle" else self._colors["disabled"]
        if self._phase in ("ready", "paused", "idle"):
            self._draw_play(painter, color)
            return
        if self._phase == "complete":
            pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(10.5, 15.0), QPointF(13.5, 18.0))
            painter.drawLine(QPointF(13.5, 18.0), QPointF(20.0, 11.5))
            return
        if self._phase == "error":
            font = painter.font()
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "!")
            return
        if self._completed is None:
            self._draw_pause(painter, color)
            return
        font = painter.font()
        font.setPixelSize(10 if self._completed < 100 else 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self._completed))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        ring = QRectF(3.5, 3.5, 23.0, 23.0)
        base = self._colors[self._phase]
        if self._phase == "complete":
            # Brief white glow while the control shows it is finishing.
            glow = QColor(self._colors["text"])
            gradient = QRadialGradient(QPointF(15.0, 15.0), 15.0)
            glow.setAlpha(110)
            gradient.setColorAt(0.55, glow)
            glow.setAlpha(0)
            gradient.setColorAt(1.0, glow)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(QRectF(0.0, 0.0, 30.0, 30.0))
            base = self._colors["text"]
        for index in range(self.SEGMENTS):
            color = QColor(base)
            if self._phase == "running":
                color.setAlpha(235 if index == 0 else 125)
            elif self.underMouse() and self._action is not None:
                color = self._colors["focus"]
            pen = QPen(color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            start = (90 - (index + self._rotation) * (360 / self.SEGMENTS)) * 16
            painter.drawArc(ring, round(start), -18 * 16)

        self._draw_center(painter)

        if self.hasFocus():
            pen = QPen(self._colors["focus"], 1.0, Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(1.0, 1.0, 28.0, 28.0))

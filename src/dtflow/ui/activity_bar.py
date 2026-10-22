"""One painted, single-line job strip for the middle of the status bar (no persistence or provider access)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from dtflow.ui.theme import load_tokens

BAR_HEIGHT = 20
STRIP_HEIGHT = 18
TIER_S = 520
TIER_L = 760
PAD = 6


@dataclass(frozen=True, slots=True)
class BarModel:
    """What the bar shows; the activity strip derives it from its keyed rows."""

    kind: str  # running | success | stopped
    title: str
    count: str = ""
    stage: str = ""
    fraction: float | None = None  # None: unknown progress, striped fill
    elapsed: str = ""
    eta: str = ""
    stopping: bool = False
    lifetime: float | None = None  # remaining fraction of the result notice, 0..1
    tooltip: str = ""
    stop_label: str = ""  # what Stop does, from the job ("Stop waiting locally", "Cancel export", ...)
    stop_visible: bool = True


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class _Strip(QWidget):
    """The progress bar itself: fill, then title, count, clipped stage text, elapsed and ETA."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        colors = load_tokens()
        self._track = QColor(colors["color.barTrack"])
        self._border = QColor(colors["color.border"])
        self._text = QColor(colors["color.title"])
        self._secondary = QColor(colors["color.secondary"])
        accent = QColor(colors["color.accent"])
        accent.setAlphaF(0.55)
        self._edges = {"success": QColor(colors["color.success"]), "stopped": QColor(colors["color.warning"])}
        self._fills = {"running": accent}
        for kind, tone in self._edges.items():
            # The result tone at 22 % over the track keeps text contrast on both sides.
            self._fills[kind] = QColor(
                *(round(0.22 * t + 0.78 * b) for t, b in zip(tone.getRgb()[:3], self._track.getRgb()[:3], strict=True))
            )
        self.model: BarModel | None = None
        self.setFixedHeight(STRIP_HEIGHT)

    def paintEvent(self, event) -> None:
        model = self.model
        if model is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        outer = QRectF(0.5, 0.5, width - 1, height - 1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._track)
        painter.drawRoundedRect(outer, 3, 3)
        fill = QColor(self._fills[model.kind])
        if model.stopping:
            fill.setAlphaF(fill.alphaF() * 0.5)
        painter.save()
        painter.setClipRect(QRectF(1, 1, width - 2, height - 2))
        if model.kind != "running":
            fraction = 1.0
        else:
            fraction = 1.0 if model.fraction is None else max(0.0, min(1.0, model.fraction))
        painter.fillRect(QRectF(1, 1, (width - 2) * fraction, height - 2), fill)
        if model.kind == "running" and model.fraction is None:
            stripe = QColor(self._track)
            stripe.setAlphaF(0.35)
            painter.setPen(QPen(stripe, 3))
            for x in range(-height, width, 12):
                painter.drawLine(x, height, x + height, 0)
        painter.restore()
        if model.lifetime is not None:
            line = QColor(self._edges.get(model.kind, self._text))
            line.setAlphaF(0.8)
            painter.fillRect(QRectF(1, height - 3, (width - 2) * max(0.0, min(1.0, model.lifetime)), 2), line)
        painter.setPen(QPen(self._edges.get(model.kind, self._border), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(outer, 3, 3)
        self._paint_text(painter, model, width, height)
        painter.end()

    def _paint_text(self, painter: QPainter, model: BarModel, width: int, height: int) -> None:
        font = self.font()
        font.setPointSizeF(max(7.5, font.pointSizeF() - 0.5) if font.pointSizeF() > 0 else 8)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        bold = painter.font()
        bold.setBold(True)
        parent_width = self.parentWidget().width() if self.parentWidget() is not None else width
        title = model.title if parent_width >= TIER_L else model.title.split(" · ")[0]
        symbol = {"success": "✓ ", "stopped": "■ "}.get(model.kind, "")
        left = symbol + title
        count_width = 0
        left_width = metrics.horizontalAdvance(left)
        if model.count:
            count_width = QFontMetrics(bold).horizontalAdvance(model.count) + 8
        used = PAD + left_width + count_width
        # Truncation order: stage text clips first, then ETA drops, then elapsed; title, count and Stop never drop.
        times_options = [t for t in (f"{model.elapsed} · {model.eta}" if model.eta else "", model.elapsed, "") if t]
        times = ""
        for option in [*times_options, ""]:
            if used + metrics.horizontalAdvance(option) + 2 * PAD <= width or not option:
                times = option
                break
        times_width = metrics.horizontalAdvance(times) + PAD if times else 0
        middle = width - used - times_width - PAD - 8
        middle_text = metrics.elidedText(model.stage, Qt.TextElideMode.ElideRight, middle) if middle >= 24 else ""
        y = QRectF(0, 0, width, height)
        painter.setPen(self._text)
        x = PAD
        painter.drawText(QRectF(x, 0, left_width + 2, height), Qt.AlignmentFlag.AlignVCenter, left)
        x += left_width + 8
        if model.count:
            painter.setFont(bold)
            painter.drawText(QRectF(x, 0, count_width, height), Qt.AlignmentFlag.AlignVCenter, model.count)
            painter.setFont(font)
            x += count_width
        if middle_text:
            painter.drawText(QRectF(x, 0, middle, height), Qt.AlignmentFlag.AlignVCenter, middle_text)
        if times:
            painter.setPen(self._secondary if model.kind == "running" else self._text)
            painter.drawText(
                QRectF(0, 0, width - PAD, y.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                times,
            )


class ActivityBar(QWidget):
    """Strip plus one text button (Stop while running, × on a routine ending), 20 px high."""

    stop_requested = Signal()
    dismiss_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("activityBar")
        self.setFixedHeight(BAR_HEIGHT)
        self.strip = _Strip(self)
        self.button = QPushButton("Stop", self)
        self.button.setObjectName("activityBarButton")
        self.button.setFixedHeight(STRIP_HEIGHT)
        # A mouse click must not leave focus on the button: a focused bar pauses the result countdown.
        self.button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.button.clicked.connect(self._clicked)
        self._running = True
        self._visible_button = True
        self.hide()

    def _clicked(self) -> None:
        (self.stop_requested if self._running else self.dismiss_requested).emit()

    def present(self, model: BarModel, *, stop_enabled: bool = True) -> None:
        self._visible_button = model.stop_visible or model.kind != "running"
        self.strip.model = model
        self._running = model.kind == "running"
        self.button.setText("Stop" if self._running else "×")
        self.button.setEnabled(stop_enabled if self._running else True)
        label = model.tooltip or f"{model.title} {model.count} {model.stage}".strip()
        self.setToolTip(label)
        self.strip.setAccessibleName(label)
        self.button.setAccessibleName(
            (model.stop_label or f"Stop {model.title}") if self._running else f"Dismiss {model.title} result"
        )
        self.button.setVisible(self._visible_button)
        self.button.setToolTip(self.button.accessibleName())
        self._layout()
        self.strip.update()

    def _layout(self) -> None:
        button_width = max(self.button.sizeHint().width(), 36) if self._running else 22
        self.button.setFixedWidth(button_width)
        top = (self.height() - STRIP_HEIGHT) // 2
        self.button.move(self.width() - button_width, top)
        reserved = button_width + 8 if self._visible_button else 0
        self.strip.setGeometry(0, top, max(0, self.width() - reserved), STRIP_HEIGHT)

    def resizeEvent(self, event) -> None:
        self._layout()
        super().resizeEvent(event)

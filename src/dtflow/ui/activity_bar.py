"""One painted, single-line job strip for the middle of the status bar (no persistence or provider access)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QPushButton, QWidget

from dtflow.ui.theme import load_tokens
from dtflow.ui.progress_animation import paint_stripes, watch

BAR_HEIGHT = 20
STRIP_HEIGHT = 18
TIER_S = 520
TIER_L = 760
PAD = 6
# Approved queue colours for the aggregate bar; the strip assigns one per active queue by stable queue id.
QUEUE_PALETTE = ("#285D84", "#3A5F8A", "#4B5D90", "#5A5696", "#674B96", "#74488F")


@dataclass(frozen=True, slots=True)
class BarModel:
    """What the bar shows; the activity strip derives it from its keyed rows."""

    kind: str  # running | success | stopped
    title: str
    count: str = ""
    queued: int = 0
    stage: str = ""
    wide: str = ""  # the longer stage text shown at the L tier (full title width)
    fraction: float | None = None  # None: unknown progress, striped fill
    elapsed: str = ""
    eta: str = ""
    stopping: bool = False
    lifetime: float | None = None  # remaining fraction of the result notice, 0..1
    tooltip: str = ""
    stop_label: str = ""  # what Stop does, from the job ("Stop waiting locally", "Cancel export", ...)
    stop_visible: bool = True
    stop_action: str = "Stop"  # concise visible action, distinct from its explanatory tooltip
    # Aggregate bar: adjacent queue segments as (end fraction of the track, colour) and the candidate texts richest
    # first; the painter picks the first that fits the real text rectangle.
    segments: tuple[tuple[float, str], ...] = ()
    texts: tuple[str, ...] = ()


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
        self._text = QColor(colors["color.text"])
        self._secondary = QColor(colors["color.secondary"])
        self._stripe = QColor(colors["color.selectionHover"])
        self._chip = QColor(self._text)
        self._chip.setAlphaF(0.14)
        self._edges = {"success": QColor(colors["color.success"]), "stopped": QColor(colors["color.warning"])}
        self._fills = {"running": QColor(colors["color.selectionBorder"])}
        for kind, tone in self._edges.items():
            # The result tone at 22 % over the track keeps text contrast on both sides.
            self._fills[kind] = QColor(
                *(round(0.22 * t + 0.78 * b) for t, b in zip(tone.getRgb()[:3], self._track.getRgb()[:3], strict=True))
            )
        self.model: BarModel | None = None
        self.shown_text = ""  # the aggregate candidate chosen by the last paint
        self.setFixedHeight(STRIP_HEIGHT)

    def stripes_active(self):
        return self.model is not None and self.model.kind == "running" and self.model.fraction is None

    def showEvent(self, event):
        super().showEvent(event)
        watch(self)

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
        if model.kind == "running" and model.fraction is None:
            # Unknown progress alternates the two opaque selection tones; dim both when stopping.
            base = QColor(self._stripe)
            if model.stopping:
                base.setAlphaF(0.5)
            painter.setPen(Qt.PenStyle.NoPen)
            # Paint disjoint bands so 50 % stopping opacity does not compound over another band.
            paint_stripes(painter, width, height, fill, base)
        elif model.segments and model.kind == "running":
            start = 0.0
            for end, colour in model.segments:
                end = max(start, min(1.0, end))
                tone = QColor(colour)
                if model.stopping:
                    tone.setAlphaF(0.5)
                painter.fillRect(QRectF(1 + (width - 2) * start, 1, (width - 2) * (end - start), height - 2), tone)
                start = end
        else:
            painter.fillRect(QRectF(1, 1, (width - 2) * fraction, height - 2), fill)
        painter.restore()
        if model.lifetime is not None:
            line = QColor(self._edges.get(model.kind, self._text))
            line.setAlphaF(0.8)
            painter.fillRect(QRectF(1, height - 2, (width - 2) * max(0.0, min(1.0, model.lifetime)), 2), line)
        edge = QColor(self._edges.get(model.kind, self._border))
        if model.kind != "running":
            edge.setAlphaF(0.7)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(outer, 3, 3)
        self._paint_text(painter, model, width, height)
        painter.end()

    def _paint_text(self, painter: QPainter, model: BarModel, width: int, height: int) -> None:
        font = self.font()
        font.setPointSizeF(9)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        if model.texts:
            self._paint_aggregate(painter, model, metrics, width, height)
            return
        bold = QFont(font)
        bold.setBold(True)
        wide = self.parentWidget() is not None and self.parentWidget().width() >= TIER_L
        title = model.title if wide else model.title.split(" · ")[0]
        stage = (model.wide or model.stage) if wide else model.stage
        x = PAD
        if model.kind != "running":
            # Result symbol: bold, tone colour, in its own 12 px cell.
            painter.setFont(bold)
            painter.setPen(self._edges[model.kind])
            symbol = {"success": "✓", "stopped": "■"}[model.kind]
            painter.drawText(QRectF(x, 0, 12, height), int(Qt.AlignmentFlag.AlignCenter), symbol)
            painter.setFont(font)
            x += 12 + 8
        title_color = QColor(self._text)
        title_color.setAlphaF(0.8)
        title_width = metrics.horizontalAdvance(title)
        count_width = QFontMetrics(bold).horizontalAdvance(model.count) if model.count else 0
        queued_text = f"+{model.queued} queued" if model.kind == "running" and model.queued > 0 else ""
        chip_width = metrics.horizontalAdvance(queued_text) + 10 if queued_text else 0
        used = x + title_width + (8 + count_width if model.count else 0)
        used += 8 + chip_width if queued_text else 0
        # Stage clips first; retain the chip, then drop ETA and elapsed. Title/count/Stop never drop.
        times_options = [t for t in (f"{model.elapsed} · {model.eta}" if model.eta else "", model.elapsed) if t]
        times = ""
        for option in times_options:
            if used + metrics.horizontalAdvance(option) + 2 * PAD <= width:
                times = option
                break
        times_width = metrics.horizontalAdvance(times) + PAD if times else 0
        middle = width - used - 8 - times_width - PAD
        stage_text = metrics.elidedText(stage, Qt.TextElideMode.ElideRight, middle) if middle >= 24 and stage else ""
        painter.setPen(title_color)
        painter.drawText(QRectF(x, 0, title_width + 2, height), int(Qt.AlignmentFlag.AlignVCenter), title)
        x += title_width
        if model.count:
            x += 8
            painter.setFont(bold)
            painter.setPen(self._text)
            painter.drawText(QRectF(x, 0, count_width + 2, height), int(Qt.AlignmentFlag.AlignVCenter), model.count)
            painter.setFont(font)
            x += count_width
        if queued_text:
            x += 8
            chip_rect = QRectF(x, (height - 14) / 2, chip_width, 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._chip)
            painter.drawRoundedRect(chip_rect, 3, 3)
            painter.setPen(self._text)
            painter.drawText(chip_rect, int(Qt.AlignmentFlag.AlignCenter), queued_text)
            x += chip_width
        if stage_text:
            stage_color = QColor(self._text)
            stage_color.setAlphaF(0.85)
            painter.setPen(stage_color)
            painter.drawText(QRectF(x + 8, 0, middle, height), int(Qt.AlignmentFlag.AlignVCenter), stage_text)
        if times:
            painter.setPen(self._secondary if model.kind != "running" else self._text)
            painter.drawText(
                QRectF(0, 0, width - PAD, height),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                times,
            )

    def aggregate_text(self, model: BarModel, width: int | None = None) -> str:
        """The richest candidate whose measured advance fits the text rectangle; the last always shows."""
        font = self.font()
        font.setPointSizeF(9)
        room = (self.width() if width is None else width) - 2 * PAD
        metrics = QFontMetrics(font)
        for text in model.texts:
            if metrics.horizontalAdvance(text) <= room:
                return text
        return model.texts[-1]

    def _paint_aggregate(self, painter, model, metrics, width: int, height: int) -> None:
        text = self.aggregate_text(model, width)
        painter.setPen(self._text)
        # The numbers are never elided: when even the last candidate is too wide it is clipped by the strip edge.
        painter.drawText(QRectF(PAD, 0, width - 2 * PAD, height), int(Qt.AlignmentFlag.AlignVCenter), text)
        self.shown_text = text


class ActivityBar(QWidget):
    """Strip plus one text button (Stop while running, × on a routine ending), 20 px high."""

    stop_requested = Signal()
    dismiss_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("activityBar")
        self.setFixedHeight(BAR_HEIGHT)
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self.opacity_effect)
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

    def present(self, model: BarModel, *, stop_enabled: bool = True, opacity: float = 1.0) -> None:
        self.opacity_effect.setOpacity(opacity)
        self._visible_button = model.stop_visible or model.kind != "running"
        self.strip.model = model
        watch(self.strip)
        self._running = model.kind == "running"
        self.button.setText(model.stop_action if self._running else "×")
        if self.button.property("result") != (not self._running):
            self.button.setProperty("result", not self._running)  # QSS drops the side padding for ×
            self.button.style().unpolish(self.button)
            self.button.style().polish(self.button)
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

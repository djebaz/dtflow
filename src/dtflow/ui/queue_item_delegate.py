"""Painted cells of the queue items table: thumbnail, wrapped prompt, LoRA gauges and parameters.

Presentation only. Hover text comes from the model's ToolTipRole, so nothing here can intercept a row click, and no
cell offers a Copy or click action. Thumbnails are read from already saved local outputs; nothing is generated.
"""

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QTextLayout, QTextOption
from PySide6.QtWidgets import QHeaderView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from dtflow.ui.queue_model import ROW_ROLE, stopwatch_icon
from dtflow.ui.queue_presenter import display_name, lora_fraction
from dtflow.ui.theme import load_tokens

_TOKENS = load_tokens()
ROW_HEIGHT = 76
THUMB = 64
MAX_LORAS = 3
LORA_LINE = 17
GAUGE_WIDTH = 40
WEIGHT_WIDTH = 30
COL_PREVIEW, COL_PROMPT, COL_MODEL, COL_LORAS, COL_PARAMS, COL_STATUS, COL_DURATION = range(7)
PREVIEW_WIDTH, STATUS_WIDTH, DURATION_WIDTH = 72, 84, 68
PROMPT_SHARE = 0.30  # target share of the table width (spec: 30-33%)
MIN_MODEL, MIN_LORAS, MIN_PARAMS = 112, 190, 112
ZWSP = "\u200b"


def wrapped_lines(text: str, font: QFont, width: int, max_lines: int, soft_breaks: bool = False) -> list[str]:
    """Natural wrapping across up to max_lines; only an overflowing final line gets an ellipsis, never the first.

    soft_breaks lets a filename wrap after "_", "-" and "." before it is broken mid-word.
    """
    if soft_breaks:
        for mark in "_-.":
            text = text.replace(mark, mark + ZWSP)
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    spans = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, width))
        spans.append((line.textStart(), line.textLength()))
    layout.endLayout()
    metrics = QFontMetrics(font)
    lines = []
    for position, (start, length) in enumerate(spans[:max_lines]):
        if position == max_lines - 1 and len(spans) > max_lines:
            lines.append(metrics.elidedText(text[start:].replace(ZWSP, "").strip(), Qt.TextElideMode.ElideRight, width))
        else:
            lines.append(text[start : start + length].replace(ZWSP, "").rstrip())
    return lines


class QueueItemDelegate(QStyledItemDelegate):
    """Paints Preview, Prompt, Model, LoRAs and Parameters; Status and duration use the plain text path."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        row = index.data(ROW_ROLE)
        column = index.column()
        if row is None:
            super().paint(painter, option, index)
            return
        # The base style draws the item surface, hover, selection and focus from the shared QSS.
        surface = QStyleOptionViewItem(option)
        self.initStyleOption(surface, index)
        surface.text = ""
        style = option.widget.style() if option.widget else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, surface, painter, option.widget)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        text = QColor(_TOKENS["color.selectionText"] if selected else _TOKENS["color.text"])
        secondary = QColor(_TOKENS["color.secondary"])
        rect = option.rect.adjusted(7, 6, -7, -6)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if column == COL_PREVIEW:
            self._thumb(painter, rect, row, secondary, getattr(index.model(), "thumbnails", None))
        elif column == COL_PROMPT:
            self._prompt(painter, rect, row, option.font, text)
        elif column == COL_MODEL:
            bold = QFont(option.font)
            bold.setWeight(QFont.Weight.Bold)
            self._lines(
                painter,
                rect,
                wrapped_lines(display_name(row.model), bold, rect.width(), 2, soft_breaks=True),
                bold,
                text,
            )
        elif column == COL_LORAS:
            self._loras(painter, rect, row, option.font, text, secondary, getattr(index.model(), "gauge_scale", 1.0))
        elif column == COL_PARAMS:
            self._params(painter, rect, row, option.font, text, secondary)
        elif column == COL_STATUS:
            # Semibold semantic text that keeps its color on the selected row; pending stays neutral gray.
            color = index.data(Qt.ItemDataRole.ForegroundRole)
            font = QFont(option.font)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(color if isinstance(color, QColor) else (secondary if row.level == "secondary" else text))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, row.state_text)
        elif column == COL_DURATION:
            mono = QFont(_TOKENS["type.monoFamily"])
            mono.setPointSizeF(option.font.pointSizeF())
            painter.setFont(mono)
            painter.setPen(text)
            painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, row.duration_text)
        painter.restore()

    @classmethod
    def _prompt(cls, painter, rect, row, font, color) -> None:
        """The wrapped prompt; parts a prompt variation produced get the accent tag background and on-accent text."""
        lines = wrapped_lines(row.prompt, font, rect.width(), 2)
        cls._lines(painter, rect, lines, font, color)
        if not row.prompt_runs:
            return
        metrics = QFontMetrics(font)
        height = metrics.height() + 1
        cursor = 0
        for number, line in enumerate(lines):
            elided = line.endswith("…") and number == len(lines) - 1
            core = line[:-1] if elided else line
            start = row.prompt.find(core, cursor) if core else -1
            if start < 0:
                continue
            cursor = start + len(core)
            for run_start, length in row.prompt_runs:
                low, high = max(run_start, start), min(run_start + length, cursor)
                if low >= high:
                    continue
                x = rect.left() + metrics.horizontalAdvance(core[: low - start])
                piece = core[low - start : high - start]
                tag = QRect(x, rect.top() + number * height, metrics.horizontalAdvance(piece), height)
                painter.fillRect(tag, QColor(_TOKENS["color.accent"]))
                painter.setPen(QColor(_TOKENS["color.onAccent"]))
                painter.drawText(tag, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, piece)

    @staticmethod
    def _lines(painter, rect, lines, font, color) -> None:
        painter.setFont(font)
        painter.setPen(color)
        height = QFontMetrics(font).height() + 1
        for number, line in enumerate(lines):
            painter.drawText(
                QRect(rect.left(), rect.top() + number * height, rect.width(), height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )

    @staticmethod
    def _thumb(painter, rect, row, secondary, provider) -> None:
        area = QRect(rect.left(), rect.top(), THUMB, THUMB)
        painter.setPen(QColor(_TOKENS["color.sectionBorder"]))
        painter.setBrush(QColor(_TOKENS["color.canvas"]))
        painter.drawRoundedRect(QRectF(area).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        pixmap = (
            provider.pixmap(row.outputs[0], identity=row.gallery_identity)
            if provider is not None and row.outputs
            else None
        )
        if (
            pixmap is None
            and row.outputs
            and provider is not None
            and not provider.is_failed(row.outputs[0], identity=row.gallery_identity)
        ):
            return  # decoding in the background: a neutral tile, never misleading text
        if pixmap is None:
            small = QFont(painter.font())
            small.setPointSizeF(max(7.5, small.pointSizeF() - 1))
            painter.setFont(small)
            painter.setPen(QColor(_TOKENS["color.disabled"]))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "no image" if not row.outputs else "unavailable")
            return
        # The pixmap is already a centred square crop of the gallery's 600 px thumbnail: fill the tile edge to edge.
        outline = QRectF(area).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = QPainterPath()
        shape.addRoundedRect(outline, 4, 4)
        painter.save()
        painter.setClipPath(shape)
        painter.drawPixmap(area, pixmap)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(outline, 4, 4)

    @staticmethod
    def _loras(painter, rect, row, font, text, secondary, scale) -> None:
        if not row.loras:
            painter.setFont(font)
            painter.setPen(secondary)
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "\u2014")
            return
        small = QFont(font)
        small.setPointSizeF(max(7.5, font.pointSizeF() - 1))
        name_font = QFont(small)
        name_font.setWeight(QFont.Weight.DemiBold)
        mono = QFont(_TOKENS["type.monoFamily"])
        mono.setPointSizeF(small.pointSizeF())
        name_metrics = QFontMetrics(name_font)
        # Gauge and weight sit on fixed columns at the right edge, so a long name only ever shortens itself.
        weight_left = rect.right() - WEIGHT_WIDTH
        gauge_left = weight_left - 6 - GAUGE_WIDTH
        for number, (name, weight) in enumerate(row.loras[:MAX_LORAS]):
            top = rect.top() + number * LORA_LINE
            painter.setFont(name_font)
            painter.setPen(text)
            painter.drawText(
                QRect(rect.left(), top, gauge_left - 6 - rect.left(), LORA_LINE),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                name_metrics.elidedText(display_name(name), Qt.TextElideMode.ElideRight, gauge_left - 6 - rect.left()),
            )
            track = QRectF(gauge_left, top + (LORA_LINE - 6) / 2, GAUGE_WIDTH, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(_TOKENS["color.barTrack"]))
            painter.drawRoundedRect(track, 3, 3)
            fraction = lora_fraction(weight, scale)
            if fraction:
                painter.setBrush(QColor(_TOKENS["color.accent"]))
                painter.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * fraction, 6), 3, 3)
            painter.setFont(mono)
            painter.setPen(secondary)
            painter.drawText(
                QRect(weight_left, top, WEIGHT_WIDTH, LORA_LINE),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "\u2014" if weight is None else f"{weight:.2f}",
            )

    @staticmethod
    def _params(painter, rect, row, font, text, secondary) -> None:
        bold = QFont(font)
        bold.setWeight(QFont.Weight.Bold)
        small = QFont(font)
        small.setPointSizeF(max(7.5, font.pointSizeF() - 1))
        painter.setFont(bold)
        painter.setPen(text)
        painter.drawText(QRect(rect.left(), rect.top(), rect.width(), 18), Qt.AlignmentFlag.AlignLeft, row.steps_text)
        painter.setFont(small)
        painter.setPen(secondary)
        painter.drawText(
            QRect(rect.left(), rect.top() + 19, rect.width(), 15), Qt.AlignmentFlag.AlignLeft, row.guidance_text
        )
        # A long sampler name takes its own wrapped line(s) instead of being clipped.
        for number, line in enumerate(wrapped_lines(row.sampler_text, small, rect.width(), 2)):
            painter.drawText(
                QRect(rect.left(), rect.top() + 34 + number * 14, rect.width(), 14), Qt.AlignmentFlag.AlignLeft, line
            )


class ItemTableHeader(QHeaderView):
    """Header whose duration section shows only a stopwatch, right-aligned with the values below it."""

    def __init__(self, parent: QTableView):
        super().__init__(Qt.Orientation.Horizontal, parent)

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:
        super().paintSection(painter, rect, logical_index)  # background and (empty) label
        if logical_index == COL_DURATION:
            icon = QRect(rect.right() - 7 - 15, rect.center().y() - 7, 16, 16)  # the cells' 7px right padding
            stopwatch_icon().paint(painter, icon)


class ItemTableLayout(QObject):
    """Column widths for the items table: Prompt takes about a third; Status and duration stay narrow.

    The duration header carries only the stopwatch glyph, so no width is reserved for a text label.
    """

    def __init__(self, view: QTableView):
        super().__init__(view)
        self.view = view
        self._busy = False
        view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize and not self._busy:
            self.fit()
        return False

    def fit(self) -> None:
        total = self.view.viewport().width()
        if total <= 0:
            return
        self._busy = True
        try:
            prompt = max(160, round(total * PROMPT_SHARE))
            rest = max(0, total - PREVIEW_WIDTH - STATUS_WIDTH - DURATION_WIDTH - prompt)
            model = max(MIN_MODEL, round(rest * 0.28))
            loras = max(MIN_LORAS, round(rest * 0.44))
            params = max(MIN_PARAMS, rest - model - loras)
            header = self.view.horizontalHeader()
            widths = (PREVIEW_WIDTH, prompt, model, loras, params, STATUS_WIDTH, DURATION_WIDTH)
            for column, width in enumerate(widths):
                header.resizeSection(column, width)
        finally:
            self._busy = False

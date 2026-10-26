"""Painted queue presentation: the segmented progress bar and the rich queue-list rows. No queue behavior lives here."""

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from dtflow.ui.prompt_tags import elided_without_ellipsis, paint_tag_runs
from dtflow.ui.queue_presenter import QueueEntry
from dtflow.ui.theme import load_tokens

_TOKENS = load_tokens()
LEVEL_COLORS = {
    "ok": _TOKENS["color.success"],
    "warning": _TOKENS["color.warning"],
    "error": _TOKENS["color.error"],
    "unknown": _TOKENS["color.unknown"],
    "accent": _TOKENS["color.accentHover"],
    "secondary": _TOKENS["color.badgeText"],
}
ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1  # QueueEntry for a queue row; a group heading stores its title instead
HEADING_ROLE = Qt.ItemDataRole.UserRole + 2


def paint_segments(painter: QPainter, rect: QRectF, segments, total: int) -> None:
    """A rounded track with one colored run per settled state (and running work); pending stays empty track."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    radius = rect.height() / 2
    painter.setBrush(QColor(_TOKENS["color.barTrack"]))
    painter.drawRoundedRect(rect, radius, radius)
    if total > 0:
        painter.setClipRect(rect)
        x = rect.left()
        for level, count in segments:
            width = rect.width() * count / total
            painter.setBrush(QColor(LEVEL_COLORS.get(level, LEVEL_COLORS["unknown"])))
            painter.drawRect(QRectF(x, rect.top(), width, rect.height()))
            x += width
    painter.restore()


class SegmentedBar(QWidget):
    """Header progress: settled items by outcome over the queue's total, without implying item-level progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(8)
        self.setMaximumHeight(8)
        self._segments: tuple[tuple[str, int], ...] = ()
        self.settled = 0
        self.total = 0

    def set_counts(self, segments: tuple[tuple[str, int], ...], settled: int, total: int) -> None:
        self._segments, self.settled, self.total = tuple(segments), settled, total
        self.setAccessibleDescription(f"{settled} of {total} settled")
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(160, 8)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_segments(painter, QRectF(self.rect()), self._segments, self.total)
        painter.end()


class QueueListDelegate(QStyledItemDelegate):
    """Two-line queue rows (title, then model · plan · backend · time) with a status chip and a mini progress bar;
    group headings are painted as eyebrows."""

    ROW_HEIGHT = 58
    HEADING_HEIGHT = 26

    def sizeHint(self, option, index) -> QSize:
        heading = index.data(HEADING_ROLE)
        return QSize(option.rect.width(), self.HEADING_HEIGHT if heading else self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        heading = index.data(HEADING_ROLE)
        if heading:
            painter.save()
            font = QFont(option.font)
            font.setPointSizeF(8)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(_TOKENS["color.eyebrow"]))
            painter.drawText(
                option.rect.adjusted(4, 6, -4, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, heading
            )
            painter.restore()
            return
        entry: QueueEntry | None = index.data(ENTRY_ROLE)
        if entry is None:
            super().paint(painter, option, index)
            return
        # The base style draws the item surface, hover, selection and focus from the shared QSS.
        surface = QStyleOptionViewItem(option)
        self.initStyleOption(surface, index)
        surface.text = ""
        style = option.widget.style() if option.widget else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, surface, painter, option.widget)
        painter.save()
        rect = option.rect.adjusted(8, 6, -8, -6)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        text = QColor(_TOKENS["color.selectionText"] if selected else _TOKENS["color.text"])
        secondary = QColor(_TOKENS["color.secondary"])

        chip_font = QFont(option.font)
        chip_font.setPointSizeF(8)
        chip_font.setBold(True)
        chip_metrics = QFontMetrics(chip_font)
        chip_width = chip_metrics.horizontalAdvance(entry.chip) + 12
        chip = QRect(rect.right() - chip_width, rect.top(), chip_width, chip_metrics.height() + 2)
        chip_color = QColor(LEVEL_COLORS.get(entry.level, LEVEL_COLORS["unknown"]))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QColor(chip_color)
        border.setAlpha(150)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(chip).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.setFont(chip_font)
        painter.setPen(chip_color)
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, entry.chip)

        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(text)
        title_rect = QRect(rect.left(), rect.top(), chip.left() - rect.left() - 6, chip.height())
        title = QFontMetrics(title_font).elidedText(entry.title, Qt.TextElideMode.ElideRight, title_rect.width())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if entry.title_runs:
            shown = elided_without_ellipsis(entry.title, title_font, title_rect.width())
            paint_tag_runs(painter, title_rect, shown, entry.title_runs, title_font)

        small = QFont(option.font)
        small.setPointSizeF(max(7.5, option.font.pointSizeF() - 1))
        painter.setFont(small)
        painter.setPen(secondary)
        sub_rect = QRect(rect.left(), chip.bottom() + 3, rect.width(), QFontMetrics(small).height())
        subtitle = QFontMetrics(small).elidedText(entry.subtitle, Qt.TextElideMode.ElideRight, sub_rect.width())
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, subtitle)

        count = f"{entry.settled}/{entry.total}"
        count_width = QFontMetrics(small).horizontalAdvance(count) + 6
        bar = QRectF(rect.left(), rect.bottom() - 4, rect.width() - count_width, 4)
        paint_segments(painter, bar, entry.segments, entry.total)
        painter.drawText(
            QRect(rect.right() - count_width, int(bar.top()) - 6, count_width, 14),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            count,
        )
        painter.restore()

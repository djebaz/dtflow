"""Painted, token-driven Logs cells without per-row widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from dtflow.diagnostic_projection import stable_color_index
from dtflow.ui.theme import load_tokens


class LogCellDelegate(QStyledItemDelegate):
    actionRequested = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tokens = load_tokens()
        icons_dir = Path(__file__).parents[1] / "resources" / "images" / "gallery"
        self.action_icons = (QIcon(str(icons_dir / "detail.svg")), QIcon(str(icons_dir / "json.svg")))

    def _color(self, key):
        return QColor(self.tokens[key])

    def _palette(self, key):
        index = stable_color_index(key) + 1
        return self._color(f"color.logPill{index}Bg"), self._color(f"color.logPill{index}Fg")

    def _pill(self, painter, rect, text, key):
        bg, fg = self._palette(key)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(fg)
        painter.drawText(rect.adjusted(7, 0, -7, 0), Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, text)

    @staticmethod
    def _preview(index):
        model = index.model()
        if index.row() >= len(model.previews):
            return (), 0
        preview = model.previews[index.row()]
        return preview if isinstance(preview, tuple) and len(preview) == 2 else ((), 0)

    def _chips(self, painter, rect, metrics, pairs, overflow, *, draw):
        x, y = rect.x(), rect.y() + 5
        width = max(1, rect.width())
        line_height = max(24, metrics.height() + 8)
        for key, value in pairs:
            key_text = metrics.elidedText(str(key), Qt.TextElideMode.ElideRight, 125)
            value_text = metrics.elidedText(str(value), Qt.TextElideMode.ElideRight, 180)
            key_width = metrics.horizontalAdvance(key_text) + 14
            value_width = metrics.horizontalAdvance(value_text) + 10
            item_width = min(width, key_width + value_width)
            if x > rect.x() and x + item_width > rect.right():
                x, y = rect.x(), y + line_height + 3
            if draw:
                chip = QRect(x, y, min(key_width, width), line_height)
                self._pill(painter, chip, key_text, str(key))
                painter.setPen(self._color("color.text"))
                available = max(0, rect.right() - chip.right() - 7)
                painter.drawText(
                    QRect(chip.right() + 5, y, available, line_height),
                    Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
                    value_text,
                )
            x += item_width + 10
        if overflow:
            more = f"+{overflow} more"
            w = metrics.horizontalAdvance(more) + 14
            if x > rect.x() and x + w > rect.right():
                y += line_height + 3
                x = rect.x()
            if draw:
                painter.setPen(self._color("color.secondary"))
                painter.drawText(QRect(x, y, w, line_height), Qt.AlignmentFlag.AlignVCenter, more)
        return y - rect.y() + line_height + 6

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, self._color("color.selection"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, self._color("color.logRowHover"))
        row, col = index.row(), index.column()
        record = index.model().rows[row]
        rect = option.rect.adjusted(5, 3, -5, -3)
        painter.setFont(option.font)
        metrics = QFontMetrics(option.font)
        if col == 0:
            levels = {
                "DEBUG": "color.disabled",
                "INFO": "color.logInfo",
                "WARNING": "color.warning",
                "ERROR": "color.error",
                "CRITICAL": "color.error",
            }
            painter.fillRect(
                QRect(option.rect.x() + 3, option.rect.y() + 4, 4, max(0, option.rect.height() - 8)),
                self._color(levels.get(record["level"], "color.secondary")),
            )
        elif col == 1:
            timestamp = record["ts"]
            painter.setPen(self._color("color.text"))
            painter.drawText(rect.adjusted(2, 4, 0, 0), Qt.AlignmentFlag.AlignTop, timestamp[:10])
            painter.setPen(self._color("color.secondary"))
            painter.drawText(rect.adjusted(2, 25, 0, 0), Qt.AlignmentFlag.AlignTop, timestamp[11:])
        elif col == 2:
            full = record["event"]
            shortened = metrics.elidedText(full, Qt.TextElideMode.ElideRight, max(10, rect.width() - 18))
            self._pill(painter, QRect(rect.x(), rect.y() + 9, rect.width(), 26), shortened, full)
        elif col == 3:
            pairs, overflow = self._preview(index)
            self._chips(painter, rect, metrics, pairs[:6], overflow, draw=True)
        elif col == 4:
            mid = option.rect.x() + option.rect.width() // 2
            for icon, cell in zip(
                self.action_icons,
                (
                    QRect(option.rect.x(), option.rect.y(), mid - option.rect.x(), option.rect.height()),
                    QRect(mid, option.rect.y(), option.rect.right() + 1 - mid, option.rect.height()),
                ),
            ):
                icon_size = QSize(18, 18)
                icon_rect = QRect(
                    cell.center().x() - icon_size.width() // 2,
                    cell.center().y() - icon_size.height() // 2,
                    icon_size.width(),
                    icon_size.height(),
                )
                icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)
        painter.restore()

    def sizeHint(self, option, index):
        if index.column() != 3:
            return QSize(max(0, option.rect.width()), 53)
        pairs, overflow = self._preview(index)
        metrics = QFontMetrics(option.font)
        rect = QRect(0, 0, max(1, option.rect.width() - 10), 53)
        height = self._chips(None, rect, metrics, pairs[:6], overflow, draw=False)
        return QSize(rect.width(), max(53, height + 6))

    def editorEvent(self, event, model, option, index):
        if index.column() != 4 or event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if event.button() != Qt.MouseButton.LeftButton or not option.rect.contains(event.position().toPoint()):
            return False
        mid = option.rect.x() + option.rect.width() // 2
        self.actionRequested.emit(index.row(), "details" if event.position().x() < mid else "copy")
        return True

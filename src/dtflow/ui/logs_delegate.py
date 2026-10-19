"""Painted, bounded DTFlow Logs cells using the shared token palette.

No per-row widgets, arbitrary HTML rendering or duplicated log parsing.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from dtflow.diagnostic_projection import stable_color_index
from dtflow.ui.theme import load_tokens


class LogCellDelegate(QStyledItemDelegate):
    actionRequested = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tokens = load_tokens()
        self._backgrounds = (
            "color.selection", "color.button", "color.sectionHeader",
            "color.logCodeSurface", "color.logRowHover", "color.itemSurface",
            "color.toolbar", "color.selectionHover",
        )
        self._height_cache = {}

    def _color(self, token):
        return QColor(self.tokens[token])

    def _pill(self, painter, rect, text, *, seed, selected=False):
        background = "color.selection" if selected else self._backgrounds[stable_color_index(seed)]
        painter.setBrush(self._color(background))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(self._color("color.selectionText" if selected else "color.text"))
        metrics = QFontMetrics(painter.font())
        painter.drawText(rect.adjusted(7, 0, -7, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
                         metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, rect.width() - 14)))

    def _chips(self, option, index):
        model = index.model()
        if index.row() >= len(model.previews):
            return (), 0
        preview = model.previews[index.row()]
        if isinstance(preview, tuple) and len(preview) == 2:
            return preview
        return (), 0

    def _chip_rows(self, option, index):
        pairs, more = self._chips(option, index)
        metrics = QFontMetrics(option.font)
        x, y = 0, 0
        result = []
        available = max(40, option.rect.width() - 12)
        line = max(metrics.height() + 8, 21)
        for key, value in pairs:
            label_width = min(metrics.horizontalAdvance(str(key)) + 14, available // 2)
            value_width = min(metrics.horizontalAdvance(str(value)) + 10, max(40, available // 2))
            width = min(available, label_width + value_width + 7)
            if x and x + width > available:
                x, y = 0, y + line + 3
            result.append((x, y, width, label_width, key, value))
            x += width + 7
        if more:
            width = min(available, metrics.horizontalAdvance(f"+{more} more") + 14)
            if x and x + width > available:
                x, y = 0, y + line + 3
            result.append((x, y, width, 0, "", f"+{more} more"))
        return result, y + line + 8

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, self._color("color.selection"))
        model = index.model()
        record = model.rows[index.row()]
        rect = option.rect.adjusted(4, 3, -4, -3)
        col = index.column()
        painter.setFont(option.font)
        metrics = QFontMetrics(option.font)
        if col == 0:
            levels = {"DEBUG": "color.disabled", "INFO": "color.logInfo",
                      "WARNING": "color.warning", "ERROR": "color.error",
                      "CRITICAL": "color.error"}
            painter.fillRect(QRect(option.rect.x() + 3, option.rect.y() + 4, 4,
                                   max(0, option.rect.height() - 8)),
                             self._color(levels.get(record["level"], "color.secondary")))
        elif col == 1:
            ts = record["ts"]
            painter.setPen(self._color("color.text"))
            painter.drawText(rect.adjusted(3, 3, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, ts[:10])
            painter.setPen(self._color("color.secondary"))
            painter.drawText(rect.adjusted(3, 24, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, ts[11:])
        elif col == 2:
            self._pill(painter, QRect(rect.x(), rect.y() + 9, rect.width(), 26),
                       record["event"], seed=record["event"], selected=selected)
        elif col == 3:
            chips, _height = self._chip_rows(option, index)
            for x, y, width, label_width, key, value in chips:
                top = rect.y() + y + 2
                if key:
                    painter.setBrush(self._color(self._backgrounds[stable_color_index(key)]))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(QRect(rect.x() + x, top, label_width, 20), 4, 4)
                    painter.setPen(self._color("color.text"))
                    painter.drawText(QRect(rect.x() + x + 5, top, label_width - 8, 20),
                                     Qt.AlignmentFlag.AlignVCenter,
                                     metrics.elidedText(str(key), Qt.TextElideMode.ElideRight, label_width - 9))
                painter.setPen(self._color("color.secondary" if not selected else "color.selectionText"))
                remaining = width - label_width
                painter.drawText(QRect(rect.x() + x + label_width + 3, top, max(1, remaining - 3), 20),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 metrics.elidedText(str(value), Qt.TextElideMode.ElideRight, max(1, remaining - 5)))
        elif col == 4:
            mid = option.rect.x() + option.rect.width() // 2
            painter.setPen(self._color("color.secondary"))
            painter.drawText(QRect(option.rect.x(), option.rect.y(), mid - option.rect.x(),
                                   option.rect.height()), Qt.AlignmentFlag.AlignCenter, "Details")
            painter.drawText(QRect(mid, option.rect.y(), option.rect.right() - mid,
                                   option.rect.height()), Qt.AlignmentFlag.AlignCenter, "Copy")
        painter.restore()

    def sizeHint(self, option, index):
        if index.column() != 3:
            return QSize(option.rect.width(), 54)
        _, height = self._chip_rows(option, index)
        return QSize(option.rect.width(), max(54, height + 8))

    def editorEvent(self, event, model, option, index):
        if index.column() != 4 or event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if event.button() != Qt.MouseButton.LeftButton or not option.rect.contains(event.position().toPoint()):
            return False
        mid = option.rect.x() + option.rect.width() // 2
        self.actionRequested.emit(index.row(), "details" if event.position().x() < mid else "copy")
        return True

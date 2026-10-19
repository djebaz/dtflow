"""Painted DTFlow Logs cells; no per-row QWidget allocation."""
from __future__ import annotations

import hashlib

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from dtflow.ui.theme import load_tokens


class LogCellDelegate(QStyledItemDelegate):
    actionRequested = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors = load_tokens()
        self.pairs = [
            ("color.accent", "color.selectionText"),
            ("color.selection", "color.text"),
            ("color.button", "color.secondary"),
        ]

    def _color(self, key):
        return QColor(self.colors[key])

    def _pill(self, painter, rect, text, font, *, seed):
        index = int.from_bytes(hashlib.blake2s(seed.encode("utf-8"), digest_size=2).digest(), "big") % len(self.pairs)
        background, foreground = self.pairs[index]
        painter.setBrush(self._color(background))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 5, 5)
        painter.setFont(font)
        painter.setPen(self._color(foreground))
        painter.drawText(rect.adjusted(7, 0, -7, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, text)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, self._color("color.selection"))
        record = index.model().rows[index.row()]
        rect = option.rect.adjusted(4, 3, -4, -3)
        column = index.column()
        font = option.font
        metrics = QFontMetrics(font)
        if column == 0:
            levels = {"DEBUG": "color.disabled", "INFO": "color.secondary",
                      "WARNING": "color.warning", "ERROR": "color.error",
                      "CRITICAL": "color.error"}
            painter.fillRect(QRect(option.rect.x() + 3, option.rect.y() + 4, 4,
                                   max(0, option.rect.height() - 8)),
                             self._color(levels.get(record["level"], "color.secondary")))
        elif column == 1:
            ts = record["ts"]
            painter.setPen(self._color("color.text"))
            painter.drawText(rect.adjusted(3, 3, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, ts[:10])
            painter.setPen(self._color("color.secondary"))
            painter.drawText(rect.adjusted(3, 24, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, ts[11:])
        elif column == 2:
            full = record["event"]
            display = metrics.elidedText(full, Qt.TextElideMode.ElideRight, max(10, rect.width() - 20))
            self._pill(painter, QRect(rect.x(), rect.y() + 9, rect.width(), 26), display, font, seed=full)
        elif column == 3:
            # Preview text is bounded and semantically ordered by diagnostic_projection.
            preview = index.model().previews[index.row()] if index.row() < len(index.model().previews) else ""
            painter.setPen(self._color("color.text"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, preview)
        elif column == 4:
            mid = option.rect.x() + option.rect.width() // 2
            painter.setPen(self._color("color.secondary"))
            painter.drawText(QRect(option.rect.x(), option.rect.y(), mid - option.rect.x(),
                                   option.rect.height()), Qt.AlignmentFlag.AlignCenter, "Details")
            painter.drawText(QRect(mid, option.rect.y(), option.rect.right() - mid,
                                   option.rect.height()), Qt.AlignmentFlag.AlignCenter, "Copy")
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 56)

    def editorEvent(self, event, model, option, index):
        if index.column() != 4 or event.type() != event.Type.MouseButtonRelease:
            return False
        if event.button() != Qt.MouseButton.LeftButton or not option.rect.contains(event.position().toPoint()):
            return False
        mid = option.rect.x() + option.rect.width() // 2
        self.actionRequested.emit(index.row(), "details" if event.position().x() < mid else "copy")
        return True

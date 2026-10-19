"""Painted, token-driven Logs cells without per-row widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QHeaderView, QStyle, QStyledItemDelegate, QStyleOptionHeader

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
        # (pairs, overflow) or (pairs, overflow, message) when a record has no characteristics.
        return preview[:2] if isinstance(preview, tuple) and len(preview) in (2, 3) else ((), 0)

    @staticmethod
    def _message(index):
        model = index.model()
        if index.row() >= len(model.previews):
            return ""
        preview = model.previews[index.row()]
        return preview[2] if isinstance(preview, tuple) and len(preview) == 3 else ""

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
            message = self._message(index)
            if message and not pairs:
                painter.setPen(self._color("color.secondary"))
                painter.drawText(
                    rect.adjusted(0, 5, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, message
                )
            else:
                self._chips(painter, rect, metrics, pairs[:6], overflow, draw=True)
        elif col == 4:
            # Quiet at rest, full brightness on row hover or selection.
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
            painter.setOpacity(1.0 if selected or hovered else 0.55)
            mid = option.rect.x() + option.rect.width() // 2
            for icon, cell in zip(
                self.action_icons,
                (
                    QRect(option.rect.x(), option.rect.y(), mid - option.rect.x(), option.rect.height()),
                    QRect(mid, option.rect.y(), option.rect.right() + 1 - mid, option.rect.height()),
                ),
                strict=True,
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
        message = self._message(index)
        if message and not pairs:
            bounds = metrics.boundingRect(rect, Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, message)
            return QSize(rect.width(), max(53, bounds.height() + 16))
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


class LogHeaderView(QHeaderView):
    """Horizontal header whose split section paints two independent sort targets.

    The model returns that section's text as ``"FIRST ↕ . SECOND ↓"``; the view paints
    each part in its own hit area and reports which part a click landed on.
    """

    SPLIT_SEPARATOR = " . "
    # Matches the shared QSS `QHeaderView::section` horizontal padding.
    PADDING = 5

    def __init__(self, split_section, active_part, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.split_section = split_section
        # `active_part()` names the part (0 or 1) that is sorted, or None; the active part is brighter.
        self.active_part = active_part
        self.clicked_part = 0
        self.tokens = load_tokens()

    def _parts(self, rect):
        """Text and hit rectangle of each part of the split section."""
        model = self.model()
        text = model.headerData(self.split_section, Qt.Orientation.Horizontal) if model is not None else ""
        parts = str(text or "").split(self.SPLIT_SEPARATOR, 1)
        if len(parts) != 2:
            return ()
        metrics = self.fontMetrics()
        first = metrics.horizontalAdvance(parts[0])
        gap = metrics.horizontalAdvance(self.SPLIT_SEPARATOR)
        # Each part owns half of the separator, so the hit areas cover the whole section.
        split = rect.x() + self.PADDING + first + gap // 2
        return (
            (parts[0], QRect(rect.x(), rect.y(), split - rect.x(), rect.height()), rect.x() + self.PADDING),
            (parts[1], QRect(split, rect.y(), rect.right() + 1 - split, rect.height()), split + gap - gap // 2),
        )

    def part_at(self, x):
        """Index of the split-section part under viewport x, or 0 elsewhere."""
        position = self.sectionViewportPosition(self.split_section)
        rect = QRect(position, 0, self.sectionSize(self.split_section), self.height())
        for number, (_text, hit, _start) in enumerate(self._parts(rect)):
            if hit.left() <= x <= hit.right():
                return number
        return 0

    def mousePressEvent(self, event):
        # sectionClicked follows on release over the same section; remember which part was pressed.
        self.clicked_part = self.part_at(event.position().toPoint().x())
        super().mousePressEvent(event)

    def paintSection(self, painter, rect, logical_index):
        parts = self._parts(rect) if logical_index == self.split_section else ()
        if not parts:
            super().paintSection(painter, rect, logical_index)
            return
        painter.save()
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect, option.section, option.text = rect, logical_index, ""
        option.position = QStyleOptionHeader.SectionPosition.Middle
        # Keep the hover and pressed feedback the base paintSection gives the other sections.
        cursor = self.viewport().mapFromGlobal(QCursor.pos())
        if self.viewport().rect().contains(cursor) and self.logicalIndexAt(cursor) == logical_index:
            option.state |= QStyle.StateFlag.State_MouseOver
            if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                option.state |= QStyle.StateFlag.State_Sunken
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)
        active = self.active_part()
        separator_start = parts[0][2] + self.fontMetrics().horizontalAdvance(parts[0][0])
        text_flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine
        painter.setPen(QColor(self.tokens["color.secondary"]))
        painter.drawText(
            QRect(separator_start, rect.y(), rect.right() - separator_start, rect.height()),
            text_flags,
            self.SPLIT_SEPARATOR,
        )
        for number, (text, _hit, start) in enumerate(parts):
            painter.setPen(QColor(self.tokens["color.text" if active in (None, number) else "color.secondary"]))
            painter.drawText(QRect(start, rect.y(), max(0, rect.right() - start), rect.height()), text_flags, text)
        painter.restore()

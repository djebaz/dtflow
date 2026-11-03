"""Presentation-only controls for the Designer-owned script library cards."""

from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton, QStylePainter


class ScriptRemoveButton(QPushButton):
    """Paint a 14 px control inside a 22 px native button hit target.

    Native button events, focus, accessibility and the shared distinct-click
    confirmation guard still operate on the full widget, including its padding.
    """

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect = self.rect().adjusted(4, 4, -4, -4)
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)

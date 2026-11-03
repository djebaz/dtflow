"""Grid-only image selection control with invisible pointer padding."""

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton, QStylePainter


class GallerySelectionCheck(QCheckBox):
    """Keep the existing 28 px checkbox housing inside a 56 px hit target.

    The painted housing remains at the existing (9, 9) image offset. Only this
    grid overlay captures the additional area; List and Detail are unchanged.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("galleryCardCheck")
        self.setGeometry(0, 0, 56, 56)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect = QRect(9, 9, 28, 28)
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_CheckBox, option)

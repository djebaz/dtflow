"""Keep the mouse wheel from changing values while a panel is being scrolled.

A combo box or spin box changes value on wheel even when it merely sits under the pointer, so scrolling the left panel
silently switched presets and steps. Unfocused ones now hand the wheel to the enclosing scroll area; a control the user
has clicked into still responds.
"""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QWidget


class WheelGuard(QObject):
    """Application-level wheel filter scoped to one window and parented to it (dies with the window)."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QComboBox | QAbstractSpinBox):
            return False
        if watched.hasFocus() or watched.window() is not self._window:
            return False
        parent = watched.parentWidget()
        while parent is not None and not isinstance(parent, QAbstractScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            QApplication.sendEvent(parent.viewport(), event)
        return True

"""Keep the mouse wheel from changing values while a panel is being scrolled.

A combo box or spin box changes value on wheel even when it merely sits under the pointer, so scrolling the left panel
silently switched presets and steps. Inside a panel that can scroll, combo boxes never take the wheel (the window may give one
initial keyboard focus, so focus alone cannot tell intent) and unfocused spin boxes and sliders do not either; all hand it to the
scroll area. In a panel that does not scroll the wheel still changes them.
"""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QWidget


class WheelGuard(QObject):
    """Application-level wheel filter scoped to one window and parented to it (dies with the window)."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QComboBox | QAbstractSpinBox | QAbstractSlider):
            return False
        if watched.window() is not self._window:
            return False
        if isinstance(watched, QAbstractSpinBox | QAbstractSlider) and watched.hasFocus():
            return False
        # Only guard where the wheel would otherwise scroll something: panels that do not scroll keep the wheel action.
        parent = watched.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea) and parent.verticalScrollBar().maximum() > 0:
                QApplication.sendEvent(parent.viewport(), event)
                return True
            parent = parent.parentWidget()
        return False

"""One wheel step for every list, table, tree and scroll area of the main window (#487).

Qt scrolls item views by whole items (a three-line wheel notch can jump several rows) and scroll areas by their own
default. This rule gives them all the Civitai gallery's step: pixel scrolling with a single step of ``WHEEL_STEP_PX``.
It is applied lazily, when a wheel event first reaches a view, so no view needs its own edit. Keyboard navigation, scroll
bar dragging and page steps keep Qt's distances (only the single step, used by the wheel and the scroll bar arrows, and the item-view scroll mode change).
"""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QScrollArea, QWidget

from dtflow.ui.app_filters import install_app_filter

WHEEL_STEP_PX = 12  # single step of the vertical scroll bar: smaller increments than Qt's item-sized automatic step
_OWN_WHEEL = "ownWheelScrolling"  # a view with its own benchmarked wheel handling sets this True and is left alone


def keep_own_wheel(area: QAbstractScrollArea) -> None:
    """Leave this view's wheel scrolling to its owner (the gallery grid and list, whose scrolling is benchmarked)."""
    area.setProperty(_OWN_WHEEL, True)


def apply_wheel_step(area: QAbstractScrollArea) -> None:
    """Give one view the shared wheel step (item views also scroll by pixel)."""
    if isinstance(area, QAbstractItemView):
        if area.verticalScrollMode() != QAbstractItemView.ScrollMode.ScrollPerPixel:
            area.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    area.verticalScrollBar().setSingleStep(WHEEL_STEP_PX)


class WheelStepRule(QObject):
    """Application-level filter scoped to one window and parented to it (dies with the window)."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        install_app_filter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        area = watched.parentWidget()
        if not isinstance(area, QAbstractScrollArea) or area.viewport() is not watched:
            return False
        if not isinstance(area, QAbstractItemView | QScrollArea) or area.window() is not self._window:
            return False
        if area.property(_OWN_WHEEL):
            return False
        # Re-asserted on every wheel event: a view may reset its single step when its contents or geometry change.
        apply_wheel_step(area)
        return False

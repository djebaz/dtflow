"""A stacked widget whose size follows only the page it shows."""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QStackedWidget


class CurrentPageStack(QStackedWidget):
    """Size hints from the shown page, not the tallest page of the whole stack.

    A plain QStackedWidget reports the largest hint and height-for-width of every page,
    hidden ones included. Inside a scroll area that made each rail as tall as the tallest
    tab's panel, so short pages (Connection) showed a scrollbar without any overflow.
    """

    def sizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()

    def heightForWidth(self, width: int) -> int:
        page = self.currentWidget()
        return page.heightForWidth(width) if page is not None else -1

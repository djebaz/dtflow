"""Non-interactive empty-list affordance; list models remain the source of truth."""

from PySide6.QtCore import QEvent, QObject, Qt, Slot
from PySide6.QtWidgets import QLabel
from shiboken6 import isValid


class EmptyListHint(QObject):
    def __init__(self, view, text):
        super().__init__(view)
        self.view = view
        self.label = QLabel(text, view.viewport())
        self.label.setProperty("role", "secondary")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        view.viewport().installEventFilter(self)
        for signal in (view.model().rowsInserted, view.model().rowsRemoved, view.model().modelReset):
            signal.connect(self.refresh)
        self.refresh()

    @Slot()
    def refresh(self):
        if not isValid(self.view) or not isValid(self.label):
            return
        self.label.setGeometry(self.view.viewport().rect().adjusted(8, 8, -8, -8))
        self.label.setVisible(self.view.model().rowCount() == 0)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.refresh()
        return False

"""Presentation-only exact-ID picker; searching never edits the selected model."""

from PySide6.QtCore import QEvent, QPoint, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class ModelPicker(QComboBox):
    recentChanged = Signal()
    RECENT_LIMIT = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(None)
        self._names: dict[str, str] = {}
        self._recents: list[str] = []
        self._observed = False
        self._popup = QFrame(self, Qt.WindowType.Popup)
        self._popup.setObjectName("modelPickerPopup")
        layout = QVBoxLayout(self._popup)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._search = QLineEdit(self._popup)
        self._search.setPlaceholderText("Search model name or ID…")
        self._search.setAccessibleName("Search models without changing selection")
        layout.addWidget(self._search)
        self._summary = QLabel(self._popup)
        self._summary.setProperty("textRole", "supporting")
        layout.addWidget(self._summary)
        self._results = QListWidget(self._popup)
        self._results.setObjectName("modelPickerResults")
        self._results.setAccessibleName("Model search results")
        self._results.setWordWrap(False)
        self._results.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._results, 1)
        self._manual = QPushButton("Use exact ID from search", self._popup)
        self._manual.setToolTip("Use the search text literally, even if it is not in the inventory")
        layout.addWidget(self._manual)
        hint = QLabel("Type an exact ID directly in the model field, or search here. Escape cancels.", self._popup)
        hint.setProperty("textRole", "supporting")
        layout.addWidget(hint)
        self._search.textChanged.connect(self._filter)
        self._results.itemClicked.connect(self._choose)
        self._manual.clicked.connect(lambda: self._commit(self._search.text().strip()))
        for widget in (self._search, self._results, self._manual, self._popup):
            widget.installEventFilter(self)
        self.lineEdit().editingFinished.connect(self._remember_current)
        self.currentTextChanged.connect(self._selection_hint)
        self._popup.hide()

    def set_inventory(self, ids, names=None, *, observed=True):
        current = self.currentText()
        self._names = {identifier: (names or {}).get(identifier, "") for identifier in dict.fromkeys(ids)}
        self._observed = observed
        # QComboBox otherwise selects the first item and emits a transient model change.
        with QSignalBlocker(self):
            self.clear()
            self.addItems(list(self._names))
            self.setCurrentText(current)
        self._selection_hint(current)
        if self._popup.isVisible():
            self._filter()

    def recents(self):
        return list(self._recents)

    def restore_recents(self, values):
        self._recents = (
            list(dict.fromkeys(x for x in values if isinstance(x, str) and x.strip()))[: self.RECENT_LIMIT]
            if isinstance(values, list)
            else []
        )

    def _remember_current(self):
        value = self.currentText().strip()
        if not value:
            return
        updated = [value, *(x for x in self._recents if x != value)][: self.RECENT_LIMIT]
        if updated != self._recents:
            self._recents = updated
            self.recentChanged.emit()

    def _selection_hint(self, value):
        note = "Not in current inventory" if self._observed and value and value not in self._names else ""
        self.setToolTip(f"{value}\n{note}".strip())
        self.lineEdit().setToolTip(self.toolTip())

    def showPopup(self):
        if not self.isEnabled():
            return
        # The shell initially disables unwired Designer descendants; these are wired here.
        for widget in (self._popup, self._search, self._results, self._manual):
            widget.setEnabled(True)
        self._search.clear()
        self._filter()
        screen = self.screen().availableGeometry()
        origin = self.mapToGlobal(QPoint(0, self.height()))
        width = min(max(760, self.width()), screen.width())
        below = screen.bottom() - origin.y() + 1
        above = self.mapToGlobal(QPoint(0, 0)).y() - screen.top()
        height = min(420, max(below, above), screen.height())
        x = max(screen.left(), min(origin.x(), screen.right() - width + 1))
        y = origin.y() if below >= height else origin.y() - self.height() - height
        self._popup.setGeometry(x, max(screen.top(), y), width, height)
        self._popup.show()
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)

    def hidePopup(self):
        self._popup.hide()
        super().hidePopup()

    def _filter(self, *_):
        selected = self._results.currentItem()
        previous = selected.data(Qt.ItemDataRole.UserRole) if selected else self.currentText()
        words = self._search.text().casefold().split()
        self._results.clear()
        matches = [key for key, name in self._names.items() if all(w in f"{name} {key}".casefold() for w in words)]
        recent = [] if words else self._recents
        for heading, identifiers in (("Recent", recent), ("Models", [x for x in matches if x not in recent])):
            if not identifiers:
                continue
            header = QListWidgetItem(heading, self._results)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            for identifier in identifiers:
                name = self._names.get(identifier, "")
                text = f"{name} — {identifier}" if name and name != identifier else identifier
                if self._observed and identifier not in self._names:
                    text += " — Not in current inventory"
                item = QListWidgetItem(text.replace("\n", " ").replace("\r", " "), self._results)
                item.setData(Qt.ItemDataRole.UserRole, identifier)
                item.setToolTip(identifier)
                if identifier == previous:
                    self._results.setCurrentItem(item)
        self._summary.setText(
            f"{len(matches)} matching models" if words else f"{len(matches)} inventory models · {len(recent)} recent"
        )
        if not matches and not recent:
            self._summary.setText("No matching models" if words else "Refresh inventory, or enter an exact model ID")
        self._manual.setEnabled(bool(self._search.text().strip()))
        if self._results.currentRow() < 0:
            self._move(1)

    def _move(self, direction):
        row = self._results.currentRow()
        if row < 0:
            row = -1 if direction > 0 else self._results.count()
        while 0 <= row + direction < self._results.count():
            row += direction
            item = self._results.item(row)
            if item.data(Qt.ItemDataRole.UserRole) is not None:
                self._results.setCurrentItem(item)
                self._results.scrollToItem(item)
                break

    def _choose(self, item):
        identifier = item.data(Qt.ItemDataRole.UserRole)
        if identifier is not None:
            self._commit(identifier)

    def _commit(self, identifier):
        if not identifier:
            return
        self.setCurrentText(identifier)
        self._remember_current()
        self.hidePopup()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.hidePopup()
                self.setFocus(Qt.FocusReason.PopupFocusReason)
                return True
            if watched is self._search and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._move(1 if key == Qt.Key.Key_Down else -1)
                return True
            if watched in (self._search, self._results) and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self._results.currentItem()
                if item:
                    self._choose(item)
                return True
        return super().eventFilter(watched, event)

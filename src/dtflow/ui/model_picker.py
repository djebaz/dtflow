"""Presentation-only exact-ID picker; searching never edits the selected model."""

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRegularExpression,
    QSignalBlocker,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QLayout, QLineEdit, QListView, QPushButton, QVBoxLayout


class ModelPicker(QComboBox):
    recentChanged = Signal()
    RECENT_LIMIT = 8
    ID_ROLE = Qt.ItemDataRole.UserRole
    SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(None)
        self._names: dict[str, str] = {}
        self._recents: list[str] = []
        self._observed = False
        self._dirty = True
        self._popup = None
        self._source = QStandardItemModel(self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._source)
        self._proxy.setFilterRole(self.SEARCH_ROLE)
        self._proxy.setDynamicSortFilter(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._filter)
        self.lineEdit().editingFinished.connect(self._remember_current)
        self.currentTextChanged.connect(self._selection_hint)
        self.installEventFilter(self)

    def _create_popup(self):
        # Create only after the shell has disabled the unwired Designer controls.
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
        self._results = QListView(self._popup)
        self._results.setObjectName("modelPickerResults")
        self._results.setAccessibleName("Model search results")
        self._results.setUniformItemSizes(True)
        self._results.setWrapping(False)
        self._results.setWordWrap(False)
        self._results.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._results.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self._results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._results.setModel(self._proxy)
        layout.addWidget(self._results, 1)
        self._manual = QPushButton("Use exact ID from search", self._popup)
        self._manual.setToolTip("Use the search text literally, even if it is not in the inventory")
        layout.addWidget(self._manual)
        hint = QLabel(
            "Recent choices appear first. Escape cancels. You can also type an ID in the model field.", self._popup
        )
        hint.setProperty("textRole", "supporting")
        layout.addWidget(hint)
        self._search.textChanged.connect(self._query_changed)
        self._results.clicked.connect(self._choose)
        self._manual.clicked.connect(lambda: self._commit(self._search.text().strip()))
        for widget in (self._search, self._results, self._manual, self._popup):
            widget.installEventFilter(self)

    def set_inventory(self, ids, names=None, *, observed=True):
        current = self.currentText()
        self._names = {identifier: (names or {}).get(identifier, "") for identifier in dict.fromkeys(ids)}
        self._observed = observed
        with QSignalBlocker(self):
            self.clear()
            self.addItems(list(self._names))
            self.setCurrentText(current)
        self._selection_hint(current)
        self._dirty = True
        if self._popup and self._popup.isVisible():
            self._rebuild()
            self._filter()

    def recents(self):
        return list(self._recents)

    def restore_recents(self, values):
        self._recents = (
            list(dict.fromkeys(x for x in values if isinstance(x, str) and x.strip()))[: self.RECENT_LIMIT]
            if isinstance(values, list)
            else []
        )
        self._dirty = True

    def _remember_current(self):
        value = self.currentText().strip()
        if not value:
            return
        updated = [value, *(x for x in self._recents if x != value)][: self.RECENT_LIMIT]
        if updated != self._recents:
            self._recents = updated
            self._dirty = True
            self.recentChanged.emit()

    def _selection_hint(self, value):
        note = "Not in current inventory" if self._observed and value and value not in self._names else ""
        self.setToolTip(f"{value}\n{note}".strip())
        self.lineEdit().setToolTip(self.toolTip())

    def _rebuild(self):
        if not self._dirty:
            return
        # Populate detached items once per inventory/history change, not once per key.
        source = QStandardItemModel(self)
        recent = set(self._recents)
        self._results.ensurePolished()
        metrics = self._results.fontMetrics()
        width = 0
        for identifier in dict.fromkeys([*self._recents, *self._names]):
            name = self._names.get(identifier, "")
            text = f"{name} — {identifier}" if name and name != identifier else identifier
            if identifier in recent:
                text = f"Recent · {text}"
            if self._observed and identifier not in self._names:
                text += " — Not in current inventory"
            text = text.replace("\n", " ").replace("\r", " ")
            width = max(width, metrics.horizontalAdvance(text) + 24)
            item = QStandardItem(text)
            item.setData(identifier, self.ID_ROLE)
            item.setData(f"{name} {identifier}".casefold(), self.SEARCH_ROLE)
            item.setToolTip(identifier)
            item.setEditable(False)
            source.appendRow(item)
        old = self._source
        self._source = source
        self._proxy.setSourceModel(source)
        self._results.setGridSize(QSize(width, max(36, metrics.height() + 12)))
        old.deleteLater()
        self._dirty = False

    def showPopup(self):
        if not self.isEnabled():
            return
        if self._popup is None:
            self._create_popup()
        self._timer.stop()
        with QSignalBlocker(self._search):
            self._search.clear()
        self._rebuild()
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
        self._timer.stop()
        if self._popup:
            self._popup.hide()
        super().hidePopup()

    def _query_changed(self, text):
        self._manual.setEnabled(bool(text.strip()))
        self._summary.setText("Searching…")
        self._timer.start()

    def _filter(self):
        self._timer.stop()
        words = self._search.text().casefold().split()
        pattern = "^" + "".join(f"(?=.*{QRegularExpression.escape(word)})" for word in words)
        self._proxy.setFilterRegularExpression(QRegularExpression(pattern))
        count = self._proxy.rowCount()
        self._summary.setText(
            f"{count} matching choices"
            if words
            else f"{len(self._names)} inventory models · {len(self._recents)} recent"
        )
        if not count:
            self._summary.setText("No matching models" if words else "Refresh inventory, or enter an exact model ID")
        self._manual.setEnabled(bool(self._search.text().strip()))
        self._results.setCurrentIndex(self._proxy.index(0, 0))

    def _move(self, direction):
        if self._timer.isActive():
            self._filter()
        row = self._results.currentIndex().row()
        row = max(0, min(row + direction, self._proxy.rowCount() - 1))
        index = self._proxy.index(row, 0)
        self._results.setCurrentIndex(index)
        self._results.scrollTo(index)

    def _choose(self, index):
        if self._timer.isActive():
            self._filter()
            return
        if index.isValid():
            self._commit(index.data(self.ID_ROLE))

    def _commit(self, identifier):
        if not identifier:
            return
        self.setCurrentText(identifier)
        self._remember_current()
        self.hidePopup()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            if watched is self._popup:
                # Outside click also hides Qt.Popup: reset QComboBox's native state.
                self._timer.stop()
                super().hidePopup()
            elif watched is self:
                self.hidePopup()
        if watched is not self and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.hidePopup()
                self.setFocus(Qt.FocusReason.PopupFocusReason)
                return True
            if watched in (self._search, self._results) and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._move(1 if key == Qt.Key.Key_Down else -1)
                return True
            if watched in (self._search, self._results) and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._timer.isActive():
                    self._filter()
                self._choose(self._results.currentIndex())
                return True
        return super().eventFilter(watched, event)

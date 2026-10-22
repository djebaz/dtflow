"""Inline substring-searchable selectors, including one-step LoRA weight entry."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from dtflow.ui.inline_feedback import feedback


def choose_searchable(
    parent: QWidget,
    *,
    title: str,
    items: list[str],
    callback: Callable,
    current: str = "",
    custom_label: str | None = None,
    custom_prompt: str = "Exact identifier",
    weights: dict[str, float | None] | None = None,
) -> None:
    """Edit search, identifier and optional bounded weight together; commit once."""
    editor = QWidget()
    layout = QVBoxLayout(editor)
    layout.setContentsMargins(0, 0, 0, 0)
    search = QLineEdit(editor)
    search.setObjectName("inlineSelectionSearch")
    search.setPlaceholderText("Search anywhere in the name…")
    search.setAccessibleName(title + " search")
    search.setClearButtonEnabled(True)
    choices = QListWidget(editor)
    choices.setObjectName("inlineSelectionChoices")
    choices.setAccessibleName(title + " choices")
    choices.setMaximumHeight(120)
    value = QLineEdit(current, editor)
    value.setObjectName("inlineSelectionIdentifier")
    value.setPlaceholderText(custom_prompt)
    value.setAccessibleName(custom_prompt)
    value.setReadOnly(not custom_label)
    layout.addWidget(search)
    layout.addWidget(choices)
    row = QHBoxLayout()
    row.addWidget(value, 1)
    weight = None
    weight_hint = None
    if weights is not None:
        weight = QDoubleSpinBox(editor)
        weight.setObjectName("inlineSelectionWeight")
        weight.setRange(0.0, 3.0)
        weight.setSingleStep(0.1)
        weight.setDecimals(1)
        weight.setValue(1.0)
        weight.setAccessibleName("LoRA weight, 0.0 to 3.0")
        row.addWidget(QLabel("Weight", editor))
        row.addWidget(weight)
    layout.addLayout(row)
    if weight is not None:
        weight_hint = QLabel("", editor)
        weight_hint.setProperty("role", "secondary")
        weight_hint.setWordWrap(True)
        weight_hint.hide()
        layout.addWidget(weight_hint)
    if custom_label:
        hint = QLabel(f"{custom_label}: enter the exact identifier below the list.", editor)
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        layout.addWidget(hint)
    unique = list(dict.fromkeys(name for name in items if name))

    def populate(query=""):
        choices.blockSignals(True)
        choices.clear()
        needle = query.casefold().strip()
        choices.addItems([name for name in unique if not needle or needle in name.casefold()])
        choices.blockSignals(False)
        matches = choices.findItems(value.text(), Qt.MatchFlag.MatchExactly)
        if matches:
            choices.setCurrentItem(matches[0])

    def selected(item, previous):
        if item is None:
            return
        value.setText(item.text())
        if weight is not None:
            default = weights.get(item.text())
            supported = default is not None and 0.0 <= default <= 3.0
            weight.setValue(default if supported else 1.0)
            if default is not None and not supported:
                weight_hint.setText(f"Catalogue weight {default:g} is outside 0.0–3.0; starting at 1.0.")
                weight_hint.show()
            else:
                weight_hint.hide()

    panel = feedback(parent)

    def accept():
        name = value.text().strip()
        if not name or (not custom_label and name not in unique):
            value.setFocus()
            return
        if weight is not None and not weight.hasAcceptableInput():
            weight.setFocus()
            return
        chosen_weight = weight.value() if weight is not None else None
        panel.dismiss()
        callback((name, chosen_weight) if weight is not None else name)

    search.textChanged.connect(populate)
    choices.currentItemChanged.connect(selected)
    panel.editor(title, editor, "Add" if weights is not None else "Apply", accept, primary=weights is not None)
    if weights is None:
        # Double-click or Enter applies like the Apply button. Enter in the search applies the highlighted match, or
        # the first match when the filter hid the previous choice; Enter in the identifier field applies what is typed.
        # Weighted (LoRA) selection keeps one explicit Add step.
        def activate():
            if search.hasFocus() and choices.currentItem() is None and choices.count():
                choices.setCurrentRow(0)
            accept()

        choices.itemDoubleClicked.connect(lambda _item: activate())
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), editor)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(activate)
    populate()
    search.setFocus()


class SearchableComboBox(QComboBox):
    """A selector that expands into the main-window editor, never a popup window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(False)
        self.custom_label = "Use custom identifier…"
        self.custom_prompt = "Exact identifier"

    def setCurrentText(self, text):
        if not text:
            self.setCurrentIndex(-1)
            return
        if self.findText(text) < 0:
            self.addItem(text)
        super().setCurrentText(text)

    def showPopup(self):
        choose_searchable(
            self,
            title=self.accessibleName() or "Select checkpoint",
            items=[self.itemText(index) for index in range(self.count())],
            current=self.currentText(),
            custom_label=self.custom_label,
            custom_prompt=self.custom_prompt,
            callback=self.setCurrentText,
        )

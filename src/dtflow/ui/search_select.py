"""Reusable searchable selection dialog for long model and LoRA lists."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

CUSTOM = object()


def choose_searchable(
    parent: QWidget,
    *,
    title: str,
    items: list[str],
    current: str = "",
    custom_label: str | None = None,
    custom_prompt: str = "Exact identifier",
) -> str | None:
    """Choose an item using case-insensitive substring search, with an optional raw-ID escape hatch."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    search = QLineEdit(dialog)
    search.setPlaceholderText("Search anywhere in the name…")
    search.setClearButtonEnabled(True)
    choices = QListWidget(dialog)
    choices.setAccessibleName(title + " choices")
    layout.addWidget(search)
    layout.addWidget(choices)
    if custom_label:
        hint = QLabel(f"{custom_label} is available for identifiers that are not listed.", dialog)
        hint.setProperty("role", "secondary")
        layout.addWidget(hint)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, dialog)
    layout.addWidget(buttons)

    unique = list(dict.fromkeys(name for name in items if name))

    def populate(query: str = "") -> None:
        needle = query.casefold().strip()
        choices.clear()
        for name in unique:
            if not needle or needle in name.casefold():
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                choices.addItem(item)
        if custom_label:
            item = QListWidgetItem(custom_label)
            item.setData(Qt.ItemDataRole.UserRole, CUSTOM)
            choices.addItem(item)
        if choices.count():
            target = next(
                (i for i in range(choices.count()) if choices.item(i).data(Qt.ItemDataRole.UserRole) == current),
                0,
            )
            choices.setCurrentRow(target)

    def accept_current() -> None:
        item = choices.currentItem()
        if item is None:
            return
        dialog.done(2 if item.data(Qt.ItemDataRole.UserRole) is CUSTOM else 1)

    search.textChanged.connect(populate)
    search.returnPressed.connect(accept_current)
    choices.itemDoubleClicked.connect(lambda _item: accept_current())
    buttons.rejected.connect(dialog.reject)
    populate()
    search.setFocus()

    result = dialog.exec()
    if result == 0:
        return None
    item = choices.currentItem()
    if item is None:
        return None
    if item.data(Qt.ItemDataRole.UserRole) is CUSTOM:
        value, accepted = QInputDialog.getText(parent, title, custom_prompt, text=current)
        value = value.strip()
        return value if accepted and value else None
    return str(item.data(Qt.ItemDataRole.UserRole))

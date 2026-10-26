"""Ctrl+Enter in a prompt editor presses one of the page's own action buttons (#184)."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractButton, QPlainTextEdit

from dtflow.ui.inline_feedback import feedback

_ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)  # Enter is the keypad key; the keypad modifier rides along


class SubmitShortcut(QObject):
    """Scoped to one editor, so it is never a global command; Enter alone still reaches the editor as a newline.

    ``target`` returns the button to press, or ``None`` when the shortcut has no action in the current state. The
    button is pressed with ``click()``, so its existing handler and enabled state are the only dispatch path, and a
    disabled button does nothing. Auto-repeat is swallowed rather than repeated, and a pending inline confirmation
    or editor owns the keyboard instead.
    """

    def __init__(self, edit: QPlainTextEdit, target: Callable[[], QAbstractButton | None]) -> None:
        super().__init__(edit)
        self._edit = edit
        self._target = target
        edit.installEventFilter(self)
        edit.viewport().installEventFilter(self)  # tooltip events are delivered to the viewport, not the editor

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            button = self._target()
            self._edit.setToolTip(f"Ctrl+Enter: {button.text()}" if button is not None else "")
            return False
        if kind != QEvent.Type.KeyPress or event.key() not in _ENTER_KEYS:
            return False
        if event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier != Qt.KeyboardModifier.ControlModifier:
            return False
        button = self._target()
        panel = feedback(self._edit)
        if event.isAutoRepeat() or button is None or not button.isEnabled() or panel.owns_keyboard():
            return True  # still not a newline: the shortcut owns the combination
        button.click()
        return True

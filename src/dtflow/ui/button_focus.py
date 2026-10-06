"""Shared keyboard-only focus cues for state-colored buttons."""

from PySide6.QtCore import QEvent, QObject, Qt


class KeyboardFocusGuard(QObject):
    """Keep mouse focus from disguising state; preserve a visible keyboard cue."""

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            keyboard = event.type() == QEvent.Type.FocusIn and event.reason() in (
                Qt.FocusReason.TabFocusReason,
                Qt.FocusReason.BacktabFocusReason,
                Qt.FocusReason.ShortcutFocusReason,
            )
            watched.setProperty("keyboardFocus", keyboard)
            watched.style().unpolish(watched)
            watched.style().polish(watched)
        return False

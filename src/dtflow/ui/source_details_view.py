"""One-line labels for what a safetensors file declares about this LoRA's training (#414).

File content is untrusted: every label renders it as plain text (never rich text or image markup), and the line is
simply cut with an ellipsis to the width it has, so it never grows the layout."""

from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy


def safe_tooltip(text: str) -> str:
    """Tooltips are rich text by default; file content is escaped so it shows literally."""
    return f"<p style='white-space:pre-wrap'>{html.escape(text)}</p>" if text else ""


class ElidedLabel(QObject):
    """Keeps a one-line QLabel's text cut to its width with an ellipsis (the full text is always in the popup)."""

    def __init__(self, label: QLabel) -> None:
        super().__init__(label)
        self._label, self._text = label, ""
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.installEventFilter(self)

    def set_text(self, text: str) -> None:
        self._text = text
        self._apply()

    def _apply(self) -> None:
        width = max(0, self._label.width())
        self._label.setText(self._label.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, width))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self._label and event.type() == QEvent.Type.Resize:
            self._apply()
        return False

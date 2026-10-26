"""Tag styling for prompt-variation keywords in prompt editors (#129 syntax: ``{a|b}`` choices, ``__name__`` wildcards)."""

import re

from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit

from dtflow.ui.theme import load_tokens

_WILDCARD = re.compile(r"__([A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)*)__")


def tag_spans(text: str) -> list[tuple[int, int]]:
    """``(start, length)`` of each outermost balanced ``{...}`` group and each top-level wildcard.

    Lenient on purpose: it runs on half-typed text, so an unclosed ``{`` or a stray ``}`` is simply not a tag.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            if depth == 0:
                start = i
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1 - start))
        elif depth == 0 and char == "_" and (match := _WILDCARD.match(text, i)):
            spans.append((i, match.end() - i))
            i = match.end()
            continue
        i += 1
    return spans


class PromptTagHighlighter(QSyntaxHighlighter):
    """Blue-background, white-text tags over variation keywords; the text itself is never changed.

    Groups are found per document, so a group spanning several lines is tagged on every line it covers.
    """

    def __init__(self, edit: QPlainTextEdit) -> None:
        super().__init__(edit.document())
        tokens = load_tokens()
        self._format = QTextCharFormat()
        self._format.setBackground(QColor(tokens["color.accent"]))
        self._format.setForeground(QColor(tokens["color.onAccent"]))
        self._revision = -1
        self._spans: list[tuple[int, int]] = []

    def highlightBlock(self, text: str) -> None:
        document = self.document()
        if document.revision() != self._revision:  # one scan per edit, not one per re-highlighted block
            self._revision = document.revision()
            self._spans = tag_spans(document.toPlainText())
        offset = self.currentBlock().position()
        end = offset + len(text)
        for start, length in self._spans:
            lo, hi = max(start, offset), min(start + length, end)
            if lo < hi:
                self.setFormat(lo - offset, hi - lo, self._format)


def style_prompt_tags(edit: QPlainTextEdit) -> PromptTagHighlighter:
    """Attach the highlighter to ``edit``; it lives as long as the editor's document."""
    return PromptTagHighlighter(edit)

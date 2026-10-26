"""Tag styling for prompt-variation keywords in prompt editors (#129 syntax: ``{a|b}`` choices, ``__name__`` wildcards)."""

import re

from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit

from dtflow.ui.theme import load_tokens

_WILDCARD = re.compile(r"__([A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)*)__")


def tag_spans(text: str) -> list[tuple[int, int]]:
    """``(start, length)`` of each outermost balanced ``{...}`` group and each top-level wildcard.

    Lenient on purpose: it runs on half-typed text, so a stray ``}`` is not a tag and an unclosed ``{`` is skipped
    without hiding the tags that follow it.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
        elif char == "{" and (end := _group_end(text, i)) is not None:
            spans.append((i, end - i))
            i = end
        elif char == "_" and (match := _WILDCARD.match(text, i)):
            spans.append((i, match.end() - i))
            i = match.end()
        else:
            i += 1
    return spans


def _group_end(text: str, start: int) -> int | None:
    """Index just past the ``}`` that closes the ``{`` at ``start``, or ``None`` when it never closes."""
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


class PromptTagHighlighter(QSyntaxHighlighter):
    """Blue-background, white-text tags over variation keywords; the text itself is never changed.

    Groups are found per document and re-applied when they change, so a group spanning lines is tagged on each one.
    """

    def __init__(self, edit: QPlainTextEdit) -> None:
        super().__init__(edit.document())
        tokens = load_tokens()
        self._format = QTextCharFormat()
        self._format.setBackground(QColor(tokens["color.accent"]))
        self._format.setForeground(QColor(tokens["color.onAccent"]))
        self._spans: list[tuple[int, int]] = []
        # A group can span lines, so an edit may change tags on blocks the editor never re-highlights by itself.
        self.document().contentsChanged.connect(self._retag)
        self._retag()

    def _retag(self) -> None:
        spans = tag_spans(self.document().toPlainText())
        if spans != self._spans:
            self._spans = spans
            self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        offset = self.currentBlock().position()
        end = offset + len(text)
        for start, length in self._spans:
            lo, hi = max(start, offset), min(start + length, end)
            if lo < hi:
                self.setFormat(lo - offset, hi - lo, self._format)


def style_prompt_tags(edit: QPlainTextEdit) -> PromptTagHighlighter:
    """Attach the highlighter to ``edit``; it lives as long as the editor's document."""
    return PromptTagHighlighter(edit)

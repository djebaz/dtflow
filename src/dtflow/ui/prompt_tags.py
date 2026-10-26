"""Tag styling for prompt-variation keywords in prompt editors (#129 syntax: ``{a|b}`` choices, ``__name__`` wildcards)."""

import json
import re
from html import escape

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTextEdit

from dtflow.services.dynamic_prompts import PROMPT_TAGS_KEY, Runs, decode_runs
from dtflow.ui.theme import load_tokens

PROMPT_RUNS_ROLE = Qt.ItemDataRole.UserRole + 7  # table cells: the (start, length) runs of the cell text to tag
_WILDCARD = re.compile(r"__([A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)*)__")


def utf16_runs(text: str, runs: Runs) -> Runs:
    """``(start, length)`` runs counted in code points, re-counted in the UTF-16 units Qt documents use."""
    if text.isascii() or all(ord(char) <= 0xFFFF for char in text):
        return tuple(runs)
    units = [0]
    for char in text:
        units.append(units[-1] + (2 if ord(char) > 0xFFFF else 1))
    return tuple((units[start], units[start + length] - units[start]) for start, length in runs)


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

    def __init__(self, edit: QPlainTextEdit | QTextEdit, *, scan: bool = True) -> None:
        super().__init__(edit.document())
        self._scan = scan  # a display of an already resolved prompt only tags the runs it is told about
        tokens = load_tokens()
        self._format = QTextCharFormat()
        self._format.setBackground(QColor(tokens["color.accent"]))
        self._format.setForeground(QColor(tokens["color.onAccent"]))
        self._spans: list[tuple[int, int]] = []
        # A group can span lines, so an edit may change tags on blocks the editor never re-highlights by itself.
        self.document().contentsChanged.connect(self._retag)
        self._retag()

    def _retag(self) -> None:
        if self._scan:
            self.set_runs(tag_spans(self.document().toPlainText()))

    def set_runs(self, runs: Runs) -> None:
        """Tag exactly these ``(start, length)`` runs of the document text, counted in code points."""
        spans = list(utf16_runs(self.document().toPlainText(), runs))
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


def style_prompt_tags(edit: QPlainTextEdit | QTextEdit, *, scan: bool = True) -> PromptTagHighlighter:
    """Attach the highlighter to ``edit``; it lives as long as the editor's document."""
    return PromptTagHighlighter(edit, scan=scan)


def request_runs(request: dict) -> Runs:
    """The runs of a stored request's prompt that its prompt variation produced (none once the prompt was edited)."""
    prompt = request.get("prompt")
    evidence = request.get("resolution_evidence")
    if not isinstance(prompt, str) or not isinstance(evidence, list):
        return ()
    value = next(
        (pair[1] for pair in evidence if isinstance(pair, list) and len(pair) == 2 and pair[0] == PROMPT_TAGS_KEY), None
    )
    return decode_runs(prompt, value if isinstance(value, str) else None)


def stored_runs(request_json: str) -> Runs:
    """``request_runs`` for a request as stored JSON text; unreadable JSON has no runs."""
    try:
        request = json.loads(request_json)
    except (TypeError, ValueError):
        return ()
    return request_runs(request) if isinstance(request, dict) else ()


def tagged_html(text: str, runs: Runs) -> str:
    """``text`` as escaped rich text with each run styled like the editor tags (for tooltips and labels)."""
    tokens = load_tokens()
    style = f"background-color:{tokens['color.accent']};color:{tokens['color.onAccent']}"
    out: list[str] = []
    position = 0
    for start, length in runs:
        out += [escape(text[position:start]), f"<span style='{style}'>{escape(text[start : start + length])}</span>"]
        position = start + length
    out.append(escape(text[position:]))
    return "".join(out)


def paint_tag_runs(painter: QPainter, rect: QRect, shown: str, runs: Runs, font: QFont) -> None:
    """Paint tags over ``shown``, the single line of text already drawn at the left of ``rect`` (ellipsis excluded)."""
    tokens = load_tokens()
    metrics = QFontMetrics(font)
    painter.save()
    painter.setClipRect(rect)
    painter.setFont(font)
    for start, length in runs:
        end = min(start + length, len(shown))
        if start >= end:
            continue
        tag = QRect(
            rect.left() + metrics.horizontalAdvance(shown[:start]),
            rect.top(),
            metrics.horizontalAdvance(shown[start:end]),
            rect.height(),
        )
        painter.fillRect(tag, QColor(tokens["color.accent"]))
        painter.setPen(QColor(tokens["color.onAccent"]))
        painter.drawText(tag, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown[start:end])
    painter.restore()


def elided_without_ellipsis(text: str, font: QFont, width: int) -> str:
    """What ``elidedText(..., ElideRight, width)`` keeps of ``text``, without the trailing ellipsis."""
    elided = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, width)
    return elided[:-1] if elided != text else text


def paint_cell_tags(delegate: QStyledItemDelegate, painter: QPainter, option, index) -> None:
    """Over a cell the style drew, paint the ``PROMPT_RUNS_ROLE`` runs of its single elided line as tags."""
    runs = index.data(PROMPT_RUNS_ROLE)
    if not runs:
        return
    option = QStyleOptionViewItem(option)
    delegate.initStyleOption(option, index)
    widget = option.widget
    style = widget.style() if widget else QApplication.style()
    rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, widget)
    text = index.data(Qt.ItemDataRole.DisplayRole) or ""
    paint_tag_runs(painter, rect, elided_without_ellipsis(text, option.font, rect.width()), runs, option.font)


class TagCellDelegate(QStyledItemDelegate):
    """Plain cells, except that runs stored under ``PROMPT_RUNS_ROLE`` are painted as tags."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        paint_cell_tags(self, painter, option, index)

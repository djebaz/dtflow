"""Native JavaScript editor: line numbers, scoped syntax colors and draft operations.

The editor manipulates in-memory draft text. Saving and parse validation are
intentionally disconnected until the immutable script-library service is wired.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from dtflow.ui.theme import load_tokens

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)


class _LineNumbers(QWidget):
    def __init__(self, editor: "ScriptCodeEdit"):
        super().__init__(editor)
        self.editor = editor
        tokens = load_tokens()
        self._background = QColor(tokens["color.scriptEditorGutter"])
        self._line_number_color = QColor(tokens["color.scriptEditorLineNumber"])
        self._separator = QColor(tokens["color.border"])

    def sizeHint(self):
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._background)
        first = self.editor.firstVisibleBlock()
        number = first.blockNumber()
        top = round(self.editor.blockBoundingGeometry(first).translated(self.editor.contentOffset()).top())
        bottom = top + round(self.editor.blockBoundingRect(first).height())
        while first.isValid() and top <= event.rect().bottom():
            if first.isVisible() and bottom >= event.rect().top():
                painter.setPen(self._line_number_color)
                painter.drawText(
                    0, top, self.width() - 7, self.editor.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(number + 1)
                )
            first = first.next()
            top = bottom
            bottom = top + round(self.editor.blockBoundingRect(first).height())
            number += 1
        painter.setPen(self._separator)
        painter.drawLine(self.width() - 1, event.rect().top(), self.width() - 1, event.rect().bottom())


class _JavaScriptColors(QSyntaxHighlighter):
    """Scoped VS2019 Dark syntax decoration; not a JavaScript parser."""

    PATTERNS = (
        (r"//[^\n]*", "color.scriptEditorComment"),
        (r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', "color.scriptEditorString"),
        (r"\b(?:const|let|var|function|return|if|else|for|while|of|in|new|true|false|null|this)\b", "color.scriptEditorKeyword"),
        (r"\b(?:pipeline|canvas|configuration|requestFromUser|Math|Array|Object|String|Number)\b", "color.scriptEditorType"),
        (r"\b\d+(?:\.\d+)?\b", "color.scriptEditorNumber"),
        (r"[=+*/<>!&|?:%-]+", "color.scriptEditorOperator"),
    )

    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        colors = load_tokens()
        for pattern, token in self.PATTERNS:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colors[token]))
            self.rules.append((re.compile(pattern), fmt))

    def highlightBlock(self, text: str):
        for regex, fmt in reversed(self.rules):
            for match in regex.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class ScriptCodeEdit(QPlainTextEdit):
    """QPlainTextEdit with a dynamically sized line-number gutter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scriptsCodeEditor")
        self.setAccessibleName("Script source code")
        self.setTabStopDistance(32)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas", 12)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.gutter = _LineNumbers(self)
        self.highlighter = _JavaScriptColors(self.document())
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._update_cursor)
        self._update_gutter_width(0)

    def line_number_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return max(40, 16 + self.fontMetrics().horizontalAdvance("9") * digits)

    def _update_gutter_width(self, _):
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def _update_cursor(self):
        self.gutter.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.gutter.setGeometry(QRect(rect.left(), rect.top(), self.line_number_width(), rect.height()))

    def change_font_size(self, delta: int):
        font = self.font()
        font.setPointSize(max(10, min(20, font.pointSize() + delta)))
        self.setFont(font)
        self._update_gutter_width(0)

    def go_to_line(self, line: int, column: int = 1):
        block = self.document().findBlockByNumber(max(0, line - 1))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, max(0, column - 1))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()


class ScriptEditor(QWidget):
    """Native draft editor with undo/redo, search and diagnostics presentation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scriptsEditorPanel")
        self._saved_content = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QFrame(self)
        toolbar.setObjectName("scriptsEditorToolbar")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        self.editor = ScriptCodeEdit(self)

        history = QFrame(toolbar)
        history.setProperty("role", "segmented")
        history_row = QHBoxLayout(history)
        history_row.setContentsMargins(0, 0, 0, 0)
        history_row.setSpacing(0)
        for label, caption, edge, callback in (
            ("↶", "Undo (Ctrl+Z)", "first", self.editor.undo),
            ("↷", "Redo (Ctrl+Y)", "last", self.editor.redo),
        ):
            button = QPushButton(label)
            button.setAccessibleName(caption)
            button.setToolTip(caption)
            button.setProperty("segmentEdge", edge)
            button.setProperty("toolbarKind", "icon")
            button.setFixedSize(32, 32)
            button.clicked.connect(callback)
            history_row.addWidget(button)
        tools.addWidget(history)

        find = QPushButton("Find…")
        find.setAccessibleName("Find in script")
        find.setToolTip("Find (Ctrl+F)")
        find.clicked.connect(self._toggle_find)
        tools.addWidget(find)

        font_group = QFrame(toolbar)
        font_group.setProperty("role", "segmented")
        font_row = QHBoxLayout(font_group)
        font_row.setContentsMargins(0, 0, 0, 0)
        font_row.setSpacing(0)
        for label, caption, edge, delta in (
            ("A−", "Smaller editor font", "first", -1),
            ("A+", "Larger editor font", "last", 1),
        ):
            button = QPushButton(label)
            button.setAccessibleName(caption)
            button.setToolTip(caption)
            button.setProperty("segmentEdge", edge)
            button.setProperty("toolbarKind", "icon")
            button.setFixedSize(32, 32)
            button.clicked.connect(lambda _checked=False, amount=delta: self.editor.change_font_size(amount))
            font_row.addWidget(button)
        tools.addWidget(font_group)
        tools.addStretch(1)
        self.check_syntax = QPushButton("Check syntax")
        self.check_syntax.setToolTip("Parse-only check will be available when the editor connects to the script engine.")
        self.check_syntax.setEnabled(False)
        tools.addWidget(self.check_syntax)
        layout.addWidget(toolbar)
        self.find_row = QWidget()
        find_layout = QHBoxLayout(self.find_row)
        find_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find")
        self.search.setAccessibleName("Find")
        find_layout.addWidget(self.search)
        next_button = QPushButton("Next")
        next_button.clicked.connect(self.find_next)
        find_layout.addWidget(next_button)
        close = QPushButton("×")
        close.setAccessibleName("Close search")
        close.clicked.connect(lambda: self.find_row.hide())
        find_layout.addWidget(close)
        layout.addWidget(self.find_row)
        self.find_row.hide()
        self.run_notice = QLabel("")
        self.run_notice.setObjectName("scriptsEditorRunNote")
        self.run_notice.setWordWrap(True)
        self.run_notice.hide()
        layout.addWidget(self.run_notice)
        layout.addWidget(self.editor, 1)
        status = QHBoxLayout()
        self.diagnostic = QLabel("Syntax not checked yet")
        self.diagnostic.setObjectName("scriptsEditorDiagnostic")
        self.diagnostic.setWordWrap(True)
        status.addWidget(self.diagnostic, 1)
        self.go_to_error = QPushButton("Go to line")
        self._error_location: tuple[int, int] | None = None
        self.go_to_error.clicked.connect(self._jump_to_error)
        self.go_to_error.hide()
        status.addWidget(self.go_to_error)
        self.cursor_status = QLabel("Ln 1 · Col 1 · JavaScript")
        self.editor.cursorPositionChanged.connect(self._cursor_changed)
        status.addWidget(self.cursor_status)
        layout.addLayout(status)
        footer = QHBoxLayout()
        self.draft_status = QLabel("New draft · unsaved")
        footer.addWidget(self.draft_status, 1)
        self.discard = QPushButton("Discard")
        self.discard.setEnabled(False)
        self.discard.clicked.connect(self.restore)
        footer.addWidget(self.discard)
        self.save_new = QPushButton("Save as new version")
        self.save_new.setToolTip("Immutable version persistence is not connected yet.")
        self.save_new.setEnabled(False)
        footer.addWidget(self.save_new)
        layout.addLayout(footer)
        self.editor.textChanged.connect(self._modified)

    def _cursor_changed(self):
        cursor = self.editor.textCursor()
        self.cursor_status.setText(f"Ln {cursor.blockNumber() + 1} · Col {cursor.positionInBlock() + 1} · JavaScript")

    def _modified(self):
        dirty = self.editor.toPlainText() != self._saved_content
        self.draft_status.setText("Draft · unsaved changes" if dirty else "No unsaved changes")
        self.discard.setEnabled(dirty)

    def _toggle_find(self):
        self.find_row.setVisible(not self.find_row.isVisible())
        if self.find_row.isVisible():
            self.search.setFocus()

    def find_next(self):
        text = self.search.text()
        if text and not self.editor.find(text):
            self.editor.moveCursor(QTextCursor.MoveOperation.Start)
            self.editor.find(text)

    def restore(self):
        self.editor.setPlainText(self._saved_content)

    def set_draft(self, source: str, *, status: str = "Draft · unsaved changes"):
        self._saved_content = source
        self.editor.setPlainText(source)
        self.clear_error()
        self.draft_status.setText(status)

    def clear_error(self) -> None:
        """Reset stale diagnostics when another draft or preview state is loaded."""
        self._error_location = None
        self.editor.setExtraSelections([])
        self.diagnostic.setText("Syntax not checked yet")
        self.diagnostic.setProperty("feedbackState", "")
        self.diagnostic.style().unpolish(self.diagnostic)
        self.diagnostic.style().polish(self.diagnostic)
        self.go_to_error.hide()

    def _jump_to_error(self) -> None:
        if self._error_location is not None:
            self.editor.go_to_line(*self._error_location)

    def set_error(self, line: int, column: int, message: str):
        """Show a visual diagnostic; the caller supplies actual parser evidence."""
        block = self.editor.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(block)
            selection.cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            selection.format.setBackground(QColor(load_tokens()["color.dangerSurface"]))
            self.editor.setExtraSelections([selection])
        self.diagnostic.setText(f"Syntax error · line {line}, column {column} · {message}")
        self.diagnostic.setProperty("feedbackState", "error")
        self._error_location = (line, column)
        self.go_to_error.setText(f"Go to line {line}")
        self.go_to_error.show()

"""Native JavaScript editor: line numbers, scoped syntax colors and draft operations.

The editor manipulates in-memory draft text. Preview checks use explicit fixture
outcomes, never a parser or script evaluation. Preview saves never write to disk.
"""

from __future__ import annotations

import re
import hashlib

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QShortcut,
    QKeySequence,
    QTextDocument,
    QTextFormat,
)
from dtflow.ui.inpaint_canvas import REDO_PATH, UNDO_PATH, _arrow_icon
from dtflow.ui.theme import load_tokens

from PySide6.QtWidgets import (
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
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
                painter.setPen(
                    QColor(load_tokens()["color.error"])
                    if number + 1 == self.editor.error_line
                    else self._line_number_color
                )
                painter.drawText(
                    0,
                    top,
                    self.width() - 7,
                    self.editor.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(number + 1),
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
        (
            r"\b(?:const|let|var|function|return|if|else|for|while|of|in|new|true|false|null|this)\b",
            "color.scriptEditorKeyword",
        ),
        (
            r"\b(?:pipeline|canvas|configuration|requestFromUser|Math|Array|Object|String|Number)\b",
            "color.scriptEditorType",
        ),
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
        self.error_line = None
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

    def __init__(self, parent=None, *, consent=None, saved=None):
        super().__init__(parent)
        from dtflow.ui.main_window import FORMS_DIR, load_ui

        self.form = load_ui(FORMS_DIR / "scripts_editor.ui", self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form)
        self._saved_content = ""
        self._consent = consent
        self._error_location = None
        self._preview_enabled = False
        self._preview_checks = {}
        self._checked_source = None
        self._based_on = ""
        self._saved_callback = saved
        self._live_check = None
        self.editor = ScriptCodeEdit(self)
        self.form.codeHost.layout().addWidget(self.editor)
        self.form.codeHost.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for name, path, callback in (
            ("undoButton", UNDO_PATH, self.editor.undo),
            ("redoButton", REDO_PATH, self.editor.redo),
        ):
            button = getattr(self.form, name)
            button.setIcon(_arrow_icon(path, load_tokens()["color.secondary"]))
            button.setIconSize(QSize(16, 16))
            button.setText("")
            button.clicked.connect(callback)
        self.form.smallerFontButton.clicked.connect(lambda: self.editor.change_font_size(-1))
        self.form.largerFontButton.clicked.connect(lambda: self.editor.change_font_size(1))
        self.check_syntax = self.form.checkSyntaxButton
        self.check_syntax.setToolTip(
            "Offline preview: known examples show fixture diagnostics; no code is parsed or executed."
        )
        self.check_syntax.clicked.connect(self.check_preview)
        self.find_row, self.search = self.form.findRow, self.form.searchField
        self.replacement, self.match_count = self.form.replacementField, self.form.matchCount
        self.form.findButton.clicked.connect(self._toggle_find)
        self.form.findNextButton.clicked.connect(self.find_next)
        self.form.replaceAllButton.clicked.connect(self.replace_all)
        self.form.closeFindButton.clicked.connect(self.find_row.hide)
        self.search.textChanged.connect(self._update_matches)
        self.search.returnPressed.connect(self.find_next)
        self.find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.find_shortcut.activated.connect(self._open_find)
        self.find_row.hide()
        self.run_notice = self.form.scriptsEditorRunNote
        self.run_notice.hide()
        self.diagnostic, self.go_to_error = self.form.scriptsEditorDiagnostic, self.form.goToErrorButton
        self.go_to_error.clicked.connect(self._jump_to_error)
        self.go_to_error.hide()
        self.cursor_status, self.draft_status = self.form.cursorStatus, self.form.draftStatus
        self.discard, self.save_new = self.form.discardButton, self.form.saveVersionButton
        self.discard.clicked.connect(self._request_restore)
        self.save_new.setToolTip("Offline preview: keep a version in memory only, never on disk.")
        self.save_new.clicked.connect(self.save_preview)
        self.editor.cursorPositionChanged.connect(self._cursor_changed)
        self.editor.textChanged.connect(self._modified)
        self.editor.textChanged.connect(self._update_matches)

    def _cursor_changed(self):
        cursor = self.editor.textCursor()
        self.cursor_status.setText(
            f"Ln {cursor.blockNumber() + 1} · Col {cursor.positionInBlock() + 1} · "
            f"{self.editor.blockCount()} lines · JavaScript"
        )

    def _modified(self):
        dirty = self.editor.toPlainText() != self._saved_content
        self.draft_status.setText(
            ("Draft · unsaved changes" if dirty else "No unsaved changes")
            + (f" · based on version {self._based_on}" if self._based_on else "")
        )
        self.discard.setEnabled(dirty)
        if self._checked_source != self.editor.toPlainText():
            self.clear_error()
        self.save_new.setEnabled(
            self._preview_enabled
            and dirty
            and self._checked_source == self.editor.toPlainText()
            and self._error_location is None
        )
        self._cursor_changed()

    def enable_preview(self, outcomes: dict[str, tuple | None]) -> None:
        """Known fixture text maps to explicit outcomes; arbitrary text is never guessed valid."""
        self._preview_enabled = True
        self._preview_checks = dict(outcomes)
        self.check_syntax.setEnabled(True)

    def enable_live(self, check) -> None:
        """Bind Check syntax to the real analyzer. ``check(source, done)`` calls ``done((line, column, message) | None)``
        later on the GUI thread; the source is only ever parsed."""
        self._live_check = check
        self._preview_enabled = True
        self._preview_checks = {}
        self.check_syntax.setEnabled(True)
        self.check_syntax.setToolTip("Parse the script with the bundled Acorn parser. Nothing is executed.")
        self.save_new.setToolTip("Store the checked text as a new immutable version in the script library.")

    def disable_live(self) -> None:
        self._live_check = None

    def _check_live(self, source: str) -> None:
        self.diagnostic.setText("Checking syntax…")
        self.check_syntax.setEnabled(False)

        def done(error) -> None:
            self.check_syntax.setEnabled(True)
            if self.editor.toPlainText() != source or self._live_check is None:
                return  # the text changed while the parser ran; the result no longer describes it
            self._checked_source = source
            if error is not None:
                self.set_error(*error)
            else:
                self.diagnostic.setText("● Syntax valid · parsed, not executed")
                self.diagnostic.setProperty("feedbackState", "ok")
                self.diagnostic.style().unpolish(self.diagnostic)
                self.diagnostic.style().polish(self.diagnostic)
            self._modified()

        self._live_check(source, done)

    def check_preview(self):
        source = self.editor.toPlainText()
        self.clear_error()
        if self._live_check is not None:
            self._check_live(source)
            return
        if not self._preview_enabled or source not in self._preview_checks:
            self.diagnostic.setText(
                "Not checked: no offline fixture diagnostic for this text. Real parsing is not connected."
            )
            self.save_new.setEnabled(False)
            return
        self._checked_source = source
        error = self._preview_checks[source]
        if error is not None:
            self.set_error(*error)
        else:
            self.diagnostic.setText("● Syntax valid · fixture outcome only; no parser ran")
        self._modified()

    def save_preview(self):
        source = self.editor.toPlainText()
        if not self.save_new.isEnabled() or self._checked_source != source:
            return
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        self._based_on = sha[:7]
        self._saved_content = source
        self._modified()
        live = self._live_check is not None
        self.draft_status.setText(
            f"Version {sha[:7]} saved to the script library"
            if live
            else f"Preview version {sha[:7]} · memory only, not persisted"
        )
        if self._saved_callback is not None:
            self._saved_callback(source, sha)

    def is_dirty(self) -> bool:
        return self.editor.toPlainText() != self._saved_content

    def _request_restore(self):
        if self.is_dirty() and self._consent is not None:
            self._consent(self.discard, self.restore)

    def _open_find(self):
        self.find_row.show()
        self.search.setFocus()
        self.search.selectAll()

    def _update_matches(self):
        query = self.search.text()
        count = self.editor.toPlainText().count(query) if query else 0
        self.match_count.setText(f"{count} match" + ("" if count == 1 else "es"))
        self.form.findNextButton.setEnabled(count > 0)
        self.form.replaceAllButton.setEnabled(count > 0)

    def replace_all(self):
        query = self.search.text()
        if not query:
            return
        document = self.editor.document()
        matches = []
        cursor = QTextCursor(document)
        while True:
            cursor = document.find(query, cursor, QTextDocument.FindFlag.FindCaseSensitively)
            if cursor.isNull():
                break
            matches.append(QTextCursor(cursor))
        edit = QTextCursor(document)
        edit.beginEditBlock()
        for match in reversed(matches):
            match.insertText(self.replacement.text())
        edit.endEditBlock()

    def _toggle_find(self):
        self.find_row.setVisible(not self.find_row.isVisible())
        if self.find_row.isVisible():
            self.search.setFocus()

    def find_next(self):
        text = self.search.text()
        flags = QTextDocument.FindFlag.FindCaseSensitively
        if text and not self.editor.find(text, flags):
            self.editor.moveCursor(QTextCursor.MoveOperation.Start)
            self.editor.find(text, flags)

    def restore(self):
        self.editor.setPlainText(self._saved_content)

    def set_draft(
        self, source: str, *, status: str = "Draft · unsaved changes", based_on: str = "", saved: bool = True
    ):
        self._based_on = based_on
        self.editor.setPlainText(source)
        # Compare against the editor's own normalised text: a CRLF file would otherwise always look edited.
        self._saved_content = self.editor.toPlainText() if saved else ""
        self.clear_error()
        self.draft_status.setText(status)

    def clear_error(self) -> None:
        """Reset stale diagnostics when another draft or preview state is loaded."""
        self._error_location = None
        self._checked_source = None
        self.editor.error_line = None
        self.editor.gutter.update()
        self.editor.setExtraSelections([])
        self.diagnostic.setText("Syntax not checked yet")
        self.diagnostic.setProperty("feedbackState", "")
        self.diagnostic.style().unpolish(self.diagnostic)
        self.diagnostic.style().polish(self.diagnostic)
        self.go_to_error.hide()
        self.save_new.setEnabled(False)

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
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            selection.format.setUnderlineColor(QColor(load_tokens()["color.error"]))
            self.editor.setExtraSelections([selection])
        self.editor.error_line = line
        self.editor.gutter.update()
        self.diagnostic.setText(f"● Syntax error · line {line}, column {column} · {message}")
        self.diagnostic.setProperty("feedbackState", "error")
        self.diagnostic.style().unpolish(self.diagnostic)
        self.diagnostic.style().polish(self.diagnostic)
        self._error_location = (line, column)
        self.go_to_error.setText(f"Go to line {line}")
        self.go_to_error.show()
        self.save_new.setEnabled(False)

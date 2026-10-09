"""Non-modal, main-window-only notices, editors and explicit two-click consent."""

import math
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QLineEdit

from dtflow.ui.activity_strip import activity_strip


class InlineFeedback(QObject):
    """One persistent Designer surface; confirmations retain their original action button."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        file = QFile(str(Path(__file__).with_name("forms") / "inline_feedback.ui"))
        if not file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise OSError("Inline feedback form unavailable")
        try:
            self.form = QUiLoader().load(file, window)
        finally:
            file.close()
        if self.form is None:
            raise OSError("Inline feedback form could not be loaded")
        self.setParent(self.form)
        self.strip = activity_strip(window)
        self.strip.mount_feedback(self)
        self.form.hide()
        self.form.inlineDismissButton.clicked.connect(self.dismiss)
        self.form.inlineEditorCancelButton.clicked.connect(self.dismiss)
        self._armed = None
        self._editor = None
        self._accept_callback = None
        self._accept_button = None
        self._close_allowed = False
        self.form.inlineAcceptButton.hide()
        self.form.inlineEditorActions.hide()
        window._inline_feedback = self
        window.installEventFilter(self)
        self.escape = QShortcut(QKeySequence("Escape"), self.form)
        self.escape.activated.connect(self.dismiss)
        self.escape.setEnabled(False)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if self._armed or self._editor:
                self.dismiss()
                return True
        return False

    def reset(self):
        self.escape.setEnabled(False)
        armed, self._armed = self._armed, None
        if armed:
            button, text, accessible, tooltip, minimum, maximum, callback = armed
            button.setText(text)
            button.setAccessibleName(accessible)
            button.setToolTip(tooltip)
            button.setMinimumWidth(minimum)
            button.setMaximumWidth(maximum)
            button.setProperty("confirmationPending", False)
            button.style().unpolish(button)
            button.style().polish(button)
            if callback:
                callback()
        if self._editor is not None:
            self.form.inlineEditorLayout.removeWidget(self._editor)
            self._editor.deleteLater()
            self._editor = None
        self.form.inlineAcceptButton.hide()
        self.form.inlineEditorActions.hide()
        self.form.inlineDismissButton.show()
        if self._accept_callback is not None:
            self._accept_button.clicked.disconnect(self._accept_callback)
            self._accept_callback = None
            self._accept_button = None

    def dismiss(self):
        self.reset()
        self.form.hide()
        self.strip._position()

    def show(self, title, text, state="info"):
        self.reset()
        self.form.setProperty("feedbackState", state)
        self.form.inlineSymbol.setText("!" if state in {"error", "partial", "confirm"} else "i")
        self.form.style().unpolish(self.form)
        self.form.style().polish(self.form)
        self.form.inlineTitleLabel.setText(title)
        self.form.inlineTextBrowser.setPlainText(text)
        self.form.inlineTextBrowser.setVisible(bool(text))
        self.form.inlineDismissButton.setText("Dismiss")
        self.form.show()
        self.strip._position()

    def fit_text(self, width):
        """Fit short messages directly; retain scrolling for explicit long metadata inspection."""
        browser = self.form.inlineTextBrowser
        controls = self.form.inlineDismissButton.sizeHint().width()
        if not self.form.inlineAcceptButton.isHidden():
            controls += self.form.inlineAcceptButton.sizeHint().width() + 10
        document = browser.document()
        document.setDefaultFont(browser.font())
        document.setDocumentMargin(0)
        document.setTextWidth(max(40, width - controls - 90))
        height = min(150, max(16, math.ceil(document.size().height()) + 2))
        if browser.height() != height:
            browser.setFixedHeight(height)

    def editor(self, title, widget, accept, callback, *, primary=False):
        self.show(title, "")
        self._editor = widget
        self.escape.setEnabled(True)
        self.form.inlineEditorLayout.addWidget(widget)
        self.form.inlineDismissButton.hide()
        self.form.inlineEditorActions.show()
        self.set_accept(accept, callback, editor_action=True, primary=primary)
        widget.show()
        self.strip._position()

    def set_accept(self, text, callback, *, editor_action=False, primary=False):
        """Configure either the editor footer action or the header consent action."""
        button = self.form.inlineEditorAcceptButton if editor_action else self.form.inlineAcceptButton
        if self._accept_callback is not None:
            self._accept_button.clicked.disconnect(self._accept_callback)
            self._accept_button.hide()
        self._accept_callback = callback
        self._accept_button = button
        button.setProperty("role", "primary" if primary else "")
        button.style().unpolish(button)
        button.style().polish(button)
        button.setText(text)
        button.show()
        button.clicked.connect(callback)
        self.strip._position()

    def arm(self, button, title, text, callback, canceled=None):
        self.show(title, text, "confirm")
        self.form.inlineDismissButton.setText("Cancel")
        self.escape.setEnabled(True)
        # Only the label/color changes in the original slot; Cancel belongs to
        # the details surface, so the action row and pointer target do not move.
        if not getattr(button, "_inline_click_guard", None):
            button._inline_click_guard = _DistinctClickGuard(button)
            button.installEventFilter(button._inline_click_guard)
        self._armed = (
            button,
            button.text(),
            button.accessibleName(),
            button.toolTip(),
            button.minimumWidth(),
            button.maximumWidth(),
            canceled,
        )
        button.setFixedWidth(button.width())
        button.setText("Confirm")
        button.setAccessibleName("Confirm " + title)
        button.setToolTip(title + " — see the inline details before confirming")
        button.setProperty(
            "confirmationDanger",
            button.objectName()
            in {
                "galleryDeleteButton",
                "clearInventoryCacheButton",
                "clearThumbnailCacheButton",
                "clearDerivedCacheButton",
                "clearAllCacheButton",
                "clearCustomLorasButton",
                "clearQueuesButton",
            },
        )
        button.setProperty("confirmationPending", True)
        button.style().unpolish(button)
        button.style().polish(button)
        button._inline_commit = callback
        self.form.setProperty("feedbackState", "error" if button.property("confirmationDanger") else "confirm")
        self.form.style().unpolish(self.form)
        self.form.style().polish(self.form)
        self.strip._position()

    def commit(self, button):
        if not self._armed or self._armed[0] is not button:
            return False
        callback = button._inline_commit
        # Successful consent must not invoke the cancellation hook.
        armed = self._armed
        self._armed = (*armed[:-1], None)
        self.dismiss()
        callback()
        return True


class _DistinctClickGuard(QObject):
    """A double-click must not count as two independent consent activations."""

    def __init__(self, button):
        super().__init__(button)
        self.button = button
        self.ignore_release = False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonDblClick:
            self.ignore_release = True
            self.button.setDown(False)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self.ignore_release:
            self.ignore_release = False
            self.button.setDown(False)
            return True
        return False


def feedback(parent):
    window = parent.window()
    return window._inline_feedback


def confirm_action(parent, button, title, text, callback: Callable, canceled=None):
    feedback(parent).arm(button, title, text, callback, canceled)


class InlineNotice:
    """Drop-in presentation of informational call sites, without a dialog or nested event loop."""

    @staticmethod
    def information(parent, title, text):
        feedback(parent).show(title, text)

    @staticmethod
    def warning(parent, title, text):
        feedback(parent).show(title, text, "error")

    about = information


def edit_text(parent, title, prompt, callback, current=""):
    edit = QLineEdit()
    edit.setText(current)
    edit.setPlaceholderText(prompt)
    edit.setAccessibleName(prompt)
    panel = feedback(parent)

    def accept():
        value = edit.text().strip()
        if not value:
            edit.setFocus()
            return
        panel.dismiss()
        callback(value)

    panel.editor(title, edit, "Save", accept)
    edit.returnPressed.connect(accept)
    edit.setFocus()

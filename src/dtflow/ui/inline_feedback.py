"""Non-modal, main-window-only notices, editors and explicit two-click consent."""

import math
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget
from shiboken6 import isValid

from dtflow.ui.app_filters import install_app_filter, remove_app_filter
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.trash_icon import trash_icon_pixmap


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
        # Resolve the actual button rather than a QUiLoader name attribute that can
        # collide with a layout item wrapper during repeated Qt window lifecycles.
        self.form.inlineDismissButton = self.form.findChild(QPushButton, "inlineDismissButton")
        self.form.inlineDismissButton.clicked.connect(self.dismiss)
        self.form.inlineEditorCancelButton.clicked.connect(self.dismiss)
        self._armed = None
        self._outside = False
        self._editor = None
        self._suspended_editor = None
        self._suspended_shortcuts = (False, False)
        self._editor_focus = None
        self._accept_callback = None
        self._accept_button = None
        self._close_allowed = False
        self._notice_index = 0
        self.form.inlineAcceptButton.hide()
        self.form.inlineEditorActions.hide()
        window._inline_feedback = self
        window.installEventFilter(self)
        self.escape = QShortcut(QKeySequence("Escape"), self.form)
        self.escape.activated.connect(self.dismiss)
        # Logs registers its own Escape; Qt then delivers ambiguous presses to one shortcut in rotation.
        self.escape.activatedAmbiguously.connect(self.dismiss)
        self.escape.setEnabled(False)
        self.button_escape = QShortcut(QKeySequence("Escape"), window)
        self.button_escape.activated.connect(self.dismiss)
        self.button_escape.activatedAmbiguously.connect(self.dismiss)
        self.button_escape.setEnabled(False)
        QApplication.instance().focusChanged.connect(self._keep_editor_focus)

    def _keep_editor_focus(self, previous, current):
        """Keep keyboard traversal in the editor, without trapping other windows."""
        if (
            self._editor is None
            or self._suspended_editor is not None
            or current is None
            or not self.strip.scrim.isVisible()
        ):
            return
        if current.window() is not self.window:
            return
        if self.form.isAncestorOf(current):
            self._editor_focus = current
            return
        target = self._editor_focus
        if target is None or not self.form.isAncestorOf(target):
            target = self.form.inlineEditorCancelButton
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _inside(self, point) -> bool:
        """The press landed on this surface: the shown card, or the in-place button of a pending confirmation."""
        if self.form.isVisible() and self.form.rect().contains(self.form.mapFromGlobal(point)):
            return True
        armed = self._armed[0] if self._armed else None
        return (
            armed is not None
            and isValid(armed)
            and armed.isVisible()
            and armed.rect().contains(armed.mapFromGlobal(point))
        )

    def eventFilter(self, watched, event):
        if (
            self._outside
            and event.type() == QEvent.Type.MouseButtonPress
            and self._suspended_editor is None
            and isinstance(watched, QWidget)
            and watched.window() is self.window  # a combo popup, menu or dialog press is not an outside click
        ):
            # Any press outside dismisses every kind of notice. For a pending confirmation or an editor that is
            # Cancel (``dismiss`` runs the cancel hook / drops the editor), never Confirm, Save or Retry.
            if (self.form.isVisible() or self._armed) and not self._inside(event.globalPosition().toPoint()):
                self.dismiss()
            return False
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if self.active():
                self.dismiss()
                return True
        return False

    def active(self) -> bool:
        """True while a pending consent, editor or notice owns Escape."""
        return self._suspended_editor is None and bool(self._armed or self._editor or self.form.isVisible())

    def suspend_editor(self, editor) -> bool:
        """Keep the editor visible but inactive behind an in-window child workflow, preserving its draft."""
        if self._editor is not editor or self._suspended_editor is not None or not isValid(editor):
            return False
        self._suspended_editor = editor
        self._suspended_shortcuts = (self.escape.isEnabled(), self.button_escape.isEnabled())
        self.escape.setEnabled(False)
        self.button_escape.setEnabled(False)
        self.form.setEnabled(False)
        self.strip._position()
        return True

    def resume_editor(self, editor) -> bool:
        """Restore only the same parked editor; navigation/replacement/reset invalidates this permission."""
        if self._suspended_editor is not editor or self._editor is not editor or not isValid(editor):
            return False
        self._suspended_editor = None
        self.escape.setEnabled(self._suspended_shortcuts[0])
        self.button_escape.setEnabled(self._suspended_shortcuts[1])
        self.form.setEnabled(True)
        self.form.show()
        self.strip.set_editor_dimming(True)
        target = self._editor_focus
        if target is None or not isValid(target) or not self.form.isAncestorOf(target):
            target = self.form.inlineEditorCancelButton
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def owns_keyboard(self) -> bool:
        """True while a pending consent or inline editor takes keyboard input; a plain notice does not."""
        return self._armed is not None or self._editor is not None

    def reset(self):
        self.form.setEnabled(True)
        self._suspended_editor = None
        self._suspended_shortcuts = (False, False)
        self._watch_outside(False)
        self.escape.setEnabled(False)
        self.button_escape.setEnabled(False)
        self.strip.set_editor_dimming(False)
        self._editor_focus = None
        armed, self._armed = self._armed, None
        if armed:
            button, text, accessible, tooltip, minimum, maximum, callback = armed
            button.setText(text)
            button.setAccessibleName(accessible)
            button.setToolTip(tooltip)
            button.setMinimumWidth(minimum)
            button.setMaximumWidth(maximum)
            button.setProperty("confirmationPending", False)
            button.setProperty("confirmationDanger", False)
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

    def _set_dismiss_label(self, *, cross: bool) -> None:
        button = self.form.inlineDismissButton
        button.setText("×" if cross else "Cancel")
        button.setToolTip("Dismiss" if cross else "Cancel")
        button.setAccessibleName("Dismiss" if cross else "Cancel")

    def _watch_outside(self, enabled: bool) -> None:
        if enabled == self._outside:
            return
        self._outside = enabled
        (install_app_filter if enabled else remove_app_filter)(self)

    def dismiss(self):
        self._watch_outside(False)
        self.reset()
        self.form.hide()
        self.strip._position()

    def _set_feedback_state(self, state):
        """Refresh both the container and its icon after a semantic state change."""
        self.form.setProperty("feedbackState", state)
        self.form.inlineSymbol.setProperty("feedbackState", state)
        for widget in (self.form, self.form.inlineSymbol):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def notice(self, title, text, state="info"):
        """Keep unrelated notices visible without disarming a pending consent."""
        if state in {"warning", "partial", "stopped"} or self._armed is not None or self._editor is not None:
            self._notice_index += 1
            self.strip.complete(f"notice-{self._notice_index}", title, text, state)
            return
        self.show(title, text, state)

    def show(self, title, text, state="info"):
        self.reset()
        self._set_feedback_state(state)
        self.form.inlineSymbol.setText(
            "✓"
            if state == "success"
            else "■"
            if state == "stopped"
            else "!"
            if state in {"error", "partial", "warning"}
            else "i"
        )
        self.escape.setEnabled(True)
        self.form.inlineTitleLabel.setText(title)
        self.form.inlineTextBrowser.setPlainText(text)
        self.form.inlineTextBrowser.setVisible(bool(text))
        self._set_dismiss_label(cross=True)
        self.form.show()
        # A press outside closes any notice; for consent and editors it acts as Cancel.
        self._watch_outside(True)
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

    def editor(self, title, widget, accept, callback, *, primary=False, dismiss_text="Cancel", dismiss_tooltip=None):
        self.show(title, "")
        self._editor = widget
        self.escape.setEnabled(True)
        self.form.inlineEditorLayout.addWidget(widget)
        self.form.inlineDismissButton.hide()
        self.form.inlineEditorActions.show()
        dismiss = self.form.inlineEditorCancelButton
        dismiss.setText(dismiss_text)
        dismiss.setAccessibleName(dismiss_text)
        dismiss.setToolTip(dismiss_tooltip or dismiss_text)
        self.set_accept(accept, callback, editor_action=True, primary=primary)
        widget.show()
        self.strip.set_editor_dimming(True)

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
        button.setEnabled(
            True
        )  # the footer button is shared: an earlier editor (Prepare, templates) may have left it off
        button.show()
        button.clicked.connect(callback)
        self.strip._position()

    def arm(self, button, title, text, callback, canceled=None, *, danger=False, confirm_text="Confirm", overlay=True):
        if overlay:
            self.show(title, text, "confirm")
        else:
            self.dismiss()
            self.button_escape.setEnabled(True)
        self._set_dismiss_label(cross=False)
        self.escape.setEnabled(overlay)
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
        button.setText(confirm_text)
        button.setAccessibleName("Confirm " + title)
        button.setToolTip(
            title
            + (
                " — see the inline details before confirming"
                if overlay
                else " — click again to confirm; Escape cancels"
            )
        )
        button.setProperty("confirmationDanger", danger)
        button.setProperty("confirmationPending", True)
        button.style().unpolish(button)
        button.style().polish(button)
        button._inline_commit = callback
        self._set_feedback_state("error" if danger else "confirm")
        self._watch_outside(True)  # also for the in-place variant, which shows no card
        if danger and title.startswith("Move "):
            self.form.inlineSymbol.setPixmap(trash_icon_pixmap())
        else:
            self.form.inlineSymbol.setText("▤" if "cache" in title.lower() else "!" if danger else "i")
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


def confirm_action(
    parent, button, title, text, callback: Callable, canceled=None, *, danger=False, confirm_text="Confirm"
):
    feedback(parent).arm(button, title, text, callback, canceled, danger=danger, confirm_text=confirm_text)


class InlineNotice:
    """Drop-in presentation of informational call sites, without a dialog or nested event loop."""

    @staticmethod
    def information(parent, title, text):
        feedback(parent).notice(title, text)

    @staticmethod
    def warning(parent, title, text):
        feedback(parent).notice(title, text, "warning")

    @staticmethod
    def error(parent, title, text):
        feedback(parent).notice(title, text, "error")

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

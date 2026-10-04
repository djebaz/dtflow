"""Main window: presentation and wiring only. Backend access and credentials live in the shared services."""

from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QWidget,
)

from dtflow.build import display_identity
from dtflow.composition import BACKENDS, BackendSelection
from dtflow.core.models import BackendError, CredentialProvider, ValidationError
from dtflow.paths import application_state_dir
from dtflow.services.connection import Presentation, check_connection, present_failure, present_status
from dtflow.services.credentials import SavedCredentials, SecureStorageUnavailable, credential_status
from dtflow.ui.activity import Activity
from dtflow.ui.generation_page import GenerationPage
from dtflow.ui.queue_page import QueuePage
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.theme import apply_native_dark_titlebar

FORMS_DIR = Path(__file__).with_name("forms")
UI_FILE = FORMS_DIR / "main_window.ui"
GENERATION_UI_FILE = FORMS_DIR / "generation_page.ui"
QUEUE_UI_FILE = FORMS_DIR / "queue_page.ui"
PREVIEW_CONTROL_TYPES = (
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QAbstractItemView,
)


def load_ui(path: Path) -> QWidget:
    """Load a canonical Designer form without committing generated Python."""
    file = QFile(str(path))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"{path.name} could not be opened.")
    try:
        widget = QUiLoader().load(file)
    finally:
        file.close()
    if widget is None:
        raise OSError(f"{path.name} could not be loaded.")
    return widget


def load_form() -> QMainWindow:
    """Load the canonical main-window form."""
    window = load_ui(UI_FILE)
    if not isinstance(window, QMainWindow):
        raise TypeError("main_window.ui must define a QMainWindow.")
    return window


class _CloseGuard(QObject):
    """Ask before a window close that would stop an active queue dispatch."""

    def __init__(self, confirm, parent: QObject):
        super().__init__(parent)
        self._confirm = confirm

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Close and not self._confirm():
            event.ignore()
            return True
        return False


class MainWindow:
    """Owns the Designer form and an AsyncRunner; ``saved`` is injectable for tests."""

    def __init__(
        self,
        *,
        saved: SavedCredentials | None = None,
        runner: AsyncRunner | None = None,
        environ=None,
        state_dir: Path | None = None,
    ):
        self.form = load_form()
        self.generation_page = load_ui(GENERATION_UI_FILE)
        self.queue_page = load_ui(QUEUE_UI_FILE)
        self._disable_preview_controls(self.generation_page)
        self._disable_preview_controls(self.queue_page)
        self.form.workspaceStack.addWidget(self.generation_page)
        self.form.workspaceStack.addWidget(self.queue_page)

        self._saved = saved or SavedCredentials()
        self._environ = environ
        self.runner = runner or AsyncRunner(self.form)
        self.state_dir = Path(state_dir) if state_dir is not None else application_state_dir()
        self._busy = False
        self.activity = Activity()
        form = self.form
        self.generation = GenerationPage(
            self.generation_page,
            runner=self.runner,
            backend_source=lambda: (form.backendCombo.currentText(), form.endpointEdit.text() or None),
            saved_source=lambda: self._saved if self._saved.available() else None,
            state_dir=self.state_dir,
            activity=self.activity,
        )
        self.queue = QueuePage(
            self.queue_page,
            runner=self.runner,
            backend_source=lambda: (form.backendCombo.currentText(), form.endpointEdit.text() or None),
            saved_source=lambda: self._saved if self._saved.available() else None,
            state_dir=self.state_dir,
            request_source=self.generation.request_for_queue,
            activity=self.activity,
        )
        form.backendCombo.addItems(BACKENDS)
        form.statusBar().showMessage(display_identity() + f" | state: {self.state_dir}")
        form.checkButton.clicked.connect(self.check)
        form.saveKeyButton.clicked.connect(self.save_key)
        form.removeKeyButton.clicked.connect(self.remove_key)
        form.aboutAction.triggered.connect(self.about)
        form.backendCombo.currentTextChanged.connect(self._backend_changed)
        form.connectionNavigationButton.clicked.connect(
            lambda: form.workspaceStack.setCurrentWidget(form.connectionPage)
        )
        form.generationNavigationButton.clicked.connect(self._show_generation)
        form.backendCombo.currentTextChanged.connect(lambda _: self.generation.sync_backend())
        form.queueNavigationButton.clicked.connect(self._show_queue)
        self.form.installEventFilter(_CloseGuard(self.queue.confirm_close, self.form))
        self._backend_changed(form.backendCombo.currentText())
        self.generation.sync_backend()
        self.refresh_credentials()

    @staticmethod
    def _disable_preview_controls(page: QWidget) -> None:
        for control_type in PREVIEW_CONTROL_TYPES:
            for control in page.findChildren(control_type):
                control.setEnabled(False)

    def _show_queue(self) -> None:
        self.form.workspaceStack.setCurrentWidget(self.queue_page)
        self.queue.refresh()

    def _show_generation(self) -> None:
        self.generation.sync_backend()
        self.form.workspaceStack.setCurrentWidget(self.generation_page)

    def show(self) -> None:
        apply_native_dark_titlebar(self.form)
        self.form.show()
        apply_native_dark_titlebar(self.form)

    def _backend_changed(self, name: str) -> None:
        self.form.endpointEdit.setEnabled(name == "local")
        self.form.credentialBox.setEnabled(name == "cloud")

    def _present(self, presentation: Presentation) -> None:
        label = self.form.statusLabel
        for widget in (label, self.form.statusGlyphLabel, self.form.statusCard):
            widget.setProperty("state", presentation.level)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        label.setText(f"{presentation.headline}. {presentation.detail}".strip())

    def refresh_credentials(self) -> None:
        status = credential_status(self._environ, self._saved)
        text = {
            "environment": "Using DRAWTHINGS_API_KEY from the environment; a saved key is not consulted.",
            "saved": "Using the key saved in Windows Credential Manager.",
            "none": "No API key available."
            + (" Secure storage is unavailable here." if status["saved"] == "unavailable" else ""),
        }[status["selected"]]
        self.form.credentialStatusLabel.setText(text)
        self.form.keyEdit.clear()

    def check(self) -> None:
        if self._busy:
            return
        selection = BackendSelection(self.form.backendCombo.currentText(), self.form.endpointEdit.text() or None)
        self._busy = True
        self.form.checkButton.setEnabled(False)
        self._present(Presentation("warning", "Checking", ""))
        saved: CredentialProvider | None = self._saved if self._saved.available() else None
        self.runner.submit(self._check(selection, saved), self._checked)

    async def _check(self, selection, saved):
        return await check_connection(selection, saved=saved)

    def _checked(self, status, failure) -> None:
        self._busy = False
        self.form.checkButton.setEnabled(True)
        self._present(present_failure(failure) if failure else present_status(status))

    def save_key(self) -> None:
        try:
            self._saved.save(self.form.keyEdit.text())
        except (BackendError, ValidationError, SecureStorageUnavailable) as exc:
            code = exc.failure.code if isinstance(exc, BackendError) else str(exc)
            self.form.credentialStatusLabel.setText(f"Key not saved: {code}")
            self.form.keyEdit.clear()
            return
        self.refresh_credentials()

    def remove_key(self) -> None:
        try:
            self._saved.remove()
        except BackendError as exc:
            self.form.credentialStatusLabel.setText(f"Key not removed: {exc.failure.code}")
            return
        self.refresh_credentials()

    def about(self) -> None:
        QMessageBox.about(self.form, "About DTFlow", display_identity())

    def close(self) -> None:
        self.runner.shutdown()
        self.form.close()

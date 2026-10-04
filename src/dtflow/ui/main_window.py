"""Main window: presentation and wiring only. Backend access and credentials live in the shared services."""

from pathlib import Path

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QMainWindow, QMessageBox

from dtflow.build import display_identity
from dtflow.composition import BACKENDS, BackendSelection
from dtflow.core.models import BackendError, CredentialProvider, ValidationError
from dtflow.paths import application_state_dir
from dtflow.services.connection import Presentation, check_connection, present_failure, present_status
from dtflow.services.credentials import SavedCredentials, SecureStorageUnavailable, credential_status
from dtflow.ui.runner import AsyncRunner

UI_FILE = Path(__file__).with_name("main_window.ui")
STYLE = {"ok": "color: #1a7f37;", "warning": "color: #9a6700;", "error": "color: #cf222e;"}


def load_form() -> QMainWindow:
    """Load the canonical Designer file; no generated Python is committed."""
    file = QFile(str(UI_FILE))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError("main_window.ui could not be opened.")
    try:
        window = QUiLoader().load(file)
    finally:
        file.close()
    if window is None:
        raise OSError("main_window.ui could not be loaded.")
    return window


class MainWindow:
    """Owns the Designer form and an AsyncRunner; ``saved`` is injectable for tests."""

    def __init__(self, *, saved: SavedCredentials | None = None, runner: AsyncRunner | None = None, environ=None):
        self.form = load_form()
        self._saved = saved or SavedCredentials()
        self._environ = environ
        self.runner = runner or AsyncRunner(self.form)
        self._busy = False
        form = self.form
        form.backendCombo.addItems(BACKENDS)
        form.statusBar().showMessage(display_identity() + f" | state: {application_state_dir()}")
        form.checkButton.clicked.connect(self.check)
        form.saveKeyButton.clicked.connect(self.save_key)
        form.removeKeyButton.clicked.connect(self.remove_key)
        form.aboutAction.triggered.connect(self.about)
        form.backendCombo.currentTextChanged.connect(self._backend_changed)
        self._backend_changed(form.backendCombo.currentText())
        self.refresh_credentials()

    def show(self) -> None:
        self.form.show()

    def _backend_changed(self, name: str) -> None:
        self.form.endpointEdit.setEnabled(name == "local")
        self.form.credentialBox.setEnabled(name == "cloud")

    def _present(self, presentation: Presentation) -> None:
        label = self.form.statusLabel
        label.setStyleSheet(STYLE[presentation.level])
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

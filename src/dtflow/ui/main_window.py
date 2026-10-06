"""Main window: presentation and wiring only. Backend access and credentials live in the shared services."""

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QPixmap
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
from dtflow.persistence.last_session import load_last_session, save_last_session
from dtflow.services.connection import Presentation, check_connection, present_failure, present_status
from dtflow.services.credentials import SavedCredentials, SecureStorageUnavailable, credential_status
from dtflow.ui.activity import Activity
from dtflow.ui.brand import connection_icon
from dtflow.ui.gallery_detail_preview import DetailFixture
from dtflow.ui.generation_page import GenerationPage
from dtflow.ui.image_preview import ImagePreview
from dtflow.ui.queue_page import QueuePage, ask_confirmation
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.theme import apply_native_dark_titlebar

log = logging.getLogger(__name__)

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
        loader = QUiLoader()
        loader.registerCustomWidget(ImagePreview)
        widget = loader.load(file)
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
        self.ask_confirmation = confirm

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Close and not self.ask_confirmation():
            event.ignore()
            return True
        return False


class _LayoutGuard(QObject):
    def __init__(self, callback, parent):
        super().__init__(parent)
        self.callback = callback

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.callback()
        return False


class _NavigationFocusGuard(QObject):
    """Keep mouse focus from disguising status; preserve a visible keyboard focus cue."""

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            keyboard = event.type() == QEvent.Type.FocusIn and event.reason() in (
                Qt.FocusReason.TabFocusReason,
                Qt.FocusReason.BacktabFocusReason,
                Qt.FocusReason.ShortcutFocusReason,
            )
            watched.setProperty("keyboardFocus", keyboard)
            watched.style().unpolish(watched)
            watched.style().polish(watched)
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
        no_cache: bool = False,
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
        self.confirm = ask_confirmation
        self.activity = Activity()
        form = self.form
        self.shell = load_ui(FORMS_DIR / "workspace_shell.ui")
        # Mount presentation regions once; page controllers retain their widget references.
        for page, names in (
            (form, ("contextRail", "workspaceFrame", "inspectorPanel")),
            (self.generation_page, ("generationContextPanel", "generationWorkspace", "generationInspector")),
            (self.queue_page, ("queueContextPanel", "queueWorkspace", "queueInspector")),
        ):
            for stack, name in zip(
                (self.shell.leftStack, self.shell.centerStack, self.shell.rightStack), names, strict=True
            ):
                stack.addWidget(getattr(page, name))
        self.images_page = QWidget()  # Navigation marker; canonical content stays mounted in the shell.
        self.gallery_detail = load_ui(FORMS_DIR / "gallery_detail_page.ui")
        self.gallery_preview = DetailFixture(self.gallery_detail)
        form.workspaceStack.addWidget(self.images_page)
        self.shell.leftStack.addWidget(load_ui(FORMS_DIR / "gallery_context.ui"))
        self.shell.centerStack.addWidget(self.gallery_detail)
        self.shell.rightStack.addWidget(QWidget())
        form.workspaceStack.hide()
        form.rootLayout.addWidget(self.shell)
        # Preserve the connection controller's established chrome references.
        for name in (
            "brandFrame",
            "navigationFrame",
            "productMarkLabel",
            "productSubtitleLabel",
            "connectionNavigationButton",
            "generationNavigationButton",
            "queueNavigationButton",
            "imagesNavigationButton",
        ):
            setattr(form, name, getattr(self.shell, name))
        self.shell.productSubtitleLabel.hide()
        form.connectionNavigationButton.installEventFilter(_NavigationFocusGuard(form.connectionNavigationButton))
        # Existing inspection integrations can refer to the same persistent shell controls.
        self.generation_page.inspectorToggle = self.shell.inspectorToggle
        self.generation_page.generationLeftRail = self.shell.leftRail
        self.generation_page.generationRightRail = self.shell.rightRail
        self.shell.workspaceSplitter.setStretchFactor(0, 0)
        self.shell.workspaceSplitter.setStretchFactor(1, 1)
        self.shell.workspaceSplitter.setStretchFactor(2, 0)
        self.shell.workspaceSplitter.setSizes([260, 700, 300])
        from dtflow.ui.brand import APP_ICON, app_icon, panel_icon

        form.setWindowIcon(app_icon())
        self.shell.inspectorToggle.setIcon(panel_icon())
        logo = QPixmap(str(APP_ICON))
        if not logo.isNull():
            form.productMarkLabel.setPixmap(
                logo.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        policy = self.shell.inspectorToggle.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.shell.inspectorToggle.setSizePolicy(policy)
        self._gallery_chrome_active = False
        self._gallery_previous_sizes = None
        self._narrow = None
        self._inspector_wanted, self._inspector_auto = True, False
        self.generation = GenerationPage(
            self.generation_page,
            runner=self.runner,
            backend_source=lambda: (form.backendCombo.currentText(), form.endpointEdit.text() or None),
            saved_source=lambda: self._saved if self._saved.available() else None,
            state_dir=self.state_dir,
            activity=self.activity,
            no_cache=no_cache,
        )
        self.queue = QueuePage(
            self.queue_page,
            runner=self.runner,
            backend_source=lambda: (form.backendCombo.currentText(), form.endpointEdit.text() or None),
            saved_source=lambda: self._saved if self._saved.available() else None,
            state_dir=self.state_dir,
            request_source=self.generation.request_for_queue,
            simulation_source=self.generation.simulation,
            created_hook=self.generation.queue_created,
            activity=self.activity,
        )
        form.backendCombo.addItems(BACKENDS)
        self._restore_session()
        form.statusBar().showMessage(display_identity())
        form.checkButton.clicked.connect(self.check)
        form.saveKeyButton.clicked.connect(self.save_key)
        form.removeKeyButton.clicked.connect(self.remove_key)
        form.aboutAction.triggered.connect(self.about)
        form.backendCombo.currentTextChanged.connect(self._backend_changed)
        form.endpointEdit.textChanged.connect(self._selection_changed)
        form.endpointEdit.editingFinished.connect(self._endpoint_entered)
        self.generation_page.generateButton.clicked.connect(self.remember)
        form.connectionNavigationButton.clicked.connect(
            lambda: form.workspaceStack.setCurrentWidget(form.connectionPage)
        )
        form.generationNavigationButton.clicked.connect(self._show_generation)
        form.backendCombo.currentTextChanged.connect(lambda _: self.generation.sync_backend())
        form.queueNavigationButton.clicked.connect(self._show_queue)
        form.imagesNavigationButton.setEnabled(True)
        form.imagesNavigationButton.setToolTip("Show Images — offline layout preview, not saved outputs")
        form.imagesNavigationButton.setAccessibleName("Show Images layout preview")
        form.imagesNavigationButton.clicked.connect(lambda: form.workspaceStack.setCurrentWidget(self.images_page))
        form.workspaceStack.currentChanged.connect(self._workspace_changed)
        self.shell.inspectorToggle.toggled.connect(self._toggle_inspector)
        form.installEventFilter(_LayoutGuard(self._responsive_workspace, form))
        self.form.installEventFilter(_CloseGuard(self.queue.confirm_close, self.form))
        log.info("window ready state=%s", "default" if state_dir is None else "custom")
        self._backend_changed(form.backendCombo.currentText())
        self.generation.sync_backend()
        self.generation.request_cached()
        self.refresh_credentials()
        self._workspace_changed(0)
        self._responsive_workspace()

    @staticmethod
    def _disable_preview_controls(page: QWidget) -> None:
        for control_type in PREVIEW_CONTROL_TYPES:
            for control in page.findChildren(control_type):
                # A spin box's or combo box's own editor follows its owner; disabling it separately would leave the
                # value greyed out and untypeable after the owner is re-enabled.
                if not isinstance(control.parent(), (QAbstractSpinBox, QComboBox)):
                    control.setEnabled(False)

    def _show_queue(self) -> None:
        self.form.workspaceStack.setCurrentWidget(self.queue_page)
        self.queue.refresh()

    def _show_generation(self) -> None:
        self.generation.sync_backend()
        self.form.workspaceStack.setCurrentWidget(self.generation_page)

    def _workspace_changed(self, index: int) -> None:
        """Switch content and use the compact full-width chrome for Images only."""
        for stack in (self.shell.leftStack, self.shell.centerStack, self.shell.rightStack):
            stack.setCurrentIndex(index)
        buttons = (
            self.form.connectionNavigationButton,
            self.form.generationNavigationButton,
            self.form.queueNavigationButton,
            self.form.imagesNavigationButton,
        )
        buttons[index].setChecked(True)
        self._responsive_workspace()

    def _toggle_inspector(self, visible: bool) -> None:
        if not self._inspector_auto:
            self._inspector_wanted = visible  # the user's own choice survives crossing the narrow breakpoint
        self._responsive_workspace()

    def _responsive_workspace(self) -> None:
        shell = self.shell
        narrow = self.form.width() < 1100
        if narrow != self._narrow:
            self._narrow = narrow
            self._inspector_auto = True
            shell.inspectorToggle.setChecked(self._inspector_wanted and not narrow)
            self._inspector_auto = False
        images = self.form.workspaceStack.currentWidget() is self.images_page
        if images != self._gallery_chrome_active:
            self._gallery_chrome_active = images
            shell.setProperty("galleryMode", images)
            shell.style().unpolish(shell)
            shell.style().polish(shell)
            if images:
                self._gallery_previous_sizes = shell.workspaceSplitter.sizes()
                # Match the mounted shell chrome before moving it into the gallery rows.
                for slot, frame in (
                    (self.gallery_detail.galleryBrandSlot, shell.brandFrame),
                    (self.gallery_detail.galleryNavigationSlot, shell.navigationFrame),
                ):
                    slot.setFixedSize(shell.leftRail.width(), frame.height())
                self.gallery_detail.galleryHeaderScroll.setFixedHeight(shell.brandFrame.height())
                self.gallery_detail.galleryBrandSlotLayout.addWidget(shell.brandFrame)
                self.gallery_detail.galleryNavigationSlotLayout.addWidget(shell.navigationFrame)
            else:
                shell.leftRailLayout.insertWidget(0, shell.brandFrame)
                shell.leftRailLayout.insertWidget(1, shell.navigationFrame)
            shell.brandFrame.show()
            shell.navigationFrame.show()
            self.gallery_detail.galleryBrandSlot.setVisible(images)
            self.gallery_detail.galleryNavigationSlot.setVisible(images)
            shell.leftRail.setVisible(not images)
            if not images and self._gallery_previous_sizes:
                shell.workspaceSplitter.setSizes(self._gallery_previous_sizes)
        shell.inspectorToggle.setVisible(not images)
        shell.inspectorToggle.setEnabled(not images)
        shell.rightRail.setVisible(not images and shell.inspectorToggle.isChecked())

    def show(self) -> None:
        apply_native_dark_titlebar(self.form)
        self.form.show()
        apply_native_dark_titlebar(self.form)

    # -- remembered session -----------------------------------------------------------------------------------------
    def _restore_session(self) -> None:
        """Bring back the last provider, endpoint and Generate form; anything unusable leaves the defaults."""
        document = load_last_session(self.state_dir)
        if document is None:
            return
        provider, endpoint = document.get("provider"), document.get("endpoint")
        if provider in BACKENDS:
            self.form.backendCombo.setCurrentText(provider)
        if isinstance(endpoint, str):
            self.form.endpointEdit.setText(endpoint)
        self.generation.restore(document.get("form"), document.get("forms"))
        log.info("last session restored provider=%s", provider if provider in BACKENDS else "-")

    def _session_document(self) -> dict:
        return {
            "provider": self.form.backendCombo.currentText(),
            "endpoint": self.form.endpointEdit.text().strip(),
            "form": self.generation.form_document(),
            "forms": self.generation.forms_document(),
        }

    def remember(self, *_, background: bool = True) -> None:
        """Save the current session; small, but off the GUI thread unless the window is closing."""
        document = self._session_document()
        if not background:
            self._write_session(document)
            return

        async def write():
            await asyncio.to_thread(self._write_session, document)

        try:
            self.runner.submit(write(), lambda *_: None)
        except RuntimeError:
            self._write_session(document)

    def _write_session(self, document: dict) -> None:
        try:
            save_last_session(self.state_dir, document)
            log.info("last session saved")
        except OSError as exc:
            log.warning("last session not saved reason=%s", type(exc).__name__)

    def _endpoint_entered(self) -> None:
        log.info("endpoint changed backend=%s", self.form.backendCombo.currentText())

    def _backend_changed(self, name: str) -> None:
        log.info("provider changed backend=%s", name)
        self.form.endpointEdit.setEnabled(name == "local")
        self.form.credentialBox.setEnabled(name == "cloud")
        self._selection_changed()

    def _selection_changed(self, *_) -> None:
        """A result belongs to the selection that was checked; a new provider or endpoint starts unchecked."""
        self._selection_revision = getattr(self, "_selection_revision", 0) + 1
        self._present(
            Presentation("unknown", "Not checked", f"Check the {self.form.backendCombo.currentText()} connection.")
        )

    def _present(self, presentation: Presentation) -> None:
        label = self.form.statusLabel
        for widget in (label, self.form.statusGlyphLabel, self.form.statusCard):
            widget.setProperty("state", presentation.level)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        label.setText(f"{presentation.headline}. {presentation.detail}".strip())
        button = self.form.connectionNavigationButton
        button.setProperty("state", presentation.level)
        button.setIcon(connection_icon(presentation.level))
        status = f"Connection ({self.form.backendCombo.currentText()}): {label.text()}"
        button.setToolTip(f"Show connection workspace. Last explicit check: {status}")
        button.setAccessibleName(status)
        button.style().unpolish(button)
        button.style().polish(button)

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
        # Remove is offered only when there may be a saved key to remove (the environment hides that answer).
        self.form.removeKeyButton.setEnabled(status["saved"] in ("present", "not consulted"))

    def check(self) -> None:
        if self._busy:
            return
        selection = BackendSelection(self.form.backendCombo.currentText(), self.form.endpointEdit.text() or None)
        self._busy = True
        self.form.checkButton.setEnabled(False)
        self._present(Presentation("warning", "Checking", ""))
        log.info("connection check started backend=%s", selection.name)
        saved: CredentialProvider | None = self._saved if self._saved.available() else None
        revision = self._selection_revision
        self.runner.submit(
            self._check(selection, saved), lambda status, failure: self._checked(status, failure, revision)
        )

    async def _check(self, selection, saved):
        return await check_connection(selection, saved=saved)

    def _checked(self, status, failure, revision=None) -> None:
        self._busy = False
        self.form.checkButton.setEnabled(True)
        if revision is not None and revision != self._selection_revision:
            return  # The provider/endpoint changed while the old check was running.
        shown = present_failure(failure) if failure else present_status(status)
        log.info("connection check result level=%s headline=%s", shown.level, shown.headline)
        self._present(shown)
        if failure is None and shown.level == "ok":
            self.remember()

    def save_key(self) -> None:
        try:
            self._saved.save(self.form.keyEdit.text())
        except (BackendError, ValidationError, SecureStorageUnavailable) as exc:
            code = exc.failure.code if isinstance(exc, BackendError) else str(exc)
            log.warning("api key not saved reason=%s", type(exc).__name__)
            self.form.credentialStatusLabel.setText(f"Key not saved: {code}")
            self.form.keyEdit.clear()
            return
        log.info("api key saved")
        self.refresh_credentials()

    def remove_key(self) -> None:
        if not self.confirm(
            self.form,
            "Remove saved key",
            "Remove the DT+ API key saved in Windows Credential Manager? Cloud checks and generations will need a key "
            "again.",
        ):
            return
        try:
            self._saved.remove()
        except BackendError as exc:
            log.warning("api key not removed code=%s", exc.failure.code)
            self.form.credentialStatusLabel.setText(f"Key not removed: {exc.failure.code}")
            return
        log.info("api key removed")
        self.refresh_credentials()

    def about(self) -> None:
        QMessageBox.about(self.form, "About DTFlow", f"{display_identity()}\n\nState folder: {self.state_dir}")

    def close(self) -> None:
        self.remember(background=False)
        log.info("window closed")
        self.runner.shutdown()
        self.form.close()

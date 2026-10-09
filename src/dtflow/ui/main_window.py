"""Main window: presentation and wiring only. Backend access and credentials live in the shared services."""

import asyncio
import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QFileDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QWidget,
)

from dtflow.build import display_identity
from dtflow.composition import BACKENDS, BackendSelection
from dtflow.core.gallery import GalleryQuery
from dtflow.core.models import BackendError, CredentialProvider, ValidationError
from dtflow.paths import application_state_dir
from dtflow.persistence.image_recycle import reconcile as reconcile_images
from dtflow.persistence.original_io import original_images_lock
from dtflow.persistence.cache_coordination import CacheBusyError, CacheCleanupError, CacheSafetyError
from dtflow.persistence.last_session import load_last_session, save_last_session
from dtflow.services.cache_management import CATEGORIES, cache_usage, purge_cache
from dtflow.services.catalogue import Catalogue, import_user_loras
from dtflow.services.connection import Presentation, check_connection, present_failure, present_status
from dtflow.services.credentials import SavedCredentials, SecureStorageUnavailable, credential_status
from dtflow.services.presets import export_presets, import_presets, load_presets
from dtflow.ui.activity import Activity
from dtflow.ui.brand import connection_icon
from dtflow.ui.button_focus import KeyboardFocusGuard
from dtflow.ui.gallery_page import GalleryPage
from dtflow.ui.generation_page import GenerationPage
from dtflow.ui.image_export_panel import ImageExportPanel
from dtflow.ui.image_recycle_panel import ImageRecyclePanel
from dtflow.ui.image_preview import ImagePreview
from dtflow.ui.queue_page import QueuePage, ask_confirmation
from dtflow.ui.run_control import RunControl
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.search_select import SearchableComboBox
from dtflow.ui.theme import apply_native_dark_titlebar

log = logging.getLogger(__name__)

RUN_SETTLE_MS = 2000  # how long the header control shows its finishing state after work ends

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
        loader.registerCustomWidget(SearchableComboBox)
        loader.registerCustomWidget(RunControl)
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
    def __init__(self, callback, parent, events=(QEvent.Type.Resize,)):
        super().__init__(parent)
        self.callback = callback
        self.events = events

    def eventFilter(self, watched, event):
        if event.type() in self.events:
            self.callback()
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
        self.gallery = GalleryPage(
            self.gallery_detail,
            runner=self.runner,
            state_dir=self.state_dir,
            reuse_callback=self._reuse_output,
            regenerate_callback=self._regenerate_output,
            export_callback=self._open_checked_export,
            recycle_callback=self._request_gallery_recycle if os.name == "nt" else None,
        )
        self.gallery_preview = self.gallery  # retain the established integration attribute
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
        form.connectionNavigationButton.installEventFilter(KeyboardFocusGuard(form.connectionNavigationButton))
        # Existing inspection integrations can refer to the same persistent shell rails.
        self.generation_page.generationLeftRail = self.shell.leftRail
        self.generation_page.generationRightRail = self.shell.rightRail
        self.shell.workspaceSplitter.setStretchFactor(0, 0)
        self.shell.workspaceSplitter.setStretchFactor(1, 1)
        self.shell.workspaceSplitter.setStretchFactor(2, 0)
        self.shell.workspaceSplitter.setSizes([260, 700, 300])
        from dtflow.ui.brand import APP_ICON, app_icon

        form.setWindowIcon(app_icon())
        logo = QPixmap(str(APP_ICON))
        if not logo.isNull():
            form.productMarkLabel.setPixmap(
                logo.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        self._gallery_chrome_active = False
        self._gallery_previous_sizes = None
        self.generation = GenerationPage(
            self.generation_page,
            runner=self.runner,
            backend_source=lambda: (form.backendCombo.currentText(), form.endpointEdit.text() or None),
            saved_source=lambda: self._saved if self._saved.available() else None,
            state_dir=self.state_dir,
            activity=self.activity,
            no_cache=no_cache,
            cache_changed=self.refresh_cache,
        )
        self.image_export = ImageExportPanel(
            form,
            runner=self.runner,
            state_dir=self.state_dir,
            selection=self._export_selection,
            no_cache=no_cache,
        )
        self.image_export.cache_changed.connect(self.refresh_cache)
        self.image_recycle = ImageRecyclePanel(
            form, runner=self.runner, state_dir=self.state_dir,
            gallery=self.gallery, busy_source=self._recycle_busy,
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
        form.importCustomLorasButton.clicked.connect(self.import_custom_loras)
        form.clearCustomLorasButton.clicked.connect(self.clear_custom_loras)
        form.importPresetsButton.clicked.connect(self.import_presets)
        form.exportPresetsButton.clicked.connect(self.export_presets)
        form.clearInventoryCacheButton.clicked.connect(lambda: self.clear_cache(("inventory",)))
        form.clearThumbnailCacheButton.clicked.connect(lambda: self.clear_cache(("thumbnails",)))
        form.clearDerivedCacheButton.clicked.connect(lambda: self.clear_cache(("derived",)))
        form.clearAllCacheButton.clicked.connect(lambda: self.clear_cache(CATEGORIES))
        form.aboutAction.triggered.connect(self.about)
        form.backendCombo.currentTextChanged.connect(self._backend_changed)
        form.endpointEdit.textChanged.connect(self._selection_changed)
        form.endpointEdit.editingFinished.connect(self._endpoint_entered)
        form.endpointEdit.editingFinished.connect(self.generation.sync_backend)
        self.generation_page.generateButton.clicked.connect(self.remember)
        form.connectionNavigationButton.clicked.connect(
            lambda: form.workspaceStack.setCurrentWidget(form.connectionPage)
        )
        form.generationNavigationButton.clicked.connect(self._show_generation)
        form.backendCombo.currentTextChanged.connect(lambda _: self.generation.sync_backend())
        form.queueNavigationButton.clicked.connect(self._show_queue)
        form.imagesNavigationButton.setEnabled(True)
        form.imagesNavigationButton.setToolTip("Browse committed saved outputs")
        form.imagesNavigationButton.setAccessibleName("Show saved Images")
        form.imagesNavigationButton.clicked.connect(lambda: form.workspaceStack.setCurrentWidget(self.images_page))
        form.workspaceStack.currentChanged.connect(self._workspace_changed)
        self.shell.runControl.clicked.connect(self.queue.trigger_run_control)
        self.shell.brandFrame.layout().setAlignment(self.shell.runControl, Qt.AlignmentFlag.AlignVCenter)
        self._run_was_running = False
        self._run_settle = QTimer(form)
        self._run_settle.setSingleShot(True)
        self._run_settle.setInterval(RUN_SETTLE_MS)
        self._run_settle.timeout.connect(self._sync_run_control)
        self.activity.listen(self._sync_run_control)
        self._sync_run_control()
        form.installEventFilter(_LayoutGuard(self._responsive_workspace, form))
        for frame in (self.shell.brandFrame, self.shell.navigationFrame):
            # Layout settles after the first show and after tab changes, not only on resize.
            frame.installEventFilter(
                _LayoutGuard(
                    self._align_run_control,
                    frame,
                    (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest),
                )
            )
        for widget in (self.shell.imagesNavigationButton, self.shell.runControl):
            widget.installEventFilter(
                _LayoutGuard(self._align_run_control, form, (QEvent.Type.Resize, QEvent.Type.Move))
            )
        self.form.installEventFilter(_CloseGuard(self._confirm_close, self.form))
        log.info("window ready state=%s", "default" if state_dir is None else "custom")
        self._backend_changed(form.backendCombo.currentText())
        self.generation.sync_backend()
        self.refresh_credentials()
        self.refresh_custom_loras()
        self.refresh_presets()
        self._workspace_changed(0)
        self._responsive_workspace()
        self.runner.submit(asyncio.to_thread(self._reconcile_images), self._images_reconciled)

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
        if index == 0:
            self.refresh_cache()
        for stack in (self.shell.leftStack, self.shell.centerStack, self.shell.rightStack):
            stack.setCurrentIndex(index)
        buttons = (
            self.form.connectionNavigationButton,
            self.form.generationNavigationButton,
            self.form.queueNavigationButton,
            self.form.imagesNavigationButton,
        )
        buttons[index].setChecked(True)
        self.gallery.set_active(self.form.workspaceStack.currentWidget() is self.images_page)
        self._responsive_workspace()

    def _sync_run_control(self) -> None:
        state = self.activity.run_state
        running = state.phase == "running"
        if running:
            self._run_settle.stop()
        elif self._run_was_running and state.phase in ("idle", "ready") and not self._run_settle.isActive():
            # Work just ended: show the finishing state briefly before the control settles back.
            self._run_was_running = False
            self.shell.runControl.set_presentation("complete", detail="Finishing.")
            self._run_settle.start()
            return
        elif self._run_settle.isActive():
            return  # keep the finishing state until its timer elapses
        self._run_was_running = running
        self.shell.runControl.set_presentation(
            state.phase,
            action=state.action,
            completed=state.completed,
            total=state.total,
            detail=state.detail,
        )

    def _align_run_control(self) -> None:
        """Centre the header control above the Images tab; both rows share the rail's left edge."""
        shell = self.shell
        images = shell.imagesNavigationButton.geometry()
        if not (shell.brandFrame.isVisible() and shell.navigationFrame.isVisible()) or images.width() <= 0:
            return
        control, offset = shell.runControl, shell.runControlOffset
        # The offset's left edge does not depend on its own width, so this target is stable and cannot oscillate.
        width = max(0, images.center().x() - control.width() // 2 - offset.x())
        if offset.width() != width:
            offset.setFixedWidth(width)

    def _responsive_workspace(self) -> None:
        shell = self.shell
        narrow = self.form.width() < 1100
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
        shell.runControl.setVisible(True)
        self._align_run_control()
        shell.rightRail.setVisible(not images and not narrow)

    def show(self) -> None:
        apply_native_dark_titlebar(self.form)
        self.form.show()
        QTimer.singleShot(0, self._align_run_control)
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

    @staticmethod
    def _human_bytes(value: int) -> str:
        amount = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if amount < 1024 or unit == "GB":
                return f"{amount:.0f} {unit}" if unit in ("B", "KB") else f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{value} B"

    def refresh_presets(self) -> None:
        try:
            count = len(load_presets(self.state_dir))
            self.form.presetLibraryStatusLabel.setText(
                f"{count} named preset" + ("" if count == 1 else "s") + " in the durable library."
                if count
                else "No imported presets."
            )
            self.form.exportPresetsButton.setEnabled(count > 0)
        except ValidationError as exc:
            self.form.presetLibraryStatusLabel.setText(f"Preset library is unreadable: {exc}")
            self.form.exportPresetsButton.setEnabled(False)

    def import_presets(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self.form, "Import Draw Things presets", str(self.state_dir), "Draw Things presets (*.json)"
        )
        if not chosen:
            return
        if not self.confirm(
            self.form,
            "Replace preset library?",
            "Importing replaces the entire durable preset library, including presets saved from Generate. Continue?",
        ):
            return
        try:
            count = import_presets(Path(chosen), self.state_dir)
        except (ValidationError, OSError) as exc:
            QMessageBox.warning(self.form, "Presets not imported", str(exc))
            return
        self.refresh_presets()
        self.generation.refresh_presets()
        self.form.statusBar().showMessage(f"Imported {count} Draw Things preset(s).", 5000)

    def export_presets(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self.form, "Export Draw Things presets", str(Path.home() / "custom_configs.json"), "JSON (*.json)"
        )
        if not chosen:
            return
        try:
            count = export_presets(self.state_dir, Path(chosen))
        except (ValidationError, OSError) as exc:
            QMessageBox.warning(self.form, "Presets not exported", str(exc))
            return
        self.form.statusBar().showMessage(f"Exported {count} Draw Things preset(s).", 5000)

    def refresh_cache(self) -> None:
        token = self._cache_measure_token = object()

        async def measure():
            # Startup cache reads may briefly own the lease. Do not leave a transient
            # busy warning on screen; retry off the GUI thread for at most one second.
            for attempt in range(11):
                try:
                    return await asyncio.to_thread(cache_usage, self.state_dir)
                except CacheBusyError:
                    if attempt == 10:
                        raise
                    await asyncio.sleep(0.1)

        self.runner.submit(
            measure(),
            lambda usage, failure: self._cache_measured(usage, failure) if token is self._cache_measure_token else None,
        )

    def _cache_measured(self, usage, failure) -> None:
        if failure is not None:
            log.warning("cache measurement failed reason=%s", type(failure).__name__)
            self.form.cacheStatusLabel.setText(
                str(failure)
                if isinstance(failure, (CacheBusyError, CacheCleanupError, CacheSafetyError))
                else "Disposable cache size unavailable; refresh to retry."
            )
            return
        labels = {item.name: f"{self._human_bytes(item.bytes)} ({item.files} files)" for item in usage}
        self.form.cacheStatusLabel.setText(
            f"Inventory {labels['inventory']} · Thumbnails {labels['thumbnails']} · Derived {labels['derived']}"
        )

    def clear_cache(self, categories: tuple[str, ...]) -> None:
        if self.activity.generating or self.activity.dispatching or self.image_export.running:
            QMessageBox.warning(
                self.form, "Cache is busy", "Stop the active generation, queue dispatch or image export before cleanup."
            )
            return
        names = ", ".join(categories)
        if not self.confirm(
            self.form,
            "Clear disposable cache?",
            f"Delete disposable {names} cache? Outputs, attempts, queues, presets, settings, credentials and model assets are preserved.",
        ):
            return

        async def purge():
            return await asyncio.to_thread(purge_cache, self.state_dir, categories)

        self.runner.submit(purge(), lambda _, failure: self._cache_cleared(names, failure))

    def _cache_cleared(self, names: str, failure) -> None:
        if failure is not None:
            log.warning("cache cleanup failed reason=%s", type(failure).__name__)
            QMessageBox.warning(
                self.form,
                "Cache not cleared",
                str(failure)
                if isinstance(failure, (CacheBusyError, CacheCleanupError, CacheSafetyError))
                else "Cleanup may be partial; refresh sizes and retry. Check cache-folder permissions.",
            )
            self.refresh_cache()
            return
        self.refresh_cache()
        self.form.statusBar().showMessage(f"Cleared disposable cache: {names}.", 5000)

    def _reuse_output(self, request: dict, identity: str) -> None:
        self.generation.reuse_saved_request(request, identity)
        self._show_generation()

    def _regenerate_output(self, request: dict, identity: str) -> None:
        if self.activity.generating or self.activity.dispatching:
            QMessageBox.warning(
                self.form, "Generation is busy", "Stop the active generation or queue dispatch before regenerating."
            )
            return
        if not self.confirm(
            self.form,
            "Regenerate saved output?",
            "This creates a fresh request and may consume provider generation credits. The original output is preserved.",
        ):
            return
        self._show_generation()
        if not self.generation.regenerate_saved_request(request, identity):
            self.form.statusBar().showMessage("Regeneration was not submitted; Generate was left unchanged.", 5000)

    def refresh_custom_loras(self) -> None:
        count = len(Catalogue(self.state_dir).user_loras())
        self.form.customLorasStatusLabel.setText(
            f"{count} custom LoRA definition" + ("" if count == 1 else "s") + " imported."
            if count
            else "No custom LoRA definitions imported."
        )
        self.form.clearCustomLorasButton.setEnabled(count > 0)

    def import_custom_loras(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self.form, "Import custom LoRAs", str(self.state_dir), "JSON (*.json)")
        if not chosen:
            return
        try:
            count = import_user_loras(Path(chosen), self.state_dir)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.form, "Custom LoRAs not imported", str(exc))
            return
        self.refresh_custom_loras()
        self.generation.reload_custom_loras()
        self.form.statusBar().showMessage(
            f"Imported {count} custom LoRA definition" + ("" if count == 1 else "s") + ".",
            5000,
        )

    def clear_custom_loras(self) -> None:
        catalogue = Catalogue(self.state_dir)
        if not catalogue.user_loras_path.exists():
            self.refresh_custom_loras()
            return
        if not self.confirm(
            self.form,
            "Clear custom LoRAs",
            "Remove the imported custom LoRA definitions? Bundled DTFlow catalogue data is not affected.",
        ):
            return
        try:
            catalogue.user_loras_path.unlink()
        except OSError as exc:
            QMessageBox.warning(self.form, "Custom LoRAs not cleared", type(exc).__name__)
            return
        self.refresh_custom_loras()
        self.generation.reload_custom_loras()

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
        self.generation.credentials_changed()

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
        self.generation.credentials_changed()

    def _recycle_busy(self) -> bool:
        """Keep UI-generated exports and local dispatch from racing with recycling."""
        return bool(
            self.image_export.running
            or self.generation._busy
            or self.queue.dispatching_here
            or self.activity.dispatching
        )

    def _request_gallery_recycle(self, identities: tuple[str, ...], hidden: int) -> None:
        """Only the confirmation service may move explicitly checked originals."""
        self.image_recycle.request(identities, hidden)

    def _confirm_close(self) -> bool:
        if self.image_recycle.running:
            self.image_recycle.cancel()
            QMessageBox.information(
                self.form, "Image recycling in progress",
                "Stop requested. Wait for the current image and result summary before closing.",
            )
            return False
        return self.queue.confirm_close()

    def _reconcile_images(self):
        """Inspect interrupted file operations under the original-file lease."""
        with original_images_lock(self.state_dir):
            return reconcile_images(self.state_dir)

    def _images_reconciled(self, outcome, error) -> None:
        if error is not None:
            log.warning("image-recycle reconciliation skipped reason=%s", type(error).__name__)
            return
        if outcome and any(outcome):
            self.gallery.refresh(preserve=True)

    def _open_checked_export(self) -> None:
        """Activate the one authoritative Connection export form for checked gallery IDs."""
        self.form.workspaceStack.setCurrentIndex(0)
        self.image_export.select_scope("checked")
        self.form.imageExportScopeCombo.setFocus(Qt.FocusReason.OtherFocusReason)

    def _export_selection(self, scope: str) -> GalleryQuery | tuple[str, ...]:
        """Freeze presentation choices; export service rechecks committed evidence."""
        if scope == "all":
            return GalleryQuery()
        if scope == "filter":
            return self.gallery.export_query()
        if scope == "checked":
            identities = self.gallery.export_checked()
            if not identities:
                raise ValidationError("Check at least one saved image in Images first.")
            return identities
        identity = self.gallery.export_identity() if scope == "selected" else self.generation.export_identity()
        if identity is None:
            raise ValidationError("Select a saved image in Images or a PNG output in Generate first.")
        return (identity,)

    def about(self) -> None:
        QMessageBox.about(self.form, "About DTFlow", f"{display_identity()}\n\nState folder: {self.state_dir}")

    def close(self) -> None:
        self.remember(background=False)
        self.image_export.cancel()
        self.image_recycle.cancel()
        self.gallery.save_now()
        log.info("window closed")
        self.runner.shutdown()
        self.form.close()

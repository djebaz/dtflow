"""Main window: presentation and wiring only. Backend access and credentials live in the shared services."""

import asyncio
import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QSignalBlocker, QSize, Qt, QTimer, Slot
from PySide6.QtGui import QFontMetrics, QIcon, QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from dtflow.ui import file_dialogs
from dtflow.ui.file_dialogs import QFileDialog
from dtflow import startup
from dtflow.build import display_identity
from dtflow.composition import BACKENDS, BackendSelection
from dtflow.core.gallery import GalleryQuery
from dtflow.core.models import BackendError, ValidationError, eligible_model_ids
from dtflow.diagnostics import event as diagnostic_event
from dtflow.logging_setup import configured_directory, development_mode
from dtflow.paths import application_state_dir
from dtflow.persistence.cache_coordination import CacheBusyError, CacheCleanupError, CacheSafetyError
from dtflow.persistence.image_recycle import reconcile as reconcile_images
from dtflow.persistence.last_session import load_last_session, save_last_session
from dtflow.persistence.original_io import (
    OriginalImagesBusyError,
    OriginalImagesUnavailableError,
    original_images_lock,
    recycle_batch_lock,
    require_originals_ready,
)
from dtflow.services.cache_management import CATEGORIES, cache_usage, purge_cache
from dtflow.services.catalogue import Catalogue, import_user_loras
from dtflow.services.lora_store import LoraStore
from dtflow.services.connection import Presentation, check_connection, present_failure, present_status
from dtflow.services.cloud_usage import key_identity
from dtflow.services.credentials import (
    ENVIRONMENT,
    INDEX_FILE,
    EnvironmentCredentials,
    OptionalSavedCredentials,
    SavedCredentials,
    SecureStorageUnavailable,
    credential_status,
    resolve_credentials,
)
from dtflow.services.presets import export_presets, import_presets, load_presets
from dtflow.ui.activity import Activity
from dtflow.ui.activity_strip import ActivityStrip, activity_strip
from dtflow.ui.brand import connection_icon, status_dot, tinted_icon
from dtflow.ui.button_focus import KeyboardFocusGuard
from dtflow.ui.current_page_stack import CurrentPageStack
from dtflow.ui.cloud_usage_panel import CloudUsagePanel
from dtflow.ui.workflow_defaults_panel import WorkflowDefaultsPanel
from dtflow.ui.gallery_page import GalleryPage
from dtflow.ui.generation_page import GenerationPage
from dtflow.ui.image_export_panel import ImageExportPanel
from dtflow.ui.image_preview import ImagePreview
from dtflow.ui.image_recycle_panel import ImageRecyclePanel
from dtflow.ui.inline_feedback import InlineFeedback, InlineNotice, confirm_action
from dtflow.ui.queue_page import QueuePage
from dtflow.ui.queue_popover import QueuePopover
from dtflow.ui.performance import GuiHeartbeat, profile_slow_gui_callback
from dtflow.ui.queue_composer import ValueChips
from dtflow.ui.queue_widgets import SegmentedBar
from dtflow.ui.run_control import RunControl
from dtflow.persistence.script_library import ScriptLibrary
from dtflow.ui.scripts_page import ScriptsPage
from dtflow.ui.scripts_library_card import ScriptRemoveButton
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.search_select import SearchableComboBox
from dtflow.ui.system_trash import TRASH_NAME, trash_supported
from dtflow.ui.theme import apply_native_dark_titlebar
from dtflow.ui.wheel_guard import WheelGuard

log = logging.getLogger(__name__)

RUN_SETTLE_MS = 2000  # how long the header control shows its finishing state after work ends

FORMS_DIR = Path(__file__).with_name("forms")
ASSETS_CONTROLS = Path(__file__).parents[1] / "resources" / "images" / "controls"
UI_FILE = FORMS_DIR / "main_window.ui"
GENERATION_UI_FILE = FORMS_DIR / "generation_page.ui"
SCRIPTS_UI_FILE = FORMS_DIR / "scripts_workspace.ui"
QUEUE_UI_FILE = FORMS_DIR / "queue_page.ui"
PREVIEW_CONTROL_TYPES = (
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QAbstractItemView,
)


def _form_loader() -> QUiLoader:
    """Share custom-widget registration for the lifetime of this application."""
    application = QApplication.instance()
    loader = getattr(application, "_dtflow_form_loader", None)
    if loader is None:
        loader = QUiLoader(application)
        for widget_type in (
            ImagePreview,
            SearchableComboBox,
            RunControl,
            SegmentedBar,
            ValueChips,
            ScriptRemoveButton,
            CurrentPageStack,
        ):
            loader.registerCustomWidget(widget_type)
        application._dtflow_form_loader = loader
    return loader


def load_ui(path: Path, parent: QWidget | None = None) -> QWidget:
    """Load a canonical Designer form without committing generated Python."""
    file = QFile(str(path))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"{path.name} could not be opened.")
    try:
        widget = _form_loader().load(file, parent)
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


class _ShutdownGuard(QObject):
    """Stop desktop timers only while their owning Qt window still exists."""

    @Slot()
    def stop_timers(self) -> None:
        window = self.parent()
        if window is not None:
            for timer in window.findChildren(QTimer):
                timer.stop()


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
        # Separate first-use Qt costs (loader plugins, fonts, stylesheet parsing) from the form's own cost.
        _form_loader()
        startup.mark("ui loader created")
        QWidget().ensurePolished()
        startup.mark("first widget polished")
        # Disposable font/input probes belong in tools/ui/probe_line_edit.py, not desktop startup.
        self.form = load_form()
        self.form.statusGlyphLabel.setPixmap(status_dot())
        startup.mark("main form")
        self.generation_page = load_ui(GENERATION_UI_FILE)
        startup.mark("generation form")
        self.queue_page = load_ui(QUEUE_UI_FILE)
        startup.mark("queue form")
        self._disable_preview_controls(self.generation_page)
        self._disable_preview_controls(self.queue_page)
        self.form.workspaceStack.addWidget(self.generation_page)
        self.form.workspaceStack.addWidget(self.queue_page)

        self._saved = saved or SavedCredentials()
        self._optional_saved = OptionalSavedCredentials(self._saved)
        self._summary_reads = {}
        self._startup_check_started = False
        self._check_token = None
        self._check_future = None
        self._environ = environ
        self.runner = runner or AsyncRunner(self.form)
        self._shutdown_guard = _ShutdownGuard(self.form)
        self._wheel_guard = WheelGuard(self.form)
        self.runner.shutdown_started.connect(self._shutdown_guard.stop_timers)
        self.state_dir = Path(state_dir) if state_dir is not None else application_state_dir()
        self._saved.attach(self.state_dir / INDEX_FILE)
        self._busy = False
        self.activity = Activity()
        form = self.form
        self.shell = load_ui(FORMS_DIR / "workspace_shell.ui", form.centralWidget())
        self._images_header = self.shell.imagesUtilityHeader
        # Restore the standard left-stack width policy outside Scripts.
        # The scroll viewport has horizontal overflow hidden.
        self._left_stack_default_horizontal_policy = self.shell.leftStack.sizePolicy().horizontalPolicy()
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
        startup.mark("shell form and mounting")
        self.gallery_detail = load_ui(FORMS_DIR / "gallery_detail_page.ui", self.shell.centerStack)
        startup.mark("images form")
        self.gallery = GalleryPage(
            self.gallery_detail,
            runner=self.runner,
            state_dir=self.state_dir,
            reuse_callback=self._reuse_output,
            regenerate_callback=self._regenerate_output,
            edit_callback=self._edit_output,
            export_callback=self._open_checked_export,
            recycle_callback=self._request_gallery_recycle if trash_supported() else None,
        )
        startup.mark("images page controller")
        self.gallery.delete_button.setToolTip(f"Move eligible checked originals to {TRASH_NAME} after confirmation.")
        self.gallery_preview = self.gallery  # retain the established integration attribute
        form.workspaceStack.addWidget(self.images_page)
        self.shell.leftStack.addWidget(load_ui(FORMS_DIR / "gallery_context.ui", self.shell.leftStack))
        self.shell.centerStack.addWidget(self.gallery_detail)
        self.shell.rightStack.addWidget(QWidget())
        startup.mark("workspace: images mounted")
        self.logs_marker = QWidget()
        form.workspaceStack.addWidget(self.logs_marker)
        startup.mark("workspace: Logs navigation marker mounted")
        self._logs_form = None
        self._logs = None
        self._logs_placeholder = QWidget()
        self.shell.leftStack.addWidget(QWidget())
        self.shell.centerStack.addWidget(self._logs_placeholder)
        self.shell.rightStack.addWidget(QWidget())
        self._logs_previous_page = form.connectionPage
        self._logs_active = False
        startup.mark("workspace: Logs placeholders mounted")
        # Scripts reuses the existing shell rails. Add its panels only after all
        # other page stacks have been mounted, preserving their index contracts.
        self.scripts_designer = load_ui(SCRIPTS_UI_FILE)
        self.scripts = ScriptsPage(
            self.scripts_designer, owner=self, library_factory=lambda: ScriptLibrary(self.state_dir)
        )
        self.scripts.mount(self.shell)
        self._scripts_active = False
        # Workflows is its own workspace; its panels follow Scripts' in the shell stacks (selected by widget).
        from dtflow.ui.workflows_page import WorkflowsPage

        self.workflows_marker = QWidget()
        form.workspaceStack.addWidget(self.workflows_marker)
        self.workflows = WorkflowsPage(
            load_ui(FORMS_DIR / "workflows_context.ui"),
            load_ui(FORMS_DIR / "workflows_workspace.ui"),
            load_ui(FORMS_DIR / "workflows_inspector.ui"),
            runner=self.runner,
            state_dir=self.state_dir,
            choices_source=self._workflow_choices,
            open_image=self._open_output_in_images,
        )
        self.workflows.mount(self.shell)
        startup.mark("workspace: workflows mounted")
        form.workspaceStack.hide()
        startup.mark("workspace: legacy stack hidden")
        form.rootLayout.addWidget(self.shell)
        startup.mark("workspace: shell inserted into root layout")
        self._last_run_marker = None
        startup.mark("workspace: activity strip constructor entered")
        self.progress_strip = ActivityStrip(form)
        startup.mark("workspace: activity strip created")
        self.inline = InlineFeedback(form)
        startup.mark("workspace: inline feedback created")
        # Preserve the connection controller's established chrome references.
        for name in (
            "brandFrame",
            "navigationFrame",
            "productMarkLabel",
            "productSubtitleLabel",
            "connectionNavigationButton",
            "generationNavigationButton",
            "workflowsNavigationButton",
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
        # The fixed left rail belongs to its scroll viewport, not to the widest
        # (possibly hidden) page after inventory/queue content changes.
        self.shell.leftStack.currentChanged.connect(self._fit_context_pages)
        self._fit_context_pages()
        # A stack is as wide as its widest page; size the right rail from the shown page only,
        # so one tab's inspector cannot push every other tab's inspector past the rail.
        self.shell.rightStack.currentChanged.connect(self._fit_inspector_page)
        self._fit_inspector_page()
        startup.mark("workspace: rails and navigation configured")
        from dtflow.ui.brand import app_icon, brand_mark

        form.setWindowIcon(app_icon())
        logo = brand_mark()
        if not logo.isNull():
            form.productMarkLabel.setPixmap(
                logo.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        startup.mark("workspace: brand icon prepared")
        self._gallery_chrome_active = False
        self._gallery_previous_sizes = None
        startup.mark("workspace chrome and navigation ready")
        self.generation = GenerationPage(
            self.generation_page,
            runner=self.runner,
            backend_source=lambda: (form.backendCombo.currentText(), form.endpointEdit.text() or None),
            saved_source=lambda: self._optional_saved,
            state_dir=self.state_dir,
            activity=self.activity,
            no_cache=no_cache,
            cache_changed=self.refresh_cache,
        )
        startup.mark("generate controller")
        self.image_export = ImageExportPanel(
            form,
            runner=self.runner,
            state_dir=self.state_dir,
            selection=self._export_selection,
            no_cache=no_cache,
        )
        self.image_export.cache_changed.connect(self.refresh_cache)
        self._gallery_export_open = False
        self.gallery_detail.galleryExportScroll.hide()
        self.gallery_detail.galleryExportCloseButton.clicked.connect(self._close_gallery_export)
        self.image_recycle = ImageRecyclePanel(
            form,
            runner=self.runner,
            state_dir=self.state_dir,
            gallery=self.gallery,
            busy_source=self._recycle_busy,
        )
        self.gallery.refresh_button.clicked.connect(self._request_recycle_reconciliation)
        self.queue = QueuePage(
            self.queue_page,
            runner=self.runner,
            backend_source=lambda: (form.backendCombo.currentText(), form.endpointEdit.text() or None),
            saved_source=lambda: self._optional_saved,
            state_dir=self.state_dir,
            request_source=self.generation.request_for_queue,
            edit_source=self.generation.queue_edit_job,
            editing_source=lambda: self.generation.inpaint.active,
            simulation_source=self.generation.simulation,
            created_hook=self.generation.queue_created,
            choices_source=self.generation.plan_choices,
            generate_hook=self._show_generation,
            reuse_hook=self._reuse_queue_request,
            activity=self.activity,
        )
        self.scripts.run_start = self.queue.start_script
        self.workflows.start_workflow = self.queue.start_workflow
        self.workflows.start_created = self.queue.start_created
        self.workflows.on_layout = self._responsive_workspace
        # Queues → Open in Workflows (and its Retry routing) for a workflow item, once QueuePage offers the hook.
        for owner in (self.queue, self.queue_page):
            hook = getattr(owner, "set_open_workflow_hook", None)
            if callable(hook):
                hook(self._open_workflow_run)
                break
        self.scripts.setup_prepare = self.queue.prepare_script
        self.scripts.run_prepared = self.queue.start_prepared_script
        self.scripts.inventory_source = lambda: self.generation._inventory_snapshot
        self.scripts.settings_source = self._script_settings
        self.scripts.generate_form = self.generation_page
        self.scripts.go_generate_hook = lambda: self._leave_scripts(inpaint=False)
        startup.mark("queue and image operation controllers")
        self.generation_page.inpaintModeButton.toggled.connect(lambda _on: self.queue.sync_edit_fields())
        self.generation_page.scriptsModeButton.clicked.connect(self._show_scripts)
        self.generation.inpaint.script_handoff = self._scripts_from_inpaint
        self.generation.inpaint._controls()
        self.scripts.text_mode.clicked.connect(lambda: self._leave_scripts(inpaint=False))
        self.scripts.inpaint_mode.clicked.connect(lambda: self._leave_scripts(inpaint=True))
        # One-click Queue (#135): the same popover from the header and from Generate's prompt header.
        arrow = self.generation_page.queueGenerateButton
        self.queue_popover = QueuePopover(
            self.queue,
            self.activity,
            form,
            changed=lambda label: arrow.setToolTip(
                f"{label}: Repeat N or the New queue plan, now or behind running work."
            ),
        )
        self.shell.quickQueueButton.setIcon(QIcon(str(ASSETS_CONTROLS.parent / "gallery" / "layers.svg")))
        self.shell.quickQueueButton.setIconSize(QSize(18, 18))
        self.shell.quickQueueButton.clicked.connect(lambda: self.queue_popover.open_at(self.shell.quickQueueButton))
        self.queue.plan_hook = self.queue_popover.sync_label
        self.queue.set_images_hook(self._open_output_in_images)
        self.scripts.open_images = self._open_output_in_images
        for button in (self.queue_popover.start_button, self.queue_popover.add_button):
            button.clicked.connect(self.remember)  # a run start, like Generate image
        self.generation_page.queueGenerateButton.clicked.connect(
            lambda: self.queue_popover.open_at(self.generation_page.queueGenerateButton)
        )
        self.generation.queue_behind = self.queue.queue_behind
        self.generation.queue_images = self._queue_images
        form.backendCombo.addItems(BACKENDS)
        startup.mark("page wiring")
        self._restore_session()
        startup.mark("pages wired, session restored")
        form.statusBar().showMessage(display_identity())
        # The former Help menu is a small status-bar icon so no menu bar takes vertical room.
        help_icon = ASSETS_CONTROLS / "help.svg"
        form.aboutAction.setIcon(QIcon(str(help_icon)))
        form.aboutAction.setToolTip("About DTFlow")
        about_button = QToolButton(form.statusBar())
        about_button.setObjectName("aboutButton")
        about_button.setDefaultAction(form.aboutAction)
        about_button.setIconSize(QSize(16, 16))
        about_button.setAutoRaise(True)
        about_button.setAccessibleName("About DTFlow")
        status_actions = QWidget(form.statusBar())
        status_actions_layout = QHBoxLayout(status_actions)
        status_actions_layout.setContentsMargins(0, 0, 8, 0)
        status_actions_layout.setSpacing(6)
        about_button.setFixedSize(20, 20)
        logs_button = QToolButton(status_actions)
        logs_button.setObjectName("logsStatusButton")
        logs_button.setIcon(QIcon(str(ASSETS_CONTROLS.parent / "gallery" / "list.svg")))
        logs_button.setIconSize(QSize(16, 16))
        logs_button.setAutoRaise(True)
        logs_button.setFixedSize(20, 20)
        logs_button.setCheckable(True)
        logs_button.setToolTip("Open Logs workspace (F10)")
        logs_button.setAccessibleName("Open Logs workspace")
        logs_button.clicked.connect(self._toggle_logs)
        status_actions_layout.addWidget(about_button)
        status_actions_layout.addWidget(logs_button)
        grid_status_group = QWidget(form.statusBar())
        grid_status_group.setObjectName("dtflowGridPositionGroup")
        grid_status_group.setAccessibleName("Grid position")
        grid_status_layout = QHBoxLayout(grid_status_group)
        grid_status_layout.setContentsMargins(0, 0, 0, 0)
        grid_status_layout.setSpacing(0)
        grid_index = QLineEdit("0/0", grid_status_group)
        grid_index.setObjectName("dtflowStatusGridFirstVisibleIndex")
        grid_index.setReadOnly(True)
        grid_index.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        grid_index.setFixedWidth(70)
        grid_index.setFrame(False)
        grid_index.setStyleSheet("QLineEdit { background: transparent; border: none; color: #8a8a8a; padding: 0; }")
        grid_index.setAccessibleName("0/0")
        grid_index.setToolTip("First visible image position in the Grid workspace")
        grid_status_layout.addWidget(grid_index)
        pill = QLabel("\u2013/0", form.statusBar())
        pill.setObjectName("usagePill")
        pill.setProperty("state", "neutral")
        pill.setAccessibleName("Local DT+ completed images today, active key / all keys")
        self.usage_pill = pill
        # Fixed width (room for 5+5 digits) so the placeholder-to-number switch never moves the buttons beside it.
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bold = pill.font()
        bold.setPointSizeF(8)
        bold.setBold(True)
        pill.setFixedWidth(QFontMetrics(bold).horizontalAdvance("99999/99999") + 16)
        form.statusBar().addPermanentWidget(grid_status_group)
        form.statusBar().addPermanentWidget(status_actions)
        # Connection keeps its existing colored status glyph and is the last
        # permanent control, independent of the active workspace or inspector.
        form.statusBar().addPermanentWidget(pill)
        form.statusBar().addPermanentWidget(self.shell.connectionStatusDock)
        self.grid_index_group = grid_status_group
        # The slot stays on every tab (only its text hides) so the activity bar has the same room everywhere.
        grid_status_group.setFixedSize(70, 20)
        # Technical benchmark/diagnostic field: only in the explicit -x/--dev mode, never in ordinary use.
        grid_status_group.setVisible(development_mode())
        grid_index.setVisible(form.workspaceStack.currentWidget() is self.images_page and development_mode())
        self.grid_index_label = grid_index
        self.gallery.grid_position_changed.connect(self._sync_grid_index_status)
        self.logs_button = logs_button
        form.statusBar().setSizeGripEnabled(False)
        form.statusBar().setFixedHeight(24)  # same height on every tab; the activity bar sits at its centre
        form.checkButton.clicked.connect(self.check)
        form.saveKeyButton.clicked.connect(self.save_key)
        form.removeKeyButton.clicked.connect(self.remove_key)
        form.keySelectCombo.currentIndexChanged.connect(self.key_selected)
        form.importCustomLorasButton.clicked.connect(self.import_custom_loras)
        form.clearCustomLorasButton.clicked.connect(self.clear_custom_loras)
        form.removeRetainedLoraButton.clicked.connect(self.remove_retained_lora)
        form.retainedLorasList.itemSelectionChanged.connect(self._retained_lora_selection_changed)
        self._remove_lora_shortcut = QShortcut(QKeySequence("Delete"), form.retainedLorasList)
        self._remove_lora_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._remove_lora_shortcut.activated.connect(self.remove_retained_lora)
        self._removing_retained_lora = False
        form.forgetFolderButton.clicked.connect(self.forget_selected_folder)
        form.forgetAllFoldersButton.clicked.connect(self.forget_all_folders)
        form.rememberedFoldersList.itemSelectionChanged.connect(self.refresh_remembered_folders_buttons)
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
        form.backendCombo.currentTextChanged.connect(lambda _: self.workflows.backend_changed())
        form.queueNavigationButton.clicked.connect(self._show_queue)
        form.workflowsNavigationButton.clicked.connect(self._show_workflows)
        form.imagesNavigationButton.setEnabled(True)
        form.imagesNavigationButton.setIcon(
            tinted_icon(ASSETS_CONTROLS.parent / "gallery" / "image.svg", "color.selectionText", 14)
        )
        form.imagesNavigationButton.setIconSize(QSize(14, 14))
        form.imagesNavigationButton.installEventFilter(KeyboardFocusGuard(form.imagesNavigationButton))
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
        self.activity.listen(self._sync_activity_progress)
        self.progress_strip.form.destroyed.connect(self._retire_activity_presentation)
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
        for widget in (self.shell.queueNavigationButton, self.shell.runControl):
            widget.installEventFilter(
                _LayoutGuard(self._align_run_control, form, (QEvent.Type.Resize, QEvent.Type.Move))
            )
        self.form.installEventFilter(_CloseGuard(self._confirm_close, self.form))
        self._fullscreen_shortcut = QShortcut(QKeySequence("F11"), self.form)
        self._fullscreen_shortcut.activated.connect(self._toggle_window_fullscreen)
        self._images_live_shortcut = QShortcut(QKeySequence("F5"), self.form)
        self._images_live_shortcut.setAutoRepeat(False)
        self._images_live_shortcut.activated.connect(self._open_live_images)
        self._workspace_shortcuts = []
        for key, callback in (
            ("F1", self._show_generation),
            ("F2", self._show_workflows),
            ("F3", self._show_queue),
            ("F4", lambda: self.form.workspaceStack.setCurrentWidget(self.images_page)),
            ("F10", self._toggle_logs),
            ("F12", lambda: self.form.workspaceStack.setCurrentWidget(self.form.connectionPage)),
        ):
            shortcut = QShortcut(QKeySequence(key), self.form)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
            self._workspace_shortcuts.append(shortcut)
        self._cycle_workspace_shortcut = QShortcut(QKeySequence("Ctrl+Tab"), self.form)
        self._cycle_workspace_shortcut.setAutoRepeat(False)
        self._cycle_workspace_shortcut.activated.connect(self._cycle_workspace)
        self._submit_shortcuts = []
        for key in ("Ctrl+Return", "Ctrl+Enter"):  # Return and keypad Enter are distinct Qt keys.
            shortcut = QShortcut(QKeySequence(key), self.form)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(self._submit_active_page)
            self._submit_shortcuts.append(shortcut)
        diagnostic_event(
            log,
            "app.window_ready",
            level=logging.INFO,
            data={
                "state": "default" if state_dir is None else "custom",
            },
        )
        self._backend_changed(form.backendCombo.currentText())
        self.generation.sync_backend()
        startup.mark("backend and restored catalogue applied")
        self.refresh_credentials(background=True)
        self.cloud_usage = CloudUsagePanel(
            form,
            self.runner,
            self.state_dir,
            active_credential=self._active_credential_id,
            key_directory=self._key_directory,
            on_active=self._show_active_key,
        )
        self.workflow_defaults = WorkflowDefaultsPanel(form, self.runner, self.state_dir)
        self.cloud_usage.refresh()  # the bottom bar names the active key on every view from startup
        self.activity.listen(self._usage_activity_changed)
        self._gui_heartbeat = GuiHeartbeat(
            form,
            self.activity,
            self.progress_strip,
            self.runner,
            self._gui_heartbeat_context,
            self.gallery._perf_stats.flush,
        )
        self.refresh_custom_loras(background=True)
        file_dialogs.configure(
            self.state_dir, on_problem=self.dialog_folder_problem, on_change=self.refresh_remembered_folders
        )
        self.refresh_remembered_folders()
        self.refresh_presets(background=True)
        self._workspace_changed(0)
        self._responsive_workspace()
        # Readiness precedes reconciliation so an unsupported bridge gets one
        # startup notice, not duplicate warnings from concurrent startup tasks.
        self.runner.submit(asyncio.to_thread(require_originals_ready, self.state_dir), self._originals_ready_checked)

    @property
    def logs_form(self):
        self._ensure_logs()
        return self._logs_form

    @property
    def logs(self):
        self._ensure_logs()
        return self._logs

    def _ensure_logs(self) -> None:
        """Construct Qt objects on the GUI thread, only when Logs is first requested."""
        if self._logs is not None:
            return
        from dtflow.ui.logs_workspace import LogsWorkspace  # deferred with the form: not needed at startup

        form = load_ui(FORMS_DIR / "logs_workspace.ui")
        self._logs_form = form
        index = self.shell.centerStack.indexOf(self._logs_placeholder)
        self.shell.centerStack.removeWidget(self._logs_placeholder)
        self._logs_placeholder.deleteLater()
        self.shell.centerStack.insertWidget(index, form)
        self._logs = LogsWorkspace(
            form,
            configured_directory(),
            self._close_logs,
            runner=self.runner,
            escape_owner=self._inline_consumes_escape,
        )

    def _originals_ready_checked(self, _result, error) -> None:
        if error is not None:
            diagnostic_event(
                log,
                "app.original_image_readiness_unavailable",
                level=logging.WARNING,
                data={
                    "reason": type(error).__name__,
                },
            )
            InlineNotice.warning(
                self.form,
                "Original images unavailable",
                f"{error} Interrupted removal intents remain blocked until shared-lock readiness returns."
                if isinstance(error, OriginalImagesUnavailableError)
                else "Original-file readiness could not be checked. Interrupted removals remain blocked.",
            )
            return
        self._request_recycle_reconciliation()

    @staticmethod
    def _disable_preview_controls(page: QWidget) -> None:
        for control_type in PREVIEW_CONTROL_TYPES:
            for control in page.findChildren(control_type):
                # A spin box's or combo box's own editor and popup list follow their owner; disabling them separately
                # would leave the value greyed out and untypeable, or the popup's items unclickable, after the owner is
                # re-enabled. The popup list sits inside a container frame, so look through every ancestor.
                owner = control.parentWidget()
                while owner is not None and not isinstance(owner, (QAbstractSpinBox, QComboBox)):
                    owner = owner.parentWidget()
                if owner is None:
                    control.setEnabled(False)

    def _show_queue(self) -> None:
        self.form.workspaceStack.setCurrentWidget(self.queue_page)
        self.queue.refresh()
        self.queue.shown()

    def _show_workflows(self) -> None:
        self.form.workspaceStack.setCurrentWidget(self.workflows_marker)

    def _open_workflow_run(self, queue_id: str) -> None:
        """Show a workflow queue's run in Workflows: its steps, Retry and waiting branches."""
        self._show_workflows()
        self.workflows.open_run(queue_id)

    def _workflow_choices(self) -> dict:
        """The Workflows inspector's checkpoint and LoRA choices, from the in-memory inventory (never blocks)."""
        choices = self.generation.plan_choices()
        snapshot = self.generation._inventory_snapshot
        models = list(eligible_model_ids(snapshot)) if snapshot is not None else list(choices.get("models", ()))
        return {"models": models, "loras": choices.get("loras", {}), "backend": self.form.backendCombo.currentText()}

    def _cycle_workspace(self) -> None:
        """Cycle the ordinary workspaces without changing gallery follow or admitting work."""
        if self.runner.closed:
            return
        current = self.form.workspaceStack.currentWidget()
        if current is self.generation_page:
            self._show_workflows()
        elif current is self.workflows_marker:
            self._show_queue()
        elif current is self.queue_page:
            self.form.workspaceStack.setCurrentWidget(self.images_page)
        else:  # Images wraps to Generate; Connection and Logs enter the cycle there too.
            self._show_generation()

    def _open_live_images(self) -> None:
        """F5 opens Images with Live and Follow enabled, without submitting work."""
        if self.runner.closed:
            return
        self.form.workspaceStack.setCurrentWidget(self.images_page)
        self.gallery.follow_newest()

    def _open_output_in_images(self, identity: str) -> None:
        """Queues → Open in Images: show this committed output in the Images Detail view."""
        self.form.workspaceStack.setCurrentWidget(self.images_page)
        self.gallery.open_output(identity)

    def _show_generation(self) -> None:
        self.generation.sync_backend()
        self.form.workspaceStack.setCurrentWidget(self.generation_page)
        if self._scripts_active:
            self.scripts.activate()

    def _set_scripts_left_rail(self, active: bool) -> None:
        """Size the shared left rail without restarting workspace-navigation side effects."""
        if not active:
            self.shell.leftStack.setMinimumHeight(0)
            self.shell.leftStack.setMaximumHeight(16777215)
        policy = self.shell.leftStack.sizePolicy()
        vertical = QSizePolicy.Policy.Ignored if active else QSizePolicy.Policy.Preferred
        horizontal = QSizePolicy.Policy.Ignored if active else self._left_stack_default_horizontal_policy
        if policy.verticalPolicy() != vertical or policy.horizontalPolicy() != horizontal:
            policy.setVerticalPolicy(vertical)
            policy.setHorizontalPolicy(horizontal)
            self.shell.leftStack.setSizePolicy(policy)
        self.shell.leftScroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if active else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

    def _show_scripts(self) -> None:
        """Switch Generate to its third sub-mode without touching active work."""
        self._scripts_active = True
        self.form.workspaceStack.setCurrentWidget(self.generation_page)
        self.generation_page.scriptsModeButton.setChecked(True)
        self.scripts.activate()
        self._set_scripts_left_rail(True)
        self._responsive_workspace()

    def _script_settings(self) -> tuple[str, str | None, dict[str, str]]:
        """(summary, problem, details): what Scripts Start would run with, from the very request Start validates."""
        from dtflow.ui.generation_presenter import FormError

        try:
            request, name, endpoint, _ = self.generation.request_for_queue(preview=True)
        except (ValidationError, FormError) as exc:
            text = str(exc).rstrip(".")
            if "model" in text.lower():
                text = "pick a model on the Generate tab (" + text + ")"
            return "", text, {}
        where = f"{name} @ {endpoint}" if endpoint else name
        details = {
            "Checkpoint": request.model_id,
            "Size": f"{request.width_px} × {request.height_px}",
            "Steps": str(request.steps),
            "Sampler": str(request.sampler_id),
            "Seed": str(request.seed),
        }
        return (
            f"Runs with Generate's settings: {where} · {request.model_id} · {request.width_px}×{request.height_px} · "
            f"{request.steps} steps. The script may override size, steps, seed, model and LoRAs.",
            None,
            details,
        )

    def _scripts_from_inpaint(self, source, mask, thumbnail) -> None:
        """Offline, detached source/mask handoff only; no runtime script entry point."""
        if self.scripts.accept_inpaint_snapshot(source, mask, thumbnail):
            self._show_scripts()

    def _leave_scripts(self, *, inpaint: bool) -> None:
        """Return to the canonical Generate form without resetting another workspace."""
        self._scripts_active = False
        self.scripts.drawer.hide()
        target = self.generation_page.inpaintModeButton if inpaint else self.generation_page.textToImageModeButton
        target.setChecked(True)
        for stack in (self.shell.leftStack, self.shell.centerStack, self.shell.rightStack):
            stack.setCurrentIndex(1)
        self._set_scripts_left_rail(False)
        self._responsive_workspace()

    def _toggle_logs(self) -> None:
        if self.form.workspaceStack.currentWidget() is self.logs_marker:
            self._close_logs()
        else:
            self._logs_previous_page = self.form.workspaceStack.currentWidget()
            self.form.workspaceStack.setCurrentWidget(self.logs_marker)

    def _submit_active_page(self) -> None:
        """Ctrl+Enter clicks the active workspace's generation action, without bypassing its normal guards."""
        if self.runner.closed or self.inline.owns_keyboard() or QApplication.activePopupWidget() is not None:
            return
        current = self.form.workspaceStack.currentWidget()
        button = None
        if current is self.generation_page:
            if self._scripts_active:
                candidate = self.scripts.start
                if candidate.text() == "Run Script":
                    button = candidate  # Never submit Setup, Continue or Retry Setup by shortcut.
            else:
                button = self.generation_page.generateButton  # Generate image or Edit image.
        elif current is self.queue_page:
            button = self.queue._shortcut_button()  # Existing Start now / Add to queue choice; never Save paused.
        elif current is self.workflows_marker:
            button = self.workflows.workspace.runWorkflowButton
        if button is not None and button.isVisible() and button.isEnabled():
            button.click()

    def _toggle_window_fullscreen(self) -> None:
        if self.form.isFullScreen():
            self.form.setWindowState(
                getattr(self, "_windowed_state", self.form.windowState() & ~Qt.WindowState.WindowFullScreen)
            )
        else:
            self._windowed_state = self.form.windowState()
            self.form.showFullScreen()

    def _inline_consumes_escape(self) -> bool:
        """An active shared confirmation, editor or notice takes Escape before the Logs workspace."""
        if self.inline.active():
            self.inline.dismiss()
            return True
        return False

    def _close_logs(self) -> None:
        if self.form.workspaceStack.currentWidget() is self.logs_marker:
            self.form.workspaceStack.setCurrentWidget(self._logs_previous_page)

    def _sync_grid_index_status(self, first: int, total: int) -> None:
        """Publish the real Grid viewport position in the global status bar."""
        self.grid_index_label.setText(f"{first}/{total}")
        self.grid_index_label.setAccessibleName(f"{first}/{total}")

    def _workspace_changed(self, index: int) -> None:
        """Switch mounted workspaces without resetting active operations."""
        logs_active = self.form.workspaceStack.currentWidget() is self.logs_marker
        if logs_active:
            self.logs_button.setChecked(True)
            self.logs.open()
        else:
            self.logs_button.setChecked(False)
            if self._logs is not None:
                self._logs.stop()
        if not logs_active:
            self.gallery.recycle_scope_revision += 1
            self.inline.dismiss()
        if index == 0:
            self.refresh_cache()
            self.cloud_usage.refresh()
            self.refresh_custom_loras(background=True)
        for stack in (self.shell.leftStack, self.shell.centerStack, self.shell.rightStack):
            stack.setCurrentIndex(index)
        if index == 1 and self._scripts_active:
            self.scripts.activate()
        workflows_active = self.form.workspaceStack.currentWidget() is self.workflows_marker
        if workflows_active:
            self.workflows.activate()
        else:
            self.workflows.set_active(False)
        # Queues owns scrolling in queueList: the shared outer scroll must not
        # create a second wheel/scrollbar over the fixed filter and Clear action.
        queues_active = index == 2
        scripts_active = index == 1 and self._scripts_active
        if not scripts_active:
            self.shell.leftStack.setMinimumHeight(0)
            self.shell.leftStack.setMaximumHeight(16777215)
        scroll = self.shell.leftScroll
        stack_policy = self.shell.leftStack.sizePolicy()
        vertical = QSizePolicy.Policy.Ignored if (queues_active or scripts_active) else QSizePolicy.Policy.Preferred
        horizontal = QSizePolicy.Policy.Ignored if scripts_active else self._left_stack_default_horizontal_policy
        if stack_policy.verticalPolicy() != vertical or stack_policy.horizontalPolicy() != horizontal:
            stack_policy.setVerticalPolicy(vertical)
            stack_policy.setHorizontalPolicy(horizontal)
            self.shell.leftStack.setSizePolicy(stack_policy)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if (queues_active or scripts_active)
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        buttons = (
            self.form.connectionNavigationButton,
            self.form.generationNavigationButton,
            self.form.queueNavigationButton,
            self.form.imagesNavigationButton,
        )
        # Utilities have different parents now; synchronize selection explicitly
        # instead of relying on sibling-only Qt auto-exclusivity.
        for position, button in enumerate(buttons):
            button.setAutoExclusive(False)
            button.setChecked(not logs_active and index == position)
            button.setAutoExclusive(True)
        workflows_button = self.form.workflowsNavigationButton
        workflows_button.setAutoExclusive(False)
        workflows_button.setChecked(workflows_active)
        workflows_button.setAutoExclusive(True)
        images_active = self.form.workspaceStack.currentWidget() is self.images_page
        self.grid_index_label.setVisible(images_active and development_mode())
        self.gallery.set_active(images_active)
        self._place_export_panel()
        self._responsive_workspace()

    def _retire_activity_presentation(self) -> None:
        """Unsubscribe before Qt destroys the activity overlay's child widgets."""
        self.activity.unlisten(self._sync_run_control)
        self.activity.unlisten(self._sync_activity_progress)
        # The window owns the settling timer and stops it during destruction.

    @profile_slow_gui_callback("main.sync_run_control")
    def _sync_run_control(self) -> None:
        if self.progress_strip.retired:
            return
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
            detail=" ".join(
                part for part in (state.detail, " · ".join(p for p in (state.elapsed, state.eta) if p)) if part
            ),
        )

    def _align_run_control(self) -> None:
        """Align the header control with Queues in the primary navigation row."""
        shell = self.shell
        anchor = shell.queueNavigationButton.geometry()
        if not (shell.brandFrame.isVisible() and shell.navigationFrame.isVisible()) or anchor.width() <= 0:
            return
        control, offset = shell.runControl, shell.runControlOffset
        # The Queue button sits between the offset and the control. Its fixed width plus one more layout spacing are
        # constants, not read from positions that lag a pending relayout, so the target cannot oscillate (hovering
        # the logo requests layouts and used to make the icons drift).
        gap = shell.quickQueueButton.width() + max(0, shell.brandLayout.spacing())
        width = max(0, anchor.center().x() - control.width() // 2 - offset.x() - gap)
        if offset.width() != width:
            offset.setFixedWidth(width)

    def _fit_context_pages(self, *_args) -> None:
        stack = self.shell.leftStack
        for index in range(stack.count()):
            page = stack.widget(index)
            policy = page.sizePolicy()
            if policy.horizontalPolicy() != QSizePolicy.Policy.Ignored:
                policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                page.setSizePolicy(policy)

    def _fit_inspector_page(self, *_args) -> None:
        stack = self.shell.rightStack
        for index in range(stack.count()):
            page = stack.widget(index)
            policy = page.sizePolicy()
            shown = page is stack.currentWidget()
            # Scripts has long hook identifiers inside a scroll viewport whose
            # horizontal bar is intentionally disabled. Ignore its size hint
            # when active, so it cannot overflow the shared right rail.
            scripts_inspector = page is self.scripts.right or (
                hasattr(self, "workflows") and page is self.workflows.inspector
            )
            horizontal = (
                QSizePolicy.Policy.Ignored if (not shown or scripts_inspector) else QSizePolicy.Policy.Preferred
            )
            if policy.horizontalPolicy() != horizontal:
                policy.setHorizontalPolicy(horizontal)
                page.setSizePolicy(policy)

    def _responsive_workspace(self) -> None:
        shell = self.shell
        narrow = self.form.width() < 1100
        logs = self.form.workspaceStack.currentWidget() is self.logs_marker
        images = self.form.workspaceStack.currentWidget() is self.images_page
        if self._logs_active != logs:
            self._logs_active = logs
            if logs:
                self._logs_previous_sizes = shell.workspaceSplitter.sizes()
                for slot, frame in (
                    (self.logs_form.logsBrandSlot, shell.brandFrame),
                    (self.logs_form.logsNavigationSlot, shell.navigationFrame),
                ):
                    # Primary navigation needs its own size hint;
                    # the old 248–300 px rail width clips them in a header row.
                    width = shell.leftRail.width()
                    if frame is shell.navigationFrame:
                        width = max(width, frame.sizeHint().width())
                    slot.setFixedSize(width, frame.height())
                    layout = slot.layout()
                    if layout is None:
                        layout = QHBoxLayout(slot)
                        layout.setContentsMargins(0, 0, 0, 0)
                    layout.addWidget(frame)
            elif self._gallery_chrome_active is False:
                shell.leftRailLayout.insertWidget(0, shell.brandFrame)
                shell.leftRailLayout.insertWidget(1, shell.navigationFrame)
                shell.brandFrame.show()
                shell.navigationFrame.show()
                shell.workspaceSplitter.setSizes(self._logs_previous_sizes)
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
            elif not logs:
                shell.leftRailLayout.insertWidget(0, shell.brandFrame)
                shell.leftRailLayout.insertWidget(1, shell.navigationFrame)
            shell.brandFrame.show()
            shell.navigationFrame.show()
            self.gallery_detail.galleryBrandSlot.setVisible(images)
            self.gallery_detail.galleryNavigationSlot.setVisible(images)
            if not images and self._gallery_previous_sizes:
                shell.workspaceSplitter.setSizes(self._gallery_previous_sizes)
        # Workflows keeps its inspector (node settings, Retry, Continue/Stop) at every width; its Library toggle folds
        # the left panels instead, and the brand and navigation then sit in the Workflows header row.
        workflows = hasattr(self, "workflows") and self.form.workspaceStack.currentWidget() is self.workflows_marker
        folded = workflows and not self.workflows.library_open(narrow)
        if hasattr(self, "workflows"):
            self._place_workflows_chrome(folded, moved_elsewhere=images or logs)
        # Logs and Images use the full-width center workspace, regardless of
        # whether the Images page changed during this navigation.
        shell.leftRail.setVisible(not images and not logs and not folded)
        shell.rightRail.setVisible(not images and not logs and (workflows or not narrow))
        self._place_images_header(images=images, logs=logs, narrow=narrow and not workflows)
        if self._logs_form is not None:
            self._logs_form.logsBrandSlot.setVisible(logs)
            self._logs_form.logsNavigationSlot.setVisible(logs)
        shell.runControl.setVisible(True)
        if self._scripts_active and self.form.workspaceStack.currentWidget() is self.generation_page:
            self.scripts.sync_responsive()
        elif hasattr(self, "scripts"):
            self.scripts.drawer.hide()
        if hasattr(self, "workflows"):
            self.workflows.sync_responsive(narrow)
        self._align_run_control()
        if hasattr(self, "_gui_heartbeat"):
            self._gui_heartbeat.sync()

    def _place_workflows_chrome(self, folded: bool, *, moved_elsewhere: bool) -> None:
        """Move the brand and navigation into the Workflows header while its Library is folded, and back after."""
        shell, page = self.shell, self.workflows.workspace
        slots = ((page.workflowBrandSlot, shell.brandFrame), (page.workflowNavigationSlot, shell.navigationFrame))
        if folded:
            for slot, frame in slots:
                if frame.parentWidget() is not slot:
                    width = shell.leftRail.maximumWidth()
                    if frame is shell.navigationFrame:
                        width = max(width, frame.sizeHint().width())
                    slot.setFixedSize(width, max(frame.height(), frame.sizeHint().height()))
                    slot.layout().addWidget(frame)
                frame.show()
            page.workflowShellHeader.show()
            return
        page.workflowShellHeader.hide()
        if not moved_elsewhere and shell.brandFrame.parentWidget() is page.workflowBrandSlot:
            shell.leftRailLayout.insertWidget(0, shell.brandFrame)
            shell.leftRailLayout.insertWidget(1, shell.navigationFrame)
            shell.brandFrame.show()
            shell.navigationFrame.show()

    def _place_images_header(self, *, images, logs, narrow) -> None:
        """One Images action above ordinary inspectors, never in Images or Logs."""
        shell = self.shell
        visible = not images and not logs
        shell.imagesHeaderHome.setVisible(visible and not narrow)
        shell.compactImagesSlot.setVisible(visible and narrow)
        if not visible:
            self._images_header.hide()
            return
        target = shell.compactImagesSlot if narrow else shell.imagesHeaderHome
        if self._images_header.parentWidget() is not target:
            target.layout().addWidget(self._images_header)
        self._images_header.setMinimumWidth(0)
        self._images_header.setMaximumWidth(300 if narrow else 16777215)
        self._images_header.show()

    @profile_slow_gui_callback("main.sync_activity_progress")
    def _sync_activity_progress(self):
        if self.progress_strip.retired:
            return
        state = self.activity.run_state
        # Each settled image or finished run pushes a refresh hint to Images (it checks the database cheaply).
        marker = (self.activity.generating, self.activity.dispatching, state.completed)
        if marker != self._last_run_marker:
            self._last_run_marker = marker
            self.gallery.notify_changed()
        if not (self.activity.generating or self.activity.dispatching):
            self.progress_strip.finish("run")
            return
        run_identity = "generation" if self.activity.generating else f"queue:{state.queue_id}"
        row = self.progress_strip.rows.get("run")
        if row is not None and row.property("runIdentity") != run_identity:
            self.progress_strip.finish("run")
        if "run" not in self.progress_strip.rows:
            label = "Generation" if self.activity.generating else "Queue progress"
            self.progress_strip.finish("generation-result")
            self.progress_strip.finish("queue-result")
            self.progress_strip.start(
                "run",
                label,
                self.generation.cancel if self.activity.generating else self.queue.cancel_run,
                stop_text="Stop waiting locally" if self.activity.generating else "Stop after current image",
            )
            self.progress_strip.rows["run"].setProperty("runIdentity", run_identity)
        self.progress_strip.update(
            "run",
            state.completed or 0,
            state.total or 0,
            detail=state.detail,
            elapsed=state.elapsed,
            eta=state.eta,
            stage=state.stage,
            wide=state.wide,
            queued=state.queued,
        )

    def show(self) -> None:
        apply_native_dark_titlebar(self.form)
        startup.mark("show: titlebar prepared")
        self.form.show()
        startup.mark("show: main form shown")
        QTimer.singleShot(0, self._align_run_control)
        apply_native_dark_titlebar(self.form)
        startup.mark("show: titlebar reapplied")

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
        self.generation.inpaint.restore(document.get("edit"))
        diagnostic_event(
            log,
            "app.last_session_restored",
            level=logging.INFO,
            data={
                "provider": provider if provider in BACKENDS else "-",
            },
        )

    def _session_document(self) -> dict:
        return {
            "provider": self.form.backendCombo.currentText(),
            "endpoint": self.form.endpointEdit.text().strip(),
            "form": self.generation.form_document(),
            "forms": self.generation.forms_document(),
            "edit": self.generation.inpaint.session_document(),
        }

    def _queue_images(self, count: int) -> None:
        """Generate image with Images above 1 (#279): a Repeat queue of single-image items, started now or, behind
        running work, added up next. The Queues page reports the outcome like the Queue popover does."""
        start, _, _ = self.queue.quick_state()
        self.queue.quick_create("start" if start else "add", count=count)
        self.remember()

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
            diagnostic_event(
                log,
                "app.last_session_saved",
                level=logging.INFO,
                data={},
            )
        except OSError as exc:
            diagnostic_event(
                log,
                "app.last_session_not_saved",
                level=logging.WARNING,
                data={
                    "reason": type(exc).__name__,
                },
            )

    def _endpoint_entered(self) -> None:
        diagnostic_event(
            log,
            "app.endpoint_changed",
            level=logging.INFO,
            data={
                "backend": self.form.backendCombo.currentText(),
            },
        )

    def _backend_changed(self, name: str) -> None:
        diagnostic_event(
            log,
            "app.provider_changed",
            level=logging.INFO,
            data={
                "backend": name,
            },
        )
        self.form.endpointEdit.setEnabled(name == "local")
        self.form.credentialBox.setEnabled(name == "cloud")
        self._selection_changed()

    def _selection_changed(self, *_) -> None:
        """A result belongs to the selection that was checked; a new provider or endpoint starts unchecked."""
        self.inline.dismiss()
        self._selection_revision = getattr(self, "_selection_revision", 0) + 1
        # Retire the old owner before cancelling: queued completion/cancellation cannot unlock a newer check.
        future, self._check_future = self._check_future, None
        self._check_token = None
        self._busy = False
        self.form.checkButton.setEnabled(True)
        if future is not None:
            future.cancel()
        self._present(
            Presentation("unknown", "Not checked", f"Check the {self.form.backendCombo.currentText()} connection.")
        )

    def _present(self, presentation: Presentation) -> None:
        label = self.form.statusLabel
        self.form.statusGlyphLabel.setPixmap(status_dot(presentation.level))
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

    async def _active_credential_id(self) -> tuple[str, str | None] | None:
        """Opaque identity and hint of the key cloud work would use now; a local read, no provider call."""
        try:
            credential = await resolve_credentials(EnvironmentCredentials(self._environ), self._optional_saved)
        except BackendError:
            return None
        return key_identity(credential.secret)

    def _key_directory(self) -> dict[str, str]:
        """Display name of every key available now, by opaque identity; read off the GUI thread."""
        try:
            status = credential_status(self._environ, self._saved)
        except Exception:
            return {}
        names = {key.credential_id: key.name for key in status["keys"]}
        env = status["environment"]
        if env["credential_id"] and env["credential_id"] not in names:
            names[env["credential_id"]] = (
                f"Environment key {env['hint']}\u2022\u2022\u2022\u2022" if env["hint"] else "Environment key"
            )
        return names

    def _show_active_key(self, label: str | None, count: int, total: int, level: str, zone: str) -> None:
        """Bottom-bar pill ``active/all``: today's local completed images for the active key and for all keys."""
        pill = self.usage_pill
        if label is None:
            text, state = f"\u2013/{total}", "neutral"
            tip = (
                f"No DT+ API key is available; {total} completed images were saved today across all keys. "
                "Set DRAWTHINGS_API_KEY or save a key on Connection."
            )
        else:
            text, state = f"{count}/{total}", {"ok": "ok", "warn": "warning", "over": "error"}[level]
            tip = (
                f"{label}: {count} of {total} completed images saved today across all keys "
                f"(day resets at midnight in {zone}). A local estimate from saved outputs, not a DT+ balance."
            )
        pill.setText(text)
        pill.setAccessibleName(f"Local DT+ completed images today, active key / all keys: {text}")
        pill.setToolTip(tip)
        pill.setProperty("state", state)
        pill.style().unpolish(pill)
        pill.style().polish(pill)

    def _usage_activity_changed(self) -> None:
        # The bottom-bar counter is global, even when the Connection card is hidden.
        self.cloud_usage.schedule_refresh()

    def _gui_heartbeat_context(self) -> dict | None:
        """Safe presentation context for one shared diagnostic, never execution authority."""
        if self.runner.closed or self.progress_strip.retired:
            return None
        current = self.form.workspaceStack.currentWidget()
        images = current is self.images_page
        operations = self.progress_strip.active_operations()
        if self.activity.generating:
            operation = "generation"
        elif self.activity.dispatching:
            operation = "queue_dispatch"
        elif operations:
            operation = ",".join(operations)[:128]
        elif images and self.form.isVisible() and not self.form.isMinimized():
            operation = "gallery_browsing"
        else:
            return None
        workspace = {
            self.images_page: "Images",
            self.logs_marker: "Logs",
            self.workflows_marker: "Workflows",
            self.queue_page: "Queues",
            self.generation_page: "Scripts" if self._scripts_active else "Generate",
        }.get(current, "Connection")
        context = {
            "workspace": workspace,
            "operation": operation,
            "window_active": self.form.isActiveWindow(),
            "window_minimized": self.form.isMinimized(),
        }
        if images:
            context.update(
                mode=self.gallery.mode,
                mounted=len(self.gallery.items),
                query_busy=self.gallery._loading or self.gallery._adjacent_loading,
                preparation_busy=self.gallery.preparation.active,
            )
        return context

    def _read_summary(self, key, read, apply) -> None:
        """Optional startup reads cannot block painting or replace a newer explicit refresh."""
        token = self._summary_reads[key] = object()
        self.runner.submit(
            asyncio.to_thread(read),
            lambda result, error: apply(result, error) if self._summary_reads.get(key) is token else None,
        )

    def refresh_credentials(self, *, background: bool = False) -> None:
        self._summary_reads.pop("credentials", None)
        if background:
            self.form.credentialStatusLabel.setText("Checking credential availability…")
            self.form.removeKeyButton.setEnabled(False)
            self._read_summary(
                "credentials", lambda: credential_status(self._environ, self._saved), self._credentials_read
            )
            return
        self.form.keyEdit.clear()
        self.form.keyNameEdit.clear()
        self._credentials_read(credential_status(self._environ, self._saved), None)
        if getattr(self, "cloud_usage", None) is not None:
            self.cloud_usage.schedule_refresh()  # the active-key mark follows a saved or removed key

    def _credentials_read(self, status, error) -> None:
        form = self.form
        if error is not None:
            form.credentialStatusLabel.setText("Credential availability could not be read; check again or enter a key.")
            return
        env = status["environment"]
        selected = status["selected"]
        combo = form.keySelectCombo
        with QSignalBlocker(combo):
            combo.clear()
            for key in status["keys"]:
                combo.addItem(f"{key.name} \u00b7 saved", key.id)
                if selected == "saved" and key.active:
                    combo.setCurrentIndex(combo.count() - 1)
            if env["present"]:
                hint = f" {env['hint']}\u2022\u2022\u2022\u2022" if env["hint"] else ""
                combo.addItem(f"Environment key{hint} \u00b7 DRAWTHINGS_API_KEY", ENVIRONMENT)
                if selected == "environment":
                    combo.setCurrentIndex(combo.count() - 1)
        combo.setEnabled(combo.count() > 1)
        combo.setVisible(combo.count() > 0)
        form.keySelectLabel.setVisible(combo.count() > 0)
        active = next((key for key in status["keys"] if key.active), None)
        if selected == "saved" and active is not None:
            text = f"Using {active.name}, saved in Windows Credential Manager."
            if env["present"]:
                text += " DRAWTHINGS_API_KEY is also set but is not used; select it above to use it."
        elif selected == "environment":
            hint = f" ({env['hint']}\u2022\u2022\u2022\u2022)" if env["hint"] else ""
            text = f"Using DRAWTHINGS_API_KEY from the environment{hint}."
            if status["keys"]:
                text += " Saved keys are not used while it is selected."
        else:
            text = "No API key available." + (
                " Secure storage is unavailable here." if status["saved"] == "unavailable" else ""
            )
        form.credentialStatusLabel.setText(text)
        # Remove acts on the selected saved key only; the environment key is removed by unsetting the variable.
        removable = selected == "saved" and active is not None
        form.removeKeyButton.setEnabled(removable)
        form.removeKeyButton.setToolTip(
            "Remove the selected saved key."
            if removable
            else "An environment key cannot be removed here: unset DRAWTHINGS_API_KEY and restart."
            if selected == "environment"
            else "No saved key to remove."
        )

    def key_selected(self, _index: int = -1) -> None:
        choice = self.form.keySelectCombo.currentData()
        if choice is None:
            return
        try:
            self._saved.select(choice)
        except (BackendError, ValidationError, SecureStorageUnavailable) as exc:
            code = exc.failure.code if isinstance(exc, BackendError) else str(exc)
            self.form.credentialStatusLabel.setText(f"Key not switched: {code}")
            self.refresh_credentials()
            return
        diagnostic_event(
            log,
            "app.api_key_switched",
            level=logging.INFO,
            data={"to": "environment" if choice == ENVIRONMENT else "saved"},
        )
        self.refresh_credentials()
        self._selection_changed()
        self.generation.credentials_changed()

    @staticmethod
    def _human_bytes(value: int) -> str:
        amount = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if amount < 1024 or unit == "GB":
                return f"{amount:.0f} {unit}" if unit in ("B", "KB") else f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{value} B"

    def refresh_presets(self, *, background: bool = False) -> None:
        self._summary_reads.pop("presets", None)
        if background:
            self.form.presetLibraryStatusLabel.setText("Loading preset library…")
            self.form.exportPresetsButton.setEnabled(False)
            self._read_summary("presets", lambda: len(load_presets(self.state_dir)), self._presets_read)
            return
        try:
            count = len(load_presets(self.state_dir))
        except ValidationError as exc:
            self._presets_read(None, exc)
        else:
            self._presets_read(count, None)

    def _presets_read(self, count, error) -> None:
        if error is not None:
            self.form.presetLibraryStatusLabel.setText("Preset library is unreadable; import or refresh to retry.")
            self.form.exportPresetsButton.setEnabled(False)
            return
        self.form.presetLibraryStatusLabel.setText(
            f"{count} named preset" + ("" if count == 1 else "s") + " in the durable library."
            if count
            else "No imported presets."
        )
        self.form.exportPresetsButton.setEnabled(count > 0)

    def import_presets(self) -> None:
        if self.inline.commit(self.form.importPresetsButton):
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self.form,
            "Import Draw Things presets",
            str(self.state_dir),
            "Draw Things presets (*.json)",
            remember_as="presets",
        )
        if not chosen:
            return

        def proceed():
            try:
                count = import_presets(Path(chosen), self.state_dir)
            except (ValidationError, OSError) as exc:
                InlineNotice.error(self.form, "Presets not imported", str(exc))
                return
            self.refresh_presets()
            self.generation.refresh_presets()
            self.form.statusBar().showMessage(f"Imported {count} Draw Things preset(s).", 5000)

        confirm_action(
            self.form,
            self.form.importPresetsButton,
            "Replace preset library?",
            "Importing replaces the entire durable preset library, including presets saved from Generate. Continue?",
            proceed,
        )

    def export_presets(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self.form,
            "Export Draw Things presets",
            str(Path.home() / "custom_configs.json"),
            "JSON (*.json)",
            remember_as="presets",
        )
        if not chosen:
            return
        try:
            count = export_presets(self.state_dir, Path(chosen))
        except (ValidationError, OSError) as exc:
            InlineNotice.error(self.form, "Presets not exported", str(exc))
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
            diagnostic_event(
                log,
                "app.cache_measurement_failed",
                level=logging.WARNING,
                data={
                    "reason": type(failure).__name__,
                },
            )
            self.form.cacheStatusLabel.setText(
                str(failure)
                if isinstance(failure, (CacheBusyError, CacheCleanupError, CacheSafetyError))
                else "Disposable cache size unavailable; refresh to retry."
            )
            return
        labels = {item.name: f"{self._human_bytes(item.bytes)} ({item.files} files)" for item in usage}
        self.form.cacheStatusLabel.setText(
            f"Models {labels['inventory']} · Thumbnails {labels['thumbnails']} · Derived {labels['derived']}"
        )

    def clear_cache(self, categories: tuple[str, ...]) -> None:
        button = {
            ("inventory",): self.form.clearInventoryCacheButton,
            ("thumbnails",): self.form.clearThumbnailCacheButton,
            ("derived",): self.form.clearDerivedCacheButton,
        }.get(categories, self.form.clearAllCacheButton)
        if self.inline.commit(button):
            return
        if self.activity.generating or self.activity.dispatching or self.image_export.running:
            InlineNotice.warning(
                self.form, "Cache is busy", "Stop the active generation, queue dispatch or image export before cleanup."
            )
            return
        names = ", ".join(categories)

        def proceed():
            if self.activity.generating or self.activity.dispatching or self.image_export.running:
                InlineNotice.warning(
                    self.form, "Action is busy", "Wait for generation, queue dispatch or image export to finish."
                )
                return

            async def purge():
                return await asyncio.to_thread(purge_cache, self.state_dir, categories)

            self.progress_strip.start("cache", "Clearing disposable cache", detail=f"{names}; durable data preserved.")
            self.runner.submit(purge(), lambda _, failure: self._cache_cleared(names, failure))

        confirm_action(
            self.form,
            button,
            "Clear disposable cache?",
            f"Delete disposable {names} cache? Outputs, attempts, queues, presets, settings, credentials and model assets are preserved.",
            proceed,
            danger=True,
        )

    def _cache_cleared(self, names: str, failure) -> None:
        self.progress_strip.finish("cache")
        if failure is not None:
            diagnostic_event(
                log,
                "app.cache_cleanup_failed",
                level=logging.WARNING,
                data={
                    "reason": type(failure).__name__,
                },
            )
            self.progress_strip.complete(
                "cache",
                "Cache not cleared",
                str(failure)
                if isinstance(failure, (CacheBusyError, CacheCleanupError, CacheSafetyError))
                else "Cleanup may be partial; refresh sizes and retry. Check cache-folder permissions.",
                "error",
            )
            self.refresh_cache()
            return
        self.refresh_cache()
        self.progress_strip.complete(
            "cache", "Disposable cache cleared", f"{names}; outputs, queues, settings and model assets preserved."
        )

    def _reuse_output(self, request: dict, identity: str) -> None:
        self.generation.reuse_saved_request(request, identity)
        self._show_generation()

    def _reuse_queue_request(self, request: dict) -> None:
        """Edit as new queue: a recorded text-to-image item fills Generate, which leaves Inpaint for it."""
        self.generation_page.textToImageModeButton.setChecked(True)
        self.generation.reuse_saved_request(request, "", origin="the queue's first item")

    def _edit_output(self, path: str) -> None:
        self._show_generation()
        self.generation.edit_saved_image(path)

    def _regenerate_output(self, request: dict, identity: str) -> None:
        if self.inline.commit(self.gallery.regenerate_button):
            return
        if self.activity.generating or self.activity.dispatching:
            InlineNotice.warning(
                self.form, "Generation is busy", "Stop the active generation or queue dispatch before regenerating."
            )
            return

        def proceed():
            if self.activity.generating or self.activity.dispatching:
                InlineNotice.warning(self.form, "Action is busy", "Wait for generation or queue dispatch to finish.")
                return
            self._show_generation()
            if not self.generation.regenerate_saved_request(request, identity):
                self.form.statusBar().showMessage("Regeneration was not submitted; Generate was left unchanged.", 5000)

        confirm_action(
            self.form,
            self.gallery.regenerate_button,
            "Regenerate saved output?",
            "This creates a fresh request and may consume provider generation credits. The original output is preserved.",
            proceed,
        )

    def refresh_custom_loras(self, *, background: bool = False) -> None:
        """Read catalogue and managed-file usage away from Qt, including after navigation or removal."""
        if self._removing_retained_lora:
            return
        self._summary_reads.pop("loras", None)
        self.form.retainedLorasStatusLabel.setText("Loading retained LoRAs…")
        self.form.removeRetainedLoraButton.setEnabled(False)

        def read():
            store = LoraStore(self.state_dir)
            return len(Catalogue(self.state_dir).user_loras()), store.entries(), store.usage()

        self._read_summary("loras", read, self._custom_loras_read)

    def _custom_loras_read(self, result, error) -> None:
        if self._removing_retained_lora:
            return
        if error is not None:
            self.form.customLorasStatusLabel.setText("Custom LoRA definitions or retained files could not be read.")
            self.form.retainedLorasStatusLabel.setText("Retained files unavailable; reopen Connection to retry.")
            self.form.clearCustomLorasButton.setEnabled(False)
            self.form.removeRetainedLoraButton.setEnabled(False)
            return
        count, entries, (stored_count, stored_bytes) = result
        self.form.customLorasStatusLabel.setText(
            f"{count} custom LoRA definition" + ("" if count == 1 else "s") + " imported."
            if count
            else "No custom LoRA definitions imported."
        )
        self.form.clearCustomLorasButton.setEnabled(count > 0)
        self.form.retainedLorasStatusLabel.setText(
            f"{stored_count} retained LoRA" + ("" if stored_count == 1 else "s")
            + f" · {self._human_bytes(stored_bytes)} stored locally."
            if stored_count
            else "No locally retained LoRAs."
        )
        listing = self.form.retainedLorasList
        selected = listing.currentItem()
        identity = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        listing.clear()
        for entry in sorted(entries, key=lambda item: str(item.get("name", "")).casefold()):
            name, sha256, size = entry.get("name"), entry.get("sha256"), entry.get("size")
            if not isinstance(name, str) or not isinstance(sha256, str) or not isinstance(size, int):
                continue
            item = QListWidgetItem(f"{name} · {self._human_bytes(size)}")
            item.setData(Qt.ItemDataRole.UserRole, (sha256, size))
            item.setToolTip(
                f"{name}\n{self._human_bytes(size)} · SHA-256 {sha256}\n"
                "Remove deletes only this managed copy, not the original, remote asset, or history."
            )
            listing.addItem(item)
            if (sha256, size) == identity:
                listing.setCurrentItem(item)
        self._retained_lora_selection_changed()

    def _retained_lora_selection_changed(self) -> None:
        self.form.removeRetainedLoraButton.setEnabled(
            not self._removing_retained_lora and bool(self.form.retainedLorasList.selectedItems())
        )

    def remove_retained_lora(self) -> None:
        """Click Remove or press Delete on a selected retained copy: no confirmation or other deletion."""
        if self._removing_retained_lora:
            return
        selected = self.form.retainedLorasList.selectedItems()
        if not selected:
            return
        sha256, size = selected[0].data(Qt.ItemDataRole.UserRole)
        self._removing_retained_lora = True
        self.form.removeRetainedLoraButton.setEnabled(False)
        self.form.retainedLorasList.setEnabled(False)
        self.runner.submit(
            asyncio.to_thread(LoraStore(self.state_dir).remove, sha256, size),
            self._retained_lora_removed,
        )

    def _retained_lora_removed(self, removed, error) -> None:
        self._removing_retained_lora = False
        self.form.retainedLorasList.setEnabled(True)
        if error is not None:
            InlineNotice.error(self.form, "Retained LoRA not removed", str(error))
        elif removed:
            self.form.statusBar().showMessage("Local retained LoRA removed. Remote and saved results unchanged.", 5000)
        self.refresh_custom_loras(background=True)

    def import_custom_loras(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self.form, "Import custom LoRAs", str(self.state_dir), "JSON (*.json)")
        if not chosen:
            return
        try:
            count = import_user_loras(Path(chosen), self.state_dir)
        except (OSError, ValueError) as exc:
            InlineNotice.error(self.form, "Custom LoRAs not imported", str(exc))
            return
        self.refresh_custom_loras()
        self.generation.reload_custom_loras()
        self.form.statusBar().showMessage(
            f"Imported {count} custom LoRA definition" + ("" if count == 1 else "s") + ".",
            5000,
        )

    def clear_custom_loras(self) -> None:
        if self.inline.commit(self.form.clearCustomLorasButton):
            return
        catalogue = Catalogue(self.state_dir)
        if not catalogue.user_loras_path.exists():
            self.refresh_custom_loras()
            return

        def proceed():
            try:
                catalogue.user_loras_path.unlink()
            except OSError as exc:
                InlineNotice.error(self.form, "Custom LoRAs not cleared", type(exc).__name__)
                return
            self.refresh_custom_loras()
            self.generation.reload_custom_loras()

        confirm_action(
            self.form,
            self.form.clearCustomLorasButton,
            "Clear custom LoRAs",
            "Remove the imported custom LoRA definitions? Bundled DTFlow catalogue data is not affected.",
            proceed,
            danger=True,
        )

    def dialog_folder_problem(self, reason: str) -> None:
        """Once per session: the remembered-folder file could not be read or saved; dialogs keep working."""
        activity_strip(self.form).complete(
            "dialog-folders",
            "Folder not remembered",
            "Remembered folders could not be read or saved. File and folder pickers still work.",
            "warning",
        )

    def refresh_remembered_folders(self) -> None:
        """List every selector opened so far with its folder, most recent first."""
        listing = self.form.rememberedFoldersList
        listing.clear()
        folders = file_dialogs.remembered()
        for key, title, path in folders:
            item = QListWidgetItem(f"{title or key}  ·  {path}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(f"{title or key}\n{path}")
            listing.addItem(item)
        self.form.rememberedFoldersStatusLabel.setText(
            f"{len(folders)} folder" + ("" if len(folders) == 1 else "s") if folders else "None yet"
        )
        # Up to four rows are visible (the rest scroll vertically); long entries are elided, never scrolled sideways.
        row = listing.sizeHintForRow(0) if folders else listing.fontMetrics().height() + 10
        listing.setFixedHeight(min(max(len(folders), 1), 4) * row + 2 * listing.frameWidth())
        self.form.forgetAllFoldersButton.setEnabled(bool(folders))
        self.refresh_remembered_folders_buttons()

    def refresh_remembered_folders_buttons(self) -> None:
        self.form.forgetFolderButton.setEnabled(bool(self.form.rememberedFoldersList.selectedItems()))

    def forget_selected_folder(self) -> None:
        keys = [item.data(Qt.ItemDataRole.UserRole) for item in self.form.rememberedFoldersList.selectedItems()]
        self._forget_folders(keys)

    def forget_all_folders(self) -> None:
        self._forget_folders(None)

    def _forget_folders(self, keys) -> None:
        try:
            file_dialogs.forget(keys)
        except (OSError, ValueError) as exc:
            InlineNotice.error(self.form, "Folders not forgotten", str(exc))
        self.refresh_remembered_folders()

    def start_startup_check(self) -> None:
        """One non-generative check after the app's first event-loop turn, never periodic polling."""
        if not self._startup_check_started and not self.runner.closed:
            self.check(automatic=True)

    def check(self, *, automatic: bool = False) -> None:
        if self._busy or self.runner.closed:
            return
        self._startup_check_started = True  # An early manual check supersedes the queued startup check.
        selection = BackendSelection(self.form.backendCombo.currentText(), self.form.endpointEdit.text() or None)
        self._busy = True
        self.form.checkButton.setEnabled(False)
        self._present(Presentation("warning", "Checking", ""))
        diagnostic_event(
            log,
            "app.connection_check_started",
            level=logging.INFO,
            data={
                "backend": selection.name,
            },
        )
        revision = self._selection_revision
        credential_context = self._credential_context()
        token = self._check_token = object()
        self._check_future = self.runner.submit(
            self._check(selection, self._saved, automatic),
            lambda status, failure: self._checked(status, failure, revision, credential_context, token),
        )

    def _credential_context(self):
        # Compare only in memory; never put credential values in diagnostics or presentation.
        environ = os.environ if self._environ is None else self._environ
        return tuple((key in environ, environ.get(key)) for key in ("DRAWTHINGS_API_KEY", "DRAWTHINGS_SHARED_SECRET"))

    async def _check(self, selection, saved, automatic=False):
        if automatic:
            if selection.name not in BACKENDS:
                return None
            if selection.name == "local" and not (selection.endpoint or "").strip():
                return None
            if selection.name == "cloud":
                status = await asyncio.to_thread(credential_status, self._environ, saved)
                if status["selected"] == "none":
                    return None
        saved = saved if await asyncio.to_thread(saved.available) else None
        return await check_connection(selection, saved=saved)

    def _checked(self, status, failure, revision=None, credential_context=None, token=None) -> None:
        if token is not None and token is not self._check_token:
            return
        self._check_future = None
        self._busy = False
        self.form.checkButton.setEnabled(True)
        if revision is not None and revision != self._selection_revision:
            return  # The provider/endpoint changed while the old check was running.
        if credential_context is not None and credential_context != self._credential_context():
            self._selection_changed()
            return
        shown = (
            present_failure(failure)
            if failure
            else present_status(status)
            if status is not None
            else Presentation(
                "unknown", "Not checked", "Configure the endpoint or cloud API key, then check the connection."
            )
        )
        diagnostic_event(
            log,
            "app.connection_check_result",
            level=logging.INFO,
            data={
                "level": shown.level,
            },
        )
        self._present(shown)
        if failure is None and shown.level == "ok":
            self.remember()

    def save_key(self) -> None:
        try:
            self._saved.save(self.form.keyEdit.text(), self.form.keyNameEdit.text())
        except (BackendError, ValidationError, SecureStorageUnavailable) as exc:
            code = exc.failure.code if isinstance(exc, BackendError) else str(exc)
            diagnostic_event(
                log,
                "app.api_key_not_saved",
                level=logging.WARNING,
                data={
                    "reason": type(exc).__name__,
                },
            )
            self.form.credentialStatusLabel.setText(f"Key not saved: {code}")
            self.form.keyEdit.clear()
            self.form.keyNameEdit.clear()
            return
        diagnostic_event(
            log,
            "app.api_key_saved",
            level=logging.INFO,
            data={},
        )
        self.refresh_credentials()
        self._selection_changed()
        self.generation.credentials_changed()

    def remove_key(self) -> None:
        if self.inline.commit(self.form.removeKeyButton):
            return

        key_id = self.form.keySelectCombo.currentData()
        name = (self.form.keySelectCombo.currentText() or "the saved key").split(" \u00b7 ")[0]

        def proceed():
            try:
                self._saved.remove(key_id)
            except BackendError as exc:
                diagnostic_event(
                    log,
                    "app.api_key_not_removed",
                    level=logging.WARNING,
                    data={
                        "code": exc.failure.code,
                    },
                )
                self.form.credentialStatusLabel.setText(f"Key not removed: {exc.failure.code}")
                return
            diagnostic_event(
                log,
                "app.api_key_removed",
                level=logging.INFO,
                data={},
            )
            self.refresh_credentials()
            self._selection_changed()
            self.generation.credentials_changed()

        confirm_action(
            self.form,
            self.form.removeKeyButton,
            "Remove saved key",
            f"Remove {name} from Windows Credential Manager? Other saved keys and the environment key are not touched.",
            proceed,
        )

    def _recycle_busy(self) -> bool:
        """Keep UI-generated exports and local dispatch from racing with recycling."""
        return bool(
            self.image_export.running
            or self.generation._busy
            or self.activity.generating
            or self.queue.dispatching_here
            or self.activity.dispatching
        )

    def _request_gallery_recycle(self, identities: tuple[str, ...], hidden: int, query: GalleryQuery) -> None:
        """Only the confirmation service may move explicitly checked originals."""
        self.image_recycle.request(identities, hidden, query)

    def _confirm_close(self) -> bool:
        if self.runner.closed:
            return True
        if self.image_recycle.running:
            self.image_recycle.cancel()
            InlineNotice.information(
                self.form,
                "Image removal in progress",
                "Stop requested. Wait for the current image and result summary before closing.",
            )
            return False
        if self.inline._close_allowed:
            return True
        if not self.queue.dispatching_here:
            return True
        if self.inline._armed:
            return False

        def close_confirmed():
            self.inline._close_allowed = True
            try:
                self.form.close()
            finally:
                self.inline._close_allowed = False  # consent applies only to this close attempt

        self.inline.show(
            "Close DTFlow?",
            "Closing stops dispatch. Active attempts stop waiting locally and may end as outcome unknown; remote cancellation is not confirmed.",
            "confirm",
        )
        self.inline.escape.setEnabled(True)
        self.inline._set_dismiss_label(cross=False)
        self.inline.set_accept("Confirm close", close_confirmed)
        return False

    def _request_recycle_reconciliation(self) -> None:
        """Settle interrupted intent without inferring restored content from file stats."""
        self.runner.submit(asyncio.to_thread(self._reconcile_images), self._images_reconciled)

    def _reconcile_images(self):
        """Inspect interrupted file operations under the original-file lease."""
        with recycle_batch_lock(self.state_dir):
            with original_images_lock(self.state_dir, timeout=15):
                return reconcile_images(self.state_dir)

    def _images_reconciled(self, outcome, error) -> None:
        if error is not None:
            diagnostic_event(
                log,
                "app.image_recycle_reconciliation_skipped",
                level=logging.WARNING,
                data={
                    "reason": type(error).__name__,
                },
            )
            if isinstance(error, (OriginalImagesUnavailableError, OriginalImagesBusyError)):
                message = (
                    "Interrupted removal intents could not be reconciled because original-file access "
                    "is unavailable or busy. Pending items remain blocked; restore readiness, "
                    "then refresh Images."
                )
                InlineNotice.warning(self.form, "Removal reconciliation deferred", message)
            else:
                InlineNotice.error(
                    self.form,
                    "Removal reconciliation failed",
                    "The durable removal ledger could not be reconciled. Interrupted items remain "
                    "blocked; inspect the local state before retrying.",
                )
            return
        if outcome and any(outcome):
            self.gallery.refresh(preserve=True, trigger="removal_reconcile")
        if outcome is not None and outcome.delete_uncertain:
            count = outcome.delete_uncertain
            InlineNotice.warning(
                self.form,
                "Permanent deletion requires review",
                f"{count} interrupted permanent deletion(s) became uncertain. "
                "Deletion may or may not have occurred. Inspect the originals manually; "
                "DTFlow will not retry or repeat this alert on later starts.",
            )

    def _open_checked_export(self) -> None:
        """Follow the Images Export toggle without changing tabs or submitting work."""
        self._gallery_export_open = self.gallery.export_button.isChecked()
        self._place_export_panel()
        if self._gallery_export_open:
            target = (
                self.gallery_detail.galleryExportCloseButton
                if self.image_export.running
                else self.form.imageExportFolderEdit
            )
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _close_gallery_export(self) -> None:
        """Hiding controls never cancels the shared export operation."""
        self._gallery_export_open = False
        self._place_export_panel()
        target = (
            self.gallery.export_button
            if self.gallery.export_button.isEnabled()
            else self.gallery.select_filtered_button
        )
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _place_export_panel(self) -> None:
        """Keep one card/controller/operation, mounting it in the active page's host."""
        inline = self._gallery_export_open and self.form.workspaceStack.currentWidget() is self.images_page
        host = self.gallery_detail.galleryExportHost if inline else self.form.imageExportConnectionHost
        card = self.form.imageExportBox
        if card.parentWidget() is not host:
            if inline and not self.image_export.running:
                self.image_export.select_scope("checked")
            host.layout().addWidget(card)
            card.show()  # Reparenting hides QWidget; the same controls and progress survive.
        self.form.imageExportScopeLabel.setVisible(not inline)
        self.form.imageExportScopeCombo.setVisible(not inline)
        self.gallery_detail.galleryExportScroll.setVisible(inline)
        button = self.gallery.export_button
        button.setChecked(inline)
        button.setAccessibleName("Hide export panel" if inline else "Show export panel")
        button.setToolTip(
            "Hide the export panel without cancelling an active export."
            if inline
            else "Show export settings beside Images. Check images before starting Export JPEGs."
        )

    def _export_selection(self, scope: str) -> GalleryQuery | tuple[str, ...]:
        """Freeze presentation choices; export service rechecks committed evidence."""
        if self._gallery_export_open and self.form.workspaceStack.currentWidget() is self.images_page:
            scope = "checked"  # Images has no scope selector; never inherit a hidden Connection scope.
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
        InlineNotice.about(self.form, "About DTFlow", f"{display_identity()}\n\nState folder: {self.state_dir}")

    def close(self) -> None:
        # Request consent before saving/stopping: a refused close leaves the
        # running dispatcher and its durable uncertainty safeguards intact.
        if not self.form.close() or self.runner.closed:
            return
        self.remember(background=False)
        self.image_export.cancel()
        self.image_recycle.cancel()
        self.gallery.save_now()
        diagnostic_event(
            log,
            "app.window_closed",
            level=logging.INFO,
            data={},
        )
        self.runner.shutdown()

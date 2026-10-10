"""Generate page controller: widgets in, shared services out. Owns no resolver, backend or persistence rules.

Every service call runs on the shared ``AsyncRunner``; backend events cross to the GUI thread through a coalescing
``EventRelay``. Result wording comes from the shared presentation mapping and from durable evidence, never from a button
press: a local stop is reported as a local stop, and a possibly submitted attempt stays "outcome unknown".
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from dtflow.composition import BACKENDS
from dtflow.core.model_profiles import profile_for
from dtflow.core.models import (
    BackendEvent,
    CredentialProvider,
    Designation,
    InventorySnapshot,
    ValidationError,
    eligible_model_ids,
)
from dtflow.logging_setup import dev_enabled
from dtflow.services.catalogue import Catalogue
from dtflow.services.connection import (
    Presentation,
    present_generation_failure,
    present_interruption,
    present_outcome,
)
from dtflow.services.presets import apply_preset, builtin_presets, load_presets, save_preset
from dtflow.services.session import (
    InventoryDisplayMemory,
    generate_once,
    read_attempt,
    selection_for,
)
from dtflow.ui.activity import Activity, RunState
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.empty_hint import EmptyListHint
from dtflow.ui.generation_presenter import (
    FAKE_MODEL,
    SAMPLERS,
    FormError,
    FormValues,
    build_request,
    describe_failure,
    describe_request,
    form_from_document,
    form_to_document,
    read_configuration,
    save_configuration,
    snap_dimension,
    values_from_request,
)
from dtflow.ui.inline_feedback import InlineNotice, edit_text
from dtflow.ui.lora_panel import LoraAssetPanel
from dtflow.ui.runner import AsyncRunner, EventRelay

log = logging.getLogger(__name__)


def failure_reason(failure: BaseException) -> str:
    """Short log text for a failure: the backend's own code and category when it has them, else the type name."""
    detail = getattr(failure, "failure", None)
    code, category = getattr(detail, "code", None), getattr(detail, "category", None)
    if code:
        return f"{type(failure).__name__} code={code} category={getattr(category, 'value', category)}"
    return type(failure).__name__


PREVIEW_MIN_HEIGHT = 180
RESULT_PATH_ROLE = Qt.ItemDataRole.UserRole
METADATA_LIMIT = 256_000
WIRED_CONTROLS = (
    "promptEdit",
    "negativePromptEdit",
    "seedEdit",
    "stepsSpinBox",
    "widthSpinBox",
    "heightSpinBox",
    "guidanceSpinBox",
    "samplerCombo",
    "generationDomainCombo",
    "baseModelCombo",
    "modelCombo",
    "includeCloudCheck",
    "includeNicheCheck",
    "unclassifiedModelsButton",
    "refreshInventoryButton",
    "generateButton",
    "configurationCombo",
    "loadConfigurationButton",
    "saveConfigurationButton",
    "loraList",
    "triggerPrefixCheck",
    "assetList",
    "simulationScenarioCombo",
    "simulationDelaySpinBox",
    "simulationPreviewsCheck",
)
SIMULATED_OUTCOMES = (
    ("Succeeds", "success"),
    ("Rejected before submission", "reject"),
    ("Connection lost (outcome unknown)", "lost"),
    ("Terminal error", "terminal_error"),
    ("Finishes with no image", "empty"),
)
FIELD_WIDGETS = (
    "promptEdit",
    "modelCombo",
    "seedEdit",
    "widthSpinBox",
    "heightSpinBox",
    "stepsSpinBox",
    "guidanceSpinBox",
    "samplerCombo",
    "loraList",
)


def _choose_save(parent: QWidget, directory: Path) -> str:
    return QFileDialog.getSaveFileName(
        parent, "Save generation configuration", str(directory / "generation.json"), "JSON (*.json)"
    )[0]


def _choose_open(parent: QWidget, directory: Path) -> str:
    return QFileDialog.getOpenFileName(parent, "Load generation configuration", str(directory), "JSON (*.json)")[0]


RAIL_LABELS = (
    "backendSummaryLabel",
    "generationDomainLabel",
    "baseModelLabel",
    "seedLabel",
    "stepsLabel",
    "widthLabel",
    "heightLabel",
    "guidanceLabel",
    "samplerLabel",
    "simulationScenarioLabel",
    "simulationDelayLabel",
)


class GenerationPage:
    def __init__(
        self,
        page: QWidget,
        *,
        runner: AsyncRunner,
        backend_source: Callable[[], tuple[str, str | None]],
        saved_source: Callable[[], CredentialProvider | None],
        state_dir: Path,
        activity: Activity | None = None,
        no_cache: bool = False,
        cache_changed: Callable[[], None] | None = None,
        choose_save: Callable[[QWidget, Path], str] = _choose_save,
        choose_open: Callable[[QWidget, Path], str] = _choose_open,
    ):
        self.page = page
        self._runner = runner
        self._backend = backend_source
        self._saved = saved_source
        self._state = Path(state_dir)
        self._activity = activity or Activity()
        self._no_cache = no_cache
        self._cache_changed = cache_changed or (lambda: None)
        self._inventory_memory = InventoryDisplayMemory()
        self._inventory_source: tuple[str, str | None] | None = None
        self._inventory_revision = 0
        self._cache_request = None
        self._inventory_how = ""
        self._inventory_notice = ""
        self._inventory_error = ""
        self._pending_family: str | None = None
        self._catalogue = Catalogue(self._state)
        self._inventory_snapshot: InventorySnapshot | None = None
        self._updating_catalogue = False
        self._forms: dict[str, dict] = {}  # provider -> its remembered form fields (prompts are shared, not stored)
        self._provider: str | None = None
        self._previewed_seed: int | None = None
        self._activity.listen(self._sync_activity)
        page.destroyed.connect(self._unlisten_activity)
        self._choose_save, self._choose_open = choose_save, choose_open
        self._configs = self._state / "configs"
        self._relay = EventRelay(self._event, page)
        self._future = None
        self._generating = False
        self._request = None
        self._busy = False
        self._config_name: str | None = None
        self._reuse_source_identity: str | None = None
        self._metadata_path: Path | None = None
        self._saved_output_identities: dict[str, str] = {}
        self._submitted_backend: str | None = None
        self._empty_hints = (
            EmptyListHint(page.loraList, "No LoRAs selected\nAdd an adapter to this request."),
            EmptyListHint(page.assetList, "No local receipts\nPrepare a cloud asset to begin."),
            EmptyListHint(page.resultList, "No saved outputs yet"),
        )
        self.lora = LoraAssetPanel(
            page,
            runner=runner,
            backend_source=backend_source,
            saved_source=saved_source,
            state_dir=self._state,
            build_request=self._request_for_check,
            is_busy=lambda: self._busy,
            set_busy=self._set_busy,
        )
        for name in WIRED_CONTROLS:
            getattr(page, name).setEnabled(True)
        self._align_rail_labels()
        page.samplerCombo.addItems(SAMPLERS)
        for label, value in (("All", "all"), ("Image", "image"), ("Video", "video")):
            page.generationDomainCombo.addItem(label, value)
        page.generationDomainCombo.currentIndexChanged.connect(self._catalogue_filter_changed)
        page.baseModelCombo.currentIndexChanged.connect(self._catalogue_family_changed)
        page.includeCloudCheck.toggled.connect(self._catalogue_filter_changed)
        page.includeNicheCheck.toggled.connect(self._catalogue_filter_changed)
        page.modelCombo.currentTextChanged.connect(self._catalogue_checkpoint_changed)
        page.unclassifiedModelsButton.clicked.connect(self._show_unclassified_models)
        for label, scenario in SIMULATED_OUTCOMES:
            page.simulationScenarioCombo.addItem(label, scenario)
        page.generationBackendCombo.addItems(BACKENDS)
        page.generationBackendCombo.setToolTip("Chosen on the Connection page.")
        page.generateButton.clicked.connect(self.generate)
        page.refreshInventoryButton.clicked.connect(self.refresh)
        page.saveConfigurationButton.clicked.connect(self.save)
        page.savePresetButton.setEnabled(True)
        page.savePresetButton.clicked.connect(self.save_preset)
        page.loadConfigurationButton.clicked.connect(self.load)
        page.configurationCombo.activated.connect(self._config_chosen)
        page.openOutputFolderButton.clicked.connect(self.open_output_folder)
        page.copyOutputPathButton.clicked.connect(self.copy_output_path)
        self.openFileButton = QPushButton("Open file", page)
        self.metadataButton = QPushButton("Metadata", page)
        self.openFileButton.clicked.connect(self.open_output_file)
        self.metadataButton.clicked.connect(self.show_metadata)
        for button in (
            self.openFileButton,
            page.openOutputFolderButton,
            self.metadataButton,
            page.copyOutputPathButton,
        ):
            page.resultPreviewLabel.add_file_button(button)
        self._set_result_actions(False)
        page.modelCombo.custom_prompt = "Exact checkpoint identifier"
        page.modelCombo.setToolTip("Select a checkpoint. Search matches any part of its name.")
        page.promptEdit.setTabChangesFocus(True)
        icons = Path(__file__).parents[1] / "resources" / "images" / "gallery"
        self._expand_icon = QIcon(str(icons / "fullscreen.svg"))
        self._collapse_icon = QIcon(str(icons / "minimize.svg"))
        page.generateButton.setIcon(QIcon(str(icons / "sparkles.svg")))
        page.generateButton.setIconSize(QSize(16, 16))
        page.expandPromptButton.setIconSize(QSize(16, 16))
        self._set_prompt_expanded(page.expandPromptButton.isChecked())
        page.seedEdit.setToolTip(
            "0 picks a random seed for each submission; the seed used is recorded with the output."
        )
        for control in (page.expandPromptButton, page.detailsToggle):
            control.setEnabled(True)
        page.attemptDetails.hide()
        page.detailsToggle.toggled.connect(page.attemptDetails.setVisible)
        page.expandPromptButton.toggled.connect(self._set_prompt_expanded)
        page.resultPreviewLabel.enable_inspection()
        self.sync_backend()
        self._scan_configs()

    def _set_prompt_expanded(self, expanded: bool) -> None:
        """Match prompt height, icon and accessible label to the toggle state."""
        page = self.page
        page.promptEdit.setFixedHeight(180 if expanded else 52)
        button = page.expandPromptButton
        button.setIcon(self._collapse_icon if expanded else self._expand_icon)
        label = "Collapse prompt" if expanded else "Expand prompt"
        button.setAccessibleName(label)
        button.setToolTip(label)

    # -- context ---------------------------------------------------------------------------------------------------
    def sync_backend(self) -> None:
        """Mirror the Connection page's backend; it is read-only here so there is one selector."""
        name, _ = self._backend()
        if not name:
            return
        self.page.generationBackendCombo.setCurrentText(name)
        source = self._current_inventory_source()
        changed = source != self._inventory_source
        if changed:
            # Capture the leaving form BEFORE clearing availability/filter choices.
            if self._provider:
                self._forms[self._provider] = self._provider_form()
            self._inventory_source = source
            self._inventory_revision += 1
            self._cache_request = None
            self._inventory_notice = self._inventory_error = ""
            self._clear_inventory()
            self._updating_catalogue = True
            try:
                if self._provider == name:
                    self._apply_provider_form(self._forms.get(name, {}))
                else:
                    self._switch_provider(name, remember=False)
            finally:
                self._updating_catalogue = False
            catalogue = self._forms.get(name, {}).get("catalogue", {})
            self._pending_family = catalogue.get("base_model") if isinstance(catalogue, dict) else None
        self.page.simulationBox.setVisible(name == "fake")
        self.page.includeCloudCheck.setVisible(name == "local")
        if name != "local" and self.page.includeCloudCheck.isChecked():
            self.page.includeCloudCheck.setChecked(False)
        self.page.modelCombo.setToolTip(
            f"Select {FAKE_MODEL} or use a custom identifier."
            if name == "fake"
            else "Select a checkpoint. Search matches any part of its name; custom identifiers remain available."
        )
        self.lora.sync_backend()
        if changed:
            self._rebuild_catalogue(
                preferred_family=self._pending_family,
                preferred_checkpoint=self.page.modelCombo.currentText(),
                preserve_selection=True,
            )
            self.request_cached()

    def _current_inventory_source(self):
        name, endpoint = self._backend()
        return name, (endpoint.strip() if endpoint else None) if name == "local" else None

    def _clear_inventory(self):
        self._inventory_snapshot = None
        self._inventory_how = ""
        self._catalogue.set_inventory(None)
        self.lora.set_inventory(None)
        self._updating_catalogue = True
        try:
            self._fill_models(())
            self.page.baseModelCombo.clear()
        finally:
            self._updating_catalogue = False
        self._write_inventory_stats()

    def _values(self) -> FormValues:
        page = self.page
        return FormValues(
            page.promptEdit.toPlainText(),
            page.negativePromptEdit.text(),
            page.modelCombo.currentText(),
            page.seedEdit.text(),
            page.widthSpinBox.value(),
            page.heightSpinBox.value(),
            page.stepsSpinBox.value(),
            page.guidanceSpinBox.value(),
            page.samplerCombo.currentText(),
            self.lora.loras,
        )

    def _apply(self, values: FormValues) -> None:
        page = self.page
        page.promptEdit.setPlainText(values.prompt)
        page.negativePromptEdit.setText(values.negative_prompt)
        page.modelCombo.setCurrentText(values.model)
        page.seedEdit.setText(values.seed)
        page.widthSpinBox.setValue(values.width)
        page.heightSpinBox.setValue(values.height)
        page.stepsSpinBox.setValue(values.steps)
        page.guidanceSpinBox.setValue(values.guidance)
        self._filter_samplers(keep=values.sampler)

    def _filter_samplers(self, keep: str | None = None) -> None:
        """Offer the samplers that suit the selected model; a model with no profile offers all of them.

        ``keep`` is a sampler the caller restores on purpose (a loaded configuration), so it stays selectable even when
        the dataset would not suggest it. Otherwise an unsuitable current sampler switches to the model's recommended one.
        """
        page = self.page
        combo = page.samplerCombo
        model = page.modelCombo.currentText()
        entry = self._catalogue.checkpoint(model, include_unavailable=True) if model else None
        profile = profile_for(model, entry.base_model if entry else None) if model else None
        allowed = [item.value for item in profile.samplers] if profile else list(SAMPLERS)
        wanted = keep or combo.currentText()
        if wanted not in allowed:
            if keep:
                allowed.append(keep)
            elif profile:
                wanted = profile.sampler.value
        if [combo.itemText(i) for i in range(combo.count())] != allowed:
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(allowed)
            combo.blockSignals(False)
        combo.setCurrentText(wanted if wanted in allowed else allowed[0])

    PROVIDER_FIELDS = ("model", "seed", "width", "height", "steps", "guidance", "sampler", "loras")

    def _provider_form(self) -> dict:
        """The fields that belong to the provider in use: everything but the prompts, plus bound receipts."""
        document = form_to_document(self._values())
        form = {name: document[name] for name in self.PROVIDER_FIELDS}
        form["receipts"] = self.lora.receipts()
        form["catalogue"] = {
            "domain": self.page.generationDomainCombo.currentData() or "all",
            "base_model": self.page.baseModelCombo.currentData() or self._pending_family,
            "include_cloud": self.page.includeCloudCheck.isChecked() if self._provider == "local" else False,
            "include_niche": self.page.includeNicheCheck.isChecked(),
        }
        return form

    def _apply_provider_form(self, document: object) -> None:
        """Show a provider's remembered fields (defaults for one never used); prompts stay as typed."""
        current = self._values()
        values = replace(form_from_document(document), prompt=current.prompt, negative_prompt=current.negative_prompt)
        self._apply(values)
        receipts = document.get("receipts") if isinstance(document, dict) else None
        valid = (
            {k: v for k, v in receipts.items() if type(k) is str and type(v) is str}
            if isinstance(receipts, dict)
            else {}
        )
        self.lora.set_loras(values.loras, receipts=valid)
        catalogue = document.get("catalogue") if isinstance(document, dict) else None
        if isinstance(catalogue, dict):
            domain = catalogue.get("domain")
            index = self.page.generationDomainCombo.findData(domain if domain in ("all", "image", "video") else "all")
            self.page.generationDomainCombo.setCurrentIndex(max(index, 0))
            self.page.includeCloudCheck.setChecked(
                self._backend()[0] == "local" and catalogue.get("include_cloud") is True
            )
            self.page.includeNicheCheck.setChecked(catalogue.get("include_niche") is True)
            family = catalogue.get("base_model")
            if isinstance(family, str):
                self._pending_family = family
                self._rebuild_catalogue(
                    preferred_family=family,
                    preferred_checkpoint=values.model,
                    restore_niche=True,
                    preserve_selection=True,
                )

    def _switch_provider(self, name: str, *, remember: bool = True) -> None:
        """Remember the leaving provider's fields and restore the arriving one's (not shared between providers)."""
        if not name:
            return
        if self._provider is None:
            self._provider = name
        elif name != self._provider:
            if remember:
                self._forms[self._provider] = self._provider_form()
            self._provider = name
            self._apply_provider_form(self._forms.get(name, {}))
            log.info("form fields switched provider=%s remembered=%s", name, name in self._forms)

    def form_document(self) -> dict:
        """The remembered form values (credential-free, unvalidated)."""
        return form_to_document(self._values())

    def forms_document(self) -> dict:
        """Every provider's remembered fields, the one in use included."""
        forms = dict(self._forms)
        if self._provider is not None:
            forms[self._provider] = self._provider_form()
        return forms

    def restore(self, document: object, forms: object = None) -> None:
        """Apply a remembered form document and per-provider fields; bad fields fall back to defaults."""
        values = form_from_document(document)
        self._apply(values)
        self.lora.set_loras(values.loras)
        self._forms = {k: v for k, v in forms.items() if isinstance(v, dict)} if isinstance(forms, dict) else {}
        self._provider = self._backend()[0] or None
        if self._provider in self._forms:
            self._apply_provider_form(self._forms[self._provider])
        elif forms is None and self._provider is not None:
            self._forms[self._provider] = self._provider_form()  # a pre-per-provider file belongs to its provider

    def _snap_size(self) -> str | None:
        """Show the nearest multiple of 64 in the size boxes; returns a note when a value was adjusted."""
        page = self.page
        old = (page.widthSpinBox.value(), page.heightSpinBox.value())
        new = tuple(snap_dimension(value) for value in old)
        if new == old:
            return None
        page.widthSpinBox.setValue(new[0])
        page.heightSpinBox.setValue(new[1])
        log.info("size adjusted from %dx%d to %dx%d", *old, *new)
        return f"Size adjusted from {old[0]}x{old[1]} to {new[0]}x{new[1]} (multiples of 64)."

    def _allow_local_bridge_model(self, request):
        """Mark an unlisted checkpoint as an explicit Local -> DT+ bridge choice."""
        enabled = self._backend()[0] == "local" and self.page.includeCloudCheck.isChecked()
        return replace(request, allow_unlisted_model=enabled)

    def _request_for_check(self):
        """A request for LoRA preflight only: a blank prompt gets a placeholder because nothing is dispatched."""
        self._mark(None)
        name, _ = self._backend()
        values = self._values()
        try:
            request = build_request(replace(values, prompt=values.prompt or "preflight check"), backend=name)
            return self._allow_local_bridge_model(request)
        except FormError as exc:
            self._mark(exc.widget)
            self.lora._show(self.page.compatibilityStatusLabel, Presentation("error", "Cannot check", str(exc)))
            return None

    def _with_reuse_lineage(self, request):
        if not self._reuse_source_identity:
            return request
        return replace(
            request,
            resolution_evidence=(*request.resolution_evidence, ("source.output", self._reuse_source_identity)),
        )

    @staticmethod
    def _saved_request_values(document: dict) -> FormValues:
        return form_from_document(
            {
                "prompt": document.get("prompt", ""),
                "negative_prompt": document.get("negative_prompt", ""),
                "model": document.get("model_id", ""),
                "seed": str(document.get("seed", 0)),
                "width": document.get("width_px", 512),
                "height": document.get("height_px", 512),
                "steps": document.get("steps", 20),
                "guidance": float(document.get("guidance", 7.0)),
                "sampler": document.get("sampler_id", "euler_a"),
                "loras": document.get("loras", []),
            }
        )

    def reuse_saved_request(self, document: dict, source_identity: str) -> None:
        """Populate Generate from one recorded request without dispatching."""
        values = self._saved_request_values(document)
        self._apply(values)
        self.lora.set_loras(values.loras, loaded=True)
        self._reuse_source_identity = source_identity
        self._config_name = None
        self._status(
            Presentation(
                "ok",
                "Saved settings loaded",
                "Generate was populated from the selected output. Nothing was submitted; availability is revalidated on use.",
            )
        )

    def regenerate_saved_request(self, document: dict, source_identity: str) -> bool:
        """Validate recorded settings before replacing the form, then submit them as a fresh request."""
        if self._busy or self._activity.dispatching:
            return False
        values = self._saved_request_values(document)
        name, _ = self._backend()
        try:
            build_request(values, backend=name, random_seed=True)
        except FormError as exc:
            self._mark(exc.widget)
            self._status(Presentation("error", "Cannot regenerate", str(exc)))
            return False
        self.reuse_saved_request(document, source_identity)
        self.generate()
        return True

    # -- presentation ----------------------------------------------------------------------------------------------
    def _align_rail_labels(self) -> None:
        """Give every rail group the same label column and gap, so inputs and selects share one left edge."""
        labels = [getattr(self.page, name) for name in RAIL_LABELS]
        width = max(label.sizeHint().width() for label in labels)
        for label in labels:
            label.setMinimumWidth(width)
        for name in ("backendSummaryForm", "simulationForm"):
            getattr(self.page, name).setHorizontalSpacing(self.page.parametersGrid.horizontalSpacing())

    def _status(self, presentation: Presentation) -> None:
        label = self.page.generationStatusLabel
        label.setProperty("state", presentation.level)
        label.style().unpolish(label)
        label.style().polish(label)
        label.setText(f"{presentation.headline}. {presentation.detail}".strip())
        label.setToolTip("")

    def _mark(self, widget_name: str | None) -> None:
        for name in FIELD_WIDGETS:
            widget = getattr(self.page, name)
            widget.setProperty("invalid", name == widget_name)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            log.debug("generate page busy=%s", busy)
        self._busy = busy
        self.page.generateButton.setEnabled(not busy and not self._activity.dispatching)
        self.page.refreshInventoryButton.setEnabled(not busy)
        self.lora.set_enabled(not busy)

    def _unlisten_activity(self) -> None:
        """Detach before Qt destroys the page and its generation controls."""
        self._activity.unlisten(self._sync_activity)

    def _sync_activity(self) -> None:
        """A queue dispatch holds the state lock, so single generation waits for it (and vice versa)."""
        self.page.generateButton.setEnabled(not self._busy and not self._activity.dispatching)
        if self._activity.dispatching:
            self._status(
                Presentation("warning", "Queue dispatch is running", "Single generation is unavailable until it stops.")
            )

    def queue_created(self) -> None:
        self._previewed_seed = None

    def request_for_queue(self, *, preview: bool = False):
        """The validated request, backend and bound receipts for a new queue; raises ``FormError`` after marking.

        A random seed (form seed 0) picked for a plan preview is kept until a queue is created (``queue_created``),
        so the dry-run table shows the seeds the queue will really use, even after a rejected attempt.
        """
        self._mark(None)
        self._snap_size()
        name, endpoint = self._backend()
        try:
            request = build_request(self._values(), backend=name, config_name=self._config_name, random_seed=True)
            request = self._with_reuse_lineage(self._allow_local_bridge_model(request))
        except FormError as exc:
            self._mark(exc.widget)
            raise
        resolved = any(key == "seed.resolved" for key, _ in request.resolution_evidence)
        if preview and resolved:
            self._previewed_seed = request.seed
        elif resolved and self._previewed_seed is not None:
            request = replace(request, seed=self._previewed_seed)
        if not preview:
            self._reuse_source_identity = None
        return request, name, endpoint, self.lora.receipt_ids()

    def _present_progress(self, stage: str, completed: int | None = None, total: int | None = None) -> None:
        """Keep the last observed sampler step across stage-only events; never synthesize progress."""
        if not self._generating:
            return
        reported = type(completed) is int and type(total) is int and total > 0 and 0 <= completed <= total
        if not reported:
            current = self._activity.run_state
            if current.owner == "generation" and current.phase == "running":
                completed, total = current.completed, current.total
            else:
                completed, total = None, None
        detail = stage
        if completed is not None and total is not None:
            qualifier = "step" if reported else "last reported step"
            detail = f"{detail} · {qualifier} {completed}/{total}"
        self._activity.present_run(
            RunState(
                owner="generation",
                phase="running",
                completed=completed,
                total=total,
                detail=detail + ". Pause is not supported for single generation.",
            )
        )

    def _show_image(self, encoded: bytes, designation: str, backend_id: str | None = None) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(encoded):
            return
        label = self.page.resultPreviewLabel
        label.setPixmap(pixmap)
        label.set_designation(
            designation, fixture=(backend_id or self._submitted_backend or self._backend()[0]) == "fake"
        )

    # -- generation ------------------------------------------------------------------------------------------------
    def generate(self) -> None:
        if self._busy:
            return
        self._mark(None)
        note = self._snap_size()
        name, endpoint = self._backend()
        self._submitted_backend = name
        try:
            request = build_request(self._values(), backend=name, config_name=self._config_name, random_seed=True)
            request = self._with_reuse_lineage(self._allow_local_bridge_model(request))
        except FormError as exc:
            self._mark(exc.widget)
            log.info("generate rejected backend=%s field=%s", name, exc.widget or "-")
            log.debug("generate validation message=%s", exc)
            self._status(Presentation("error", "Invalid request", str(exc)))
            return
        self._reuse_source_identity = None
        self._request = request
        log.info("generate submitted backend=%s", name)
        log.debug(
            "generate request model=%s size=%dx%d steps=%d sampler=%s seed=%s loras=%s",
            request.model_id,
            request.width_px,
            request.height_px,
            request.steps,
            request.sampler_id.value,
            request.seed,
            [f"{item.identifier}@{item.weight}" for item in request.loras],
        )
        self.page.effectiveRequestBrowser.setPlainText(describe_request(request, name, endpoint))
        self.page.resultList.clear()
        self._set_result_actions(False)
        self.page.attemptIdentityLabel.setText("No durable attempt yet.")
        self.page.attemptIdentityLabel.setToolTip("")
        self.page.resultPreviewLabel.clear()
        self.page.resultPreviewLabel.setProperty("designation", "")
        self.page.previewHost.setMinimumHeight(PREVIEW_MIN_HEIGHT)
        self._status(Presentation("warning", "Generating", f"Waiting on the selected backend. {note or ''}".strip()))
        self._generating = True
        self._activity.set(
            generating=True,
            run_state=RunState(
                owner="generation",
                phase="running",
                detail="Preparing generation. Pause is not supported for single generation.",
            ),
        )
        self._set_busy(True)
        self._future = self._runner.submit(
            self._generate(
                name,
                endpoint,
                request,
                self._saved(),
                self.lora.receipt_ids(),
                self.page.triggerPrefixCheck.isChecked(),
                self.simulation(),
            ),
            self._generated,
        )

    def simulation(self) -> dict:
        """The fake provider's simulation settings from the form; empty for every other provider."""
        page = self.page
        if self._backend()[0] != "fake":
            return {}
        return {
            "scenario": page.simulationScenarioCombo.currentData(),
            "delay": page.simulationDelaySpinBox.value(),
            "previews": page.simulationPreviewsCheck.isChecked(),
        }

    async def _generate(self, name, endpoint, request, saved, asset_ids, prefix, simulation):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state, **simulation)
        return await generate_once(
            selection, self._state, request, prefix=prefix, saved=saved, on_event=self._relay, asset_ids=asset_ids
        )

    def _event(self, event: BackendEvent) -> None:
        if not self._busy:
            return  # a late event after completion never changes the settled view
        if event.kind == "progress":
            self._present_progress(event.stage or "Generating", event.completed, event.total)
        elif event.kind == "preview" and event.artifact and event.artifact.designation == Designation.PREVIEW:
            self._show_image(event.artifact.encoded, "preview")
            self._present_progress("Preview shown (not the final image)")
        elif event.kind in {"preparing", "submission_possible", "terminal"}:
            stage = {
                "preparing": "Preparing",
                "submission_possible": "Submitting to backend",
                "terminal": "Finalizing",
            }[event.kind]
            self._present_progress(event.stage or stage)

    def _generated(self, outcome, failure) -> None:
        self._cache_changed()
        if isinstance(failure, asyncio.CancelledError):
            self._status(Presentation("warning", "Local stop requested", "Reading the recorded evidence."))
            request_id = self._request.request_id
            self._runner.submit(read_attempt(self._state, request_id), self._interrupted)
            return
        self._generating = False
        self._activity.set(generating=False, run_state=RunState())
        self._set_busy(False)
        if failure is not None:
            log.warning("generate failed reason=%s", failure_reason(failure))
            presentation = present_generation_failure(failure)
            self._status(presentation)
            activity_strip(self.page).complete("generation-result", presentation.headline, presentation.detail, "error")
            if dev_enabled():
                self._show_failure_details(failure)
            return
        log.info("generate saved backend=%s outputs=%d", outcome.backend_id, len(outcome.output_paths))
        presentation = present_outcome(outcome)
        self._status(presentation)
        activity_strip(self.page).complete(
            "generation-result",
            "Generation " + presentation.headline,
            presentation.detail,
            "partial" if outcome.warnings else "success",
        )
        self.lora.show_diagnostics(outcome.diagnostics)
        self.page.attemptIdentityLabel.setText(f"Attempt {outcome.attempt_id[:8]} on {outcome.backend_id}.")
        self.page.attemptIdentityLabel.setToolTip(f"Attempt {outcome.attempt_id}")
        self.page.effectiveRequestBrowser.setPlainText(
            describe_request(outcome.request, outcome.backend_id, self._backend()[1])
        )
        for label, path in (*(("Output", p) for p in outcome.output_paths), ("Metadata", outcome.metadata_path)):
            item = QListWidgetItem(f"{label}: {path}")
            item.setData(RESULT_PATH_ROLE, str(path))
            self.page.resultList.addItem(item)
        self._saved_output_identities = {
            str(path): f"{outcome.attempt_id}:{index}" for index, path in enumerate(outcome.output_paths)
        }
        self._set_result_actions(True)
        self._metadata_path = Path(outcome.metadata_path)
        if len(outcome.output_paths) > 1:
            self.page.detailsToggle.setChecked(True)  # the output chooser lives in the details
        if outcome.final_image:
            self._show_image(outcome.final_image, "final", outcome.backend_id)

    def _show_failure_details(self, failure: BaseException) -> None:
        """Development mode only: keep the request and the backend's failure evidence in the Attempt details box."""
        backend, endpoint = self._backend()
        self.page.attemptIdentityLabel.setText("Attempt failed (development mode details below).")
        self.page.effectiveRequestBrowser.setPlainText(
            describe_request(self._request, backend, endpoint) + "\n\nfailure:\n" + describe_failure(failure)
        )
        self.page.detailsToggle.setChecked(True)

    # -- saved results ---------------------------------------------------------------------------------------------
    def _set_result_actions(self, available: bool) -> None:
        self.page.openOutputFolderButton.setEnabled(available)
        self.page.copyOutputPathButton.setEnabled(available)
        self.page.resultList.setEnabled(available)
        self.openFileButton.setEnabled(available)
        self.metadataButton.setEnabled(available)
        if not available:
            self._metadata_path = None
            self._saved_output_identities.clear()

    def export_identity(self) -> str | None:
        """Selected committed output only; metadata/preview selections are not images."""
        return self._saved_output_identities.get(self._result_path())

    def open_output_file(self) -> None:
        path = self._result_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_metadata(self) -> None:
        if self._metadata_path is None:
            return
        # Explicit inspection of a local artifact, not a provider call.
        try:
            with self._metadata_path.open(encoding="utf-8") as handle:
                content = handle.read(METADATA_LIMIT + 1)
        except (OSError, UnicodeError):
            InlineNotice.error(self.page, "Metadata unavailable", "The saved metadata file could not be read.")
            return
        if len(content) > METADATA_LIMIT:
            content = content[:METADATA_LIMIT] + "\n… (truncated: the saved file is longer)"
        InlineNotice.information(self.page, "Saved generation metadata", content)

    def _result_path(self) -> str | None:
        """The selected saved path, else the first one (the first output)."""
        results = self.page.resultList
        item = results.currentItem() or (results.item(0) if results.count() else None)
        return item.data(RESULT_PATH_ROLE) if item is not None else None

    def open_output_folder(self) -> None:
        path = self._result_path()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def copy_output_path(self) -> None:
        path = self._result_path()
        if path:
            QGuiApplication.clipboard().setText(path)

    def cancel(self) -> None:
        """Stop waiting locally through the shared interruption path; the server is never claimed cancelled."""
        if self._future is not None and self._generating:
            self._future.cancel()

    def _interrupted(self, evidence, failure) -> None:
        self._generating = False
        self._activity.set(generating=False, run_state=RunState())
        self._set_busy(False)
        presentation = present_generation_failure(failure) if failure else present_interruption(evidence)
        self._status(presentation)
        activity_strip(self.page).complete(
            "generation-result",
            presentation.headline,
            presentation.detail,
            "error" if presentation.level == "error" else "success" if presentation.level == "ok" else "stopped",
        )
        if failure is not None:
            return
        if evidence is not None:
            self.page.attemptIdentityLabel.setText(
                f"Attempt {evidence.attempt_id[:8]}: recorded state {evidence.state}."
            )
            self.page.attemptIdentityLabel.setToolTip(f"Attempt {evidence.attempt_id}")

    # -- inventory -------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if self._busy:
            return
        name, endpoint = self._backend()
        self._set_busy(True)
        self._inventory_notice = "Refreshing inventory…"
        self._inventory_error = ""
        self._write_inventory_stats()
        log.info("inventory refresh requested backend=%s", name)
        source, revision = self._current_inventory_source(), self._inventory_revision
        self._runner.submit(
            self._refresh(name, endpoint, self._saved()),
            lambda snapshot, failure: self._refresh_completed(source, revision, snapshot, failure),
        )

    def _refresh_completed(self, source, revision, snapshot, failure):
        self._cache_changed()
        if source != self._current_inventory_source() or revision != self._inventory_revision:
            self._set_busy(False)
            if self._inventory_notice == "Refreshing inventory…":
                self._inventory_notice = ""
                self._write_inventory_stats()
            return
        self._refreshed(snapshot, failure)

    async def _refresh(self, name, endpoint, saved):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state)
        return await self._inventory_memory.refresh(selection, self._state, saved=saved, no_cache=self._no_cache)

    def _fill_models(self, ids) -> None:
        """Replace the drop-down entries, keeping whatever is typed (the box stays editable for unlisted models)."""
        combo = self.page.modelCombo
        current = combo.currentText()
        combo.clear()
        combo.addItems(ids)
        combo.setCurrentText(current)

    def _catalogue_domain(self) -> str:
        return self.page.generationDomainCombo.currentData() or "all"

    def _catalogue_filter_changed(self, *_args) -> None:
        if not self._updating_catalogue:
            self._rebuild_catalogue(preferred_checkpoint=self.page.modelCombo.currentText())

    def _catalogue_family_changed(self, *_args) -> None:
        if not self._updating_catalogue:
            self._rebuild_checkpoints(preferred_checkpoint=self.page.modelCombo.currentText())

    def _catalogue_checkpoint_changed(self, *_args) -> None:
        if not self._updating_catalogue:
            self._filter_loras()
        self._filter_samplers()

    def _rebuild_catalogue(
        self,
        *,
        preferred_family: str | None = None,
        preferred_checkpoint: str | None = None,
        restore_niche: bool = False,
        preserve_selection: bool = False,
    ) -> None:
        page = self.page
        preferred_entry = self._catalogue.checkpoint(preferred_checkpoint or "") if preferred_checkpoint else None
        if (
            restore_niche
            and preferred_entry is not None
            and preferred_entry.tier == "niche"
            and not page.includeNicheCheck.isChecked()
        ):
            self._updating_catalogue = True
            try:
                page.includeNicheCheck.setChecked(True)
            finally:
                self._updating_catalogue = False
        current_family = preferred_family or page.baseModelCombo.currentData()
        include_cloud = self._backend()[0] == "local" and page.includeCloudCheck.isChecked()
        families = self._catalogue.families(
            domain=self._catalogue_domain(),
            include_niche=page.includeNicheCheck.isChecked(),
            include_unavailable=include_cloud,
        )
        self._updating_catalogue = True
        try:
            page.baseModelCombo.clear()
            for family, label in families:
                page.baseModelCombo.addItem(label, family)
            family_index = page.baseModelCombo.findData(current_family)
            if family_index < 0 and preferred_checkpoint:
                entry = self._catalogue.checkpoint(preferred_checkpoint)
                if entry is not None:
                    family_index = page.baseModelCombo.findData(entry.base_model)
            page.baseModelCombo.setCurrentIndex(family_index if family_index >= 0 else (0 if families else -1))
        finally:
            self._updating_catalogue = False
        self._rebuild_checkpoints(preferred_checkpoint=preferred_checkpoint, preserve_selection=preserve_selection)
        unknown = self._catalogue.view().unclassified_checkpoints
        page.unclassifiedModelsButton.setVisible(bool(unknown))
        page.unclassifiedModelsButton.setText(
            f"{len(unknown)} unclassified checkpoint" + ("" if len(unknown) == 1 else "s") + " found"
        )

    def _rebuild_checkpoints(
        self, *, preferred_checkpoint: str | None = None, preserve_selection: bool = False
    ) -> None:
        page = self.page
        current = preferred_checkpoint if preferred_checkpoint is not None else page.modelCombo.currentText()
        family = page.baseModelCombo.currentData()
        include_cloud = self._backend()[0] == "local" and page.includeCloudCheck.isChecked()
        entries = self._catalogue.checkpoints(
            domain=self._catalogue_domain(),
            base_model=family if isinstance(family, str) else None,
            include_niche=page.includeNicheCheck.isChecked(),
            include_unavailable=include_cloud,
        )
        allowed = {entry.identifier for entry in entries}
        unclassified = set(self._catalogue.view().unclassified_checkpoints)
        selectable = [entry.identifier for entry in entries]
        selectable.extend(identifier for identifier in sorted(unclassified) if identifier not in allowed)
        self._updating_catalogue = True
        try:
            self._fill_models(selectable)
            # Preserve an explicit raw identifier even when provider discovery is incomplete (for example,
            # a local Draw Things endpoint bridging to DT+). Known classified checkpoints still obey the filters.
            if (
                preserve_selection
                or self._inventory_snapshot is None
                or current in allowed
                or current in unclassified
                or (current and self._catalogue.checkpoint(current) is None)
            ):
                page.modelCombo.setCurrentText(current)
            else:
                page.modelCombo.setCurrentText("")
        finally:
            self._updating_catalogue = False
        self._filter_loras(prune_selected=not preserve_selection and self._inventory_snapshot is not None)

    def reload_custom_loras(self) -> None:
        """Re-resolve LoRA candidates after user-owned catalogue metadata changes."""
        self._filter_loras()

    def _filter_loras(self, *, prune_selected: bool = True) -> None:
        checkpoint = self.page.modelCombo.currentText().strip()
        include_cloud = self._backend()[0] == "local" and self.page.includeCloudCheck.isChecked()
        model = self._catalogue.checkpoint(checkpoint, include_unavailable=include_cloud)
        entries = (
            self._catalogue.compatible_loras(
                checkpoint, domain=self._catalogue_domain(), include_unavailable=include_cloud
            )
            if model is not None and model.base_model is not None
            else None
        )
        self.lora.set_candidates(entries, prune_selected=prune_selected and entries is not None)

    def _show_unclassified_models(self) -> None:
        unknown = self._catalogue.view().unclassified_checkpoints
        if not unknown:
            return
        InlineNotice.information(
            self.page,
            "Unclassified checkpoints",
            "These checkpoints are available on the active provider but are not classified by the DTFlow catalogue. "
            "They remain usable through the Checkpoint selector's custom-identifier action. "
            "DTFlow will not guess their family, generation type, quantization or compatible LoRAs.\n\n"
            + "\n".join(unknown),
        )

    def _show_inventory(self, snapshot: InventorySnapshot, how: str) -> None:
        self.lora.set_inventory(snapshot)
        self._inventory_snapshot = snapshot
        self._catalogue.set_inventory(snapshot)
        self._inventory_how = how
        if self._inventory_error:
            self._inventory_notice = "Refresh failed · showing previous inventory"
        self._rebuild_catalogue(
            preferred_family=self._pending_family,
            preferred_checkpoint=self.page.modelCombo.currentText(),
            restore_niche=True,
            preserve_selection=True,
        )
        self._pending_family = None
        self._write_inventory_stats()

    def _write_inventory_stats(self):
        label = self.page.inventoryEvidenceLabel
        snapshot = self._inventory_snapshot
        if snapshot is None:
            label.setText(self._inventory_notice or "Inventory has not been observed. Refresh to discover models.")
            label.setToolTip(self._inventory_error)
            return
        models = len(eligible_model_ids(snapshot))
        loras = sum(entry.kind == "lora" for entry in snapshot.entries)
        try:
            observed = datetime.fromisoformat(snapshot.observed_at_utc).astimezone(UTC).strftime("%d %b %H:%M UTC")
        except ValueError:
            observed = snapshot.observed_at_utc
        lines = [f"{models} model(s) · {loras} LoRA(s)", f"{self._inventory_how} · observed {observed}"]
        if self._inventory_notice:
            lines.append(self._inventory_notice)
        label.setText("\n".join(lines))
        label.setToolTip(
            f"{snapshot.backend_id} inventory {self._inventory_how}; observed {snapshot.observed_at_utc}.\n"
            "Last successful observation only; generation still validates live.\n" + self._inventory_error
        )

    def _refreshed(self, snapshot, failure) -> None:
        self._set_busy(False)
        if failure is not None:
            shown = present_generation_failure(failure)
            log.warning("inventory refresh failed reason=%s", failure_reason(failure))
            self._inventory_notice = (
                "Refresh failed · showing previous inventory"
                if self._inventory_snapshot
                else "Inventory refresh failed."
            )
            self._inventory_error = f"{shown.headline}: {shown.detail}"
            self._write_inventory_stats()
            return
        self._cache_request = None  # an older disk read must not replace this live observation
        self._inventory_notice = self._inventory_error = ""
        self._show_inventory(snapshot, "refreshed" if snapshot.refreshed else "as reported")

    def request_cached(self, *, revalidate: bool = False) -> None:
        """Restore only matching last-successful evidence, including on return to a provider."""
        name, endpoint = self._backend()
        if not name:
            return
        token = object()
        self._cache_request = token
        source, revision = self._current_inventory_source(), self._inventory_revision
        if self._inventory_snapshot is None and not self._inventory_notice:
            self._inventory_notice = "Loading saved inventory…"
            self._write_inventory_stats()
        self._runner.submit(
            self._cached(name, endpoint, self._saved()),
            lambda result, failure: self._inventory_restored(source, revision, token, result, revalidate),
        )

    async def _cached(self, name, endpoint, saved):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state)
        return await self._inventory_memory.restore(selection, self._state, saved=saved, no_cache=self._no_cache)

    def _inventory_restored(self, source, revision, token, result, revalidate):
        if (
            token is not self._cache_request
            or source != self._current_inventory_source()
            or revision != self._inventory_revision
        ):
            return
        self._cache_request = None
        if self._inventory_notice == "Loading saved inventory…":
            self._inventory_notice = ""
        if result is None:
            if revalidate:
                self._inventory_notice = self._inventory_error = ""
                self._pending_family = self.page.baseModelCombo.currentData() or self._pending_family
                self._clear_inventory()
                self._rebuild_catalogue(
                    preferred_checkpoint=self.page.modelCombo.currentText(), preserve_selection=True
                )
            self._write_inventory_stats()
            return
        snapshot, how = result
        if snapshot is self._inventory_snapshot:
            self._write_inventory_stats()
        else:
            if revalidate:
                self._inventory_notice = self._inventory_error = ""
            self._show_inventory(snapshot, how)

    def credentials_changed(self):
        """Recheck the selected credential identity; saving the same key must not erase evidence."""
        if self._backend()[0] == "cloud":
            self._inventory_revision += 1
            self.request_cached(revalidate=True)

    # -- configuration ---------------------------------------------------------------------------------------------
    def save(self) -> None:
        self._mark(None)
        self._snap_size()
        name, endpoint = self._backend()
        try:
            request = build_request(self._values(), backend=name, config_name=self._config_name)
        except FormError as exc:
            self._mark(exc.widget)
            self._status(Presentation("error", "Configuration not saved", str(exc)))
            return
        chosen = self._choose_save(self.page, self._configs)
        if not chosen:
            return
        self.page.effectiveRequestBrowser.setPlainText(describe_request(request, name, endpoint))
        self._runner.submit(
            save_configuration(Path(chosen), request), lambda _, failure: self._saved_config(chosen, failure)
        )

    def _saved_config(self, chosen: str, failure) -> None:
        if failure is not None:
            self._status(Presentation("error", "Configuration not saved", type(failure).__name__))
            return
        note = "Credential-free; prepared cloud references are never exported."
        self._status(Presentation("ok", "Configuration saved", f"{chosen}. {note}"))
        self._scan_configs()

    def load(self) -> None:
        chosen = self._choose_open(self.page, self._configs)
        if chosen:
            self._load(Path(chosen))

    def _config_chosen(self, index: int) -> None:
        data = self.page.configurationCombo.itemData(index)
        if not data:
            return
        if isinstance(data, tuple) and data[0] == "preset":
            self._load_preset(data[1])
        else:
            self._load(Path(data))

    def _load_preset(self, preset: dict) -> None:
        application = apply_preset(preset)
        current = form_to_document(self._values())
        current.update(application.values)
        values = form_from_document(current)
        self._apply(values)
        if "loras" in application.values:
            self.lora.set_loras(application.values["loras"], loaded=True)
        self._config_name = f"preset:{preset['name']}"
        self._reuse_source_identity = None
        # A native preset can carry dozens of unsupported fields; keep the status short and list them in the tooltip.
        count = len(application.preserved)
        kept = (
            f"{count} native field{'s' if count != 1 else ''} kept but not applied" if count else "no fields kept aside"
        )
        self._status(
            Presentation(
                "ok",
                "Preset loaded",
                f"{preset['name']} applied without dispatch; {kept}. Prompts were unchanged.",
            )
        )
        if count:
            self.page.generationStatusLabel.setToolTip("Preserved-only fields: " + ", ".join(application.preserved))

    def save_preset(self) -> None:
        def save(name):
            document = form_to_document(self._values())
            try:
                save_preset(self._state, name, document)
            except (ValidationError, OSError, ValueError) as exc:
                self._status(Presentation("error", "Preset not saved", str(exc)))
                return
            self._status(
                Presentation("ok", "Preset saved", f"{name.strip()} is available in the Draw Things preset library.")
            )
            self._scan_configs()

        edit_text(self.page, "Save preset", "Preset name", save)

    def refresh_presets(self) -> None:
        self._scan_configs()

    def _load(self, path: Path) -> None:
        self._mark(None)
        name, _ = self._backend()
        self._runner.submit(
            read_configuration(path, backend=name), lambda request, failure: self._loaded(path, request, failure)
        )

    def _loaded(self, path: Path, request, failure) -> None:
        if failure is not None:
            if isinstance(failure, FormError):
                self._mark(failure.widget)
            shown = (
                Presentation("error", "Configuration not loaded", str(failure))
                if isinstance(failure, ValidationError)
                else Presentation("error", "Configuration not loaded", type(failure).__name__)
            )
            self._status(shown)
            return
        self._config_name = path.name
        self._reuse_source_identity = None
        self._apply(values_from_request(request))
        self.lora.set_loras(request.loras, loaded=True)
        name, endpoint = self._backend()
        self.page.effectiveRequestBrowser.setPlainText(describe_request(request, name, endpoint))
        detail = f"{path.name} applied; values come from the file, with defaults only where it is silent."
        if request.loras:
            detail += (
                " It names LoRA(s); local cloud receipts are bound."
                if self.lora.receipt_ids()
                else " It names LoRA(s); no cloud receipt is bound."
            )
        self._status(Presentation("ok", "Configuration loaded", detail))

    def _scan_configs(self) -> None:
        directory = self._configs

        def scan():
            paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
            try:
                presets, preset_failure = load_presets(self._state), None
            except (ValidationError, OSError) as exc:
                presets, preset_failure = (), exc
            return paths, presets, preset_failure

        async def scan_async():
            return await asyncio.to_thread(scan)

        self._runner.submit(scan_async(), self._scanned)

    def _scanned(self, result, failure) -> None:
        combo = self.page.configurationCombo
        combo.clear()
        if failure is not None:
            log.warning("configuration scan failed reason=%s", type(failure).__name__)
            combo.setCurrentIndex(-1)
            return
        paths, presets, preset_failure = result
        if preset_failure is not None:
            log.warning("preset scan failed reason=%s", type(preset_failure).__name__)
        for preset in presets:
            combo.addItem(f"Preset · {preset['name']}", ("preset", preset))
        own = {preset["name"] for preset in presets}
        for preset in builtin_presets():
            if preset["name"] not in own:
                combo.addItem(f"DT · {preset['name']}", ("preset", preset))
        for path in paths:
            combo.addItem(f"Config · {path.name}", str(path))
        combo.setCurrentIndex(-1)

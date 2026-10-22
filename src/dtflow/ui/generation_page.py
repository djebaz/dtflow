"""Generate page controller: widgets in, shared services out. Owns no resolver, backend or persistence rules.

Every service call runs on the shared ``AsyncRunner``; backend events cross to the GUI thread through a coalescing
``EventRelay``. Result wording comes from the shared presentation mapping and from durable evidence, never from a button
press: a local stop is reported as a local stop, and a possibly submitted attempt stays "outcome unknown".
"""

import asyncio
import itertools
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from dtflow.composition import BACKENDS
from dtflow.core.compute_units import ComputeUnitEstimate, estimate_compute_units
from dtflow.core.model_profiles import has_own_profile, profile_for
from dtflow.core.models import (
    BackendEvent,
    CredentialProvider,
    Designation,
    InventorySnapshot,
    ValidationError,
    eligible_model_ids,
)
from dtflow.diagnostics import event as diagnostic_event
from dtflow.logging_setup import dev_enabled
from dtflow.services.catalogue import Catalogue
from dtflow.services.checkpoint_metadata import resolve_checkpoint_metadata
from dtflow.services.connection import (
    Presentation,
    present_generation_failure,
    present_interruption,
    present_outcome,
)
from dtflow.services.image_preparation import prepare_edit_inputs
from dtflow.services.image_quality import assess_file
from dtflow.services.presets import apply_preset, builtin_presets, load_presets, save_preset
from dtflow.services.session import (
    InventoryDisplayMemory,
    generate_image_once,
    generate_once,
    owner_probe_permit,
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
from dtflow.ui.inpaint_editor import EditJob, InpaintEditor
from dtflow.ui.lora_panel import LoraAssetPanel
from dtflow.ui.runner import AsyncRunner, EventRelay

UNCLASSIFIED_FAMILY = "__unclassified__"  # base-model combo data for checkpoints without a catalogue family

log = logging.getLogger(__name__)


def failure_fields(failure: BaseException) -> dict:
    """Stable failure identity plus an allowlisted summary of backend-projected API evidence."""
    fields = {"reason": type(failure).__name__}
    detail = getattr(failure, "failure", None)
    code, category = getattr(detail, "code", None), getattr(detail, "category", None)
    if code:
        fields["code"] = str(code)
        fields["category"] = str(getattr(category, "value", category))
        details = dict(getattr(detail, "diagnostics", ()))
        for key in (
            "transport", "operation", "http_status", "status", "server_message",
            "cause_code", "cause_category", "validation_reason", "asset",
        ):
            if key in details:
                fields[key] = details[key]
    return fields


PREVIEW_MIN_HEIGHT = 180
COMPUTE_UNITS_LIMIT = 40_000  # meter full and red from here up
COMPUTE_UNITS_METER_STEPS = 1000  # meter resolution; the bar's maximum
# (units, fill fraction) anchors of the meter scale; see compute_units_fill().
COMPUTE_UNITS_SCALE = ((100, 0.15), (3_000, 0.5), (COMPUTE_UNITS_LIMIT, 1.0))
CHECKPOINT_STEP_SETTLE_MS = 600  # LoRA pruning waits until previous/next stepping pauses
RESULT_PATH_ROLE = Qt.ItemDataRole.UserRole
DISPATCH_REASON = (
    "A queue is running in this window: Generate adds this request behind it as a one-item queue, "
    "which starts by itself when the running queue ends."
)
SNAPSHOT_NOTE = "Running snapshot: later edits to the form apply only to the next request.\n\n"
METADATA_LIMIT = 256_000
WIRED_CONTROLS = (
    "textToImageModeButton",
    "inpaintModeButton",
    "promptEdit",
    "negativePromptEdit",
    "seedEdit",
    "stepsSpinBox",
    "widthSpinBox",
    "heightSpinBox",
    "guidanceSpinBox",
    "samplerCombo",
    "suggestionLabel",
    "applySuggestionButton",
    "generationDomainCombo",
    "baseModelCombo",
    "modelCombo",
    "includeCloudCheck",
    "includeNicheCheck",
    "unclassifiedModelsButton",
    "checkpointDetailsButton",
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


def compute_units_fill(units: int) -> float:
    """Map an estimate to a 0–1 meter fill on a non-linear, monotonic scale.

    Typical estimates sit in the low thousands, so a linear 0–40 000 bar left them almost empty. The scale is linear from
    0 to 100 (15 %), then logarithmic between anchors: 100 → 15 %, 3 000 → 50 %, 40 000 → 100 %. Anything at or above
    COMPUTE_UNITS_LIMIT is clamped full.
    """
    if units <= 0:
        return 0.0
    first_units, first_fill = COMPUTE_UNITS_SCALE[0]
    if units <= first_units:
        return first_fill * units / first_units
    for (low, low_fill), (high, high_fill) in itertools.pairwise(COMPUTE_UNITS_SCALE):
        if units <= high:
            return low_fill + (high_fill - low_fill) * math.log(units / low) / math.log(high / low)
    return 1.0


def compute_units_meter_value(units: int) -> int:
    """Meter position for an estimate; only COMPUTE_UNITS_LIMIT and above fill the bar."""
    if units >= COMPUTE_UNITS_LIMIT:
        return COMPUTE_UNITS_METER_STEPS
    return min(round(compute_units_fill(units) * COMPUTE_UNITS_METER_STEPS), COMPUTE_UNITS_METER_STEPS - 1)


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
        self._step_settle = QTimer(page)
        self._step_settle.setSingleShot(True)
        self._step_settle.setInterval(CHECKPOINT_STEP_SETTLE_MS)
        self._step_settle.timeout.connect(self._checkpoint_steps_settled)
        self._step_sampler = ""
        self._forms: dict[str, dict] = {}  # provider -> its remembered form fields (prompts are shared, not stored)
        self._provider: str | None = None
        self._previewed_seed: int | None = None
        # Set by the window: queue_behind(request, backend, endpoint, receipts, edit) adds a one-item up-next queue.
        self.queue_behind: Callable | None = None
        self._activity.listen(self._sync_activity)
        page.destroyed.connect(self._unlisten_activity)
        self._choose_save, self._choose_open = choose_save, choose_open
        self._configs = self._state / "configs"
        self._relay = EventRelay(self._event, page)
        self._future = None
        self._generating = False
        self._request = None
        self._stage_started = 0.0
        self._stage_seen: dict[str, float] = {}
        self._busy = False
        self._config_name: str | None = None
        self._reuse_source_identity: str | None = None
        self._metadata_path: Path | None = None
        self._saved_output_identities: dict[str, str] = {}
        self._submitted_backend: str | None = None
        self._submitted_endpoint: str | None = None  # the running request's snapshot, not the form's current value
        self._run_edit = False
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
        page.applySuggestionButton.clicked.connect(self._apply_suggestion)
        page.stepsSpinBox.valueChanged.connect(self._update_suggestion)
        page.guidanceSpinBox.valueChanged.connect(self._update_suggestion)
        page.samplerCombo.currentTextChanged.connect(self._update_suggestion)
        page.includeCloudCheck.toggled.connect(self._catalogue_filter_changed)
        page.includeNicheCheck.toggled.connect(self._catalogue_filter_changed)
        page.modelCombo.currentTextChanged.connect(self._catalogue_checkpoint_changed)
        page.modelCombo.currentTextChanged.connect(self._update_compute_units)
        for control in (page.widthSpinBox, page.heightSpinBox, page.stepsSpinBox, page.guidanceSpinBox):
            control.valueChanged.connect(self._update_compute_units)
        for control in (page.textToImageModeButton, page.inpaintModeButton):
            control.toggled.connect(self._update_compute_units)
        page.generationBackendCombo.currentTextChanged.connect(self._update_compute_units)
        lora_model = page.loraList.model()
        for signal in (lora_model.rowsInserted, lora_model.rowsRemoved, lora_model.modelReset):
            signal.connect(self._update_compute_units)
        page.loraList.itemChanged.connect(self._update_compute_units)
        page.unclassifiedModelsButton.clicked.connect(self._show_unclassified_models)
        page.checkpointDetailsButton.clicked.connect(self.show_checkpoint_details)
        page.previousCheckpointButton.clicked.connect(lambda: self._step_checkpoint(-1))
        page.nextCheckpointButton.clicked.connect(lambda: self._step_checkpoint(1))
        page.modelCombo.currentIndexChanged.connect(self._sync_checkpoint_steppers)
        page.modelCombo.model().rowsInserted.connect(self._sync_checkpoint_steppers)
        page.modelCombo.model().rowsRemoved.connect(self._sync_checkpoint_steppers)
        page.checkpointDetailsButton.setEnabled(bool(page.modelCombo.currentText().strip()))
        for label, scenario in SIMULATED_OUTCOMES:
            page.simulationScenarioCombo.addItem(label, scenario)
        page.generationBackendCombo.addItems(BACKENDS)
        page.generationBackendCombo.setToolTip("Chosen on the Connection page.")
        page.generateButton.clicked.connect(lambda _=False: self.generate())
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
        page.checkpointDetailsButton.setIcon(QIcon(str(icons / "info.svg")))
        page.checkpointDetailsButton.setIconSize(QSize(16, 16))
        page.previousCheckpointButton.setIcon(QIcon(str(icons / "chevron-left.svg")))
        page.nextCheckpointButton.setIcon(QIcon(str(icons / "chevron-right.svg")))
        self._sync_checkpoint_steppers()
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
        self.inpaint = InpaintEditor(page, runner)
        self.inpaint.attach(self)
        page.textToImageModeButton.toggled.connect(self._sync_activity)
        page.inpaintModeButton.toggled.connect(self._sync_activity)
        self._update_compute_units()

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
        if hasattr(self, "inpaint"):
            self.inpaint.refresh_capability()
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
        self._update_suggestion()

    DISTILLED_NAME = re.compile(r"schnell|turbo|lightning|hyper|lcm|distill", re.IGNORECASE)

    def _suggestion(self):
        """The dataset profile for the selected checkpoint, or ``None`` when there is nothing honest to suggest."""
        model = self.page.modelCombo.currentText()
        if not model:
            return None
        entry = self._catalogue.checkpoint(model, include_unavailable=True)
        return profile_for(model, entry.base_model if entry else None)

    def _update_suggestion(self, *_args) -> None:
        """Hint the suggested steps/guidance/sampler; never change the user's values."""
        page = self.page
        model = page.modelCombo.currentText()
        profile = self._suggestion()
        visible = profile is not None
        page.suggestionLabel.setVisible(visible)
        page.applySuggestionButton.setVisible(visible)
        if profile is None:
            return
        distilled = not has_own_profile(model) and bool(self.DISTILLED_NAME.search(model))
        basis = profile.name if has_own_profile(model) else f"{profile.name} family default"
        text = f"Suggested ({basis}): {profile.steps} steps · guidance {profile.guidance:g} · {profile.sampler.value}."
        if distilled:
            text += " The name suggests a distilled model, which usually needs fewer steps and low guidance; verify."
        elif not has_own_profile(model):
            text += " Merged checkpoints may differ."
        page.suggestionLabel.setText(text)

    def _apply_suggestion(self) -> None:
        profile = self._suggestion()
        if profile is None:
            return
        page = self.page
        page.stepsSpinBox.setValue(profile.steps)
        page.guidanceSpinBox.setValue(float(profile.guidance))
        if page.samplerCombo.findText(profile.sampler.value) >= 0:
            page.samplerCombo.setCurrentText(profile.sampler.value)
        self._update_suggestion()

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
            diagnostic_event(
                log,
                "generation.provider_switched",
                level=logging.INFO,
                data={
                    "provider": name,
                    "remembered": name in self._forms,
                },
            )

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
        diagnostic_event(
            log,
            "generation.size_adjusted",
            level=logging.INFO,
            data={
                "old_width_px": old[0],
                "old_height_px": old[1],
                "width_px": new[0],
                "height_px": new[1],
            },
        )
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

    def reuse_saved_request(self, document: dict, source_identity: str, *, origin: str = "the selected output") -> None:
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
                f"Generate was populated from {origin}. Nothing was submitted; availability is revalidated on use.",
            )
        )

    def regenerate_saved_request(self, document: dict, source_identity: str) -> bool:
        """Validate recorded settings before replacing the form, then submit them as a fresh request."""
        if self._busy or self._generating or self._activity.dispatching:
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
        """A page service call (inventory refresh, LoRA check, Prepare or reconcile) is in flight; these share one slot.

        A running generation never locks the form: its request is an immutable snapshot taken at Generate, so edits only
        shape the next request (issue #133).
        """
        if not busy and getattr(getattr(self, "lora", None), "preparing", False):
            return  # unrelated inventory completions cannot release an admitted Prepare session
        if busy != self._busy:
            diagnostic_event(
                log,
                "generation.page_state",
                level=logging.DEBUG,
                data={
                    "busy": busy,
                },
            )
        self._busy = busy
        self.page.refreshInventoryButton.setEnabled(not busy)
        self.lora.set_enabled(not busy)
        self._sync_generate()

    def _sync_generate(self) -> None:
        """Only starting another run waits, for this generation or a page service call. While this window dispatches a
        queue, Generate stays available and queues the request behind it (``queue_behind``)."""
        dispatching = self._activity.dispatching
        button = self.page.generateButton
        blocked = self._busy or self._generating
        button.setEnabled(not blocked)
        # The Queue dropdown stays open during a generation: its Add to queue waits behind it (#135).
        self.page.queueGenerateButton.setEnabled(not self._busy)
        if hasattr(self, "inpaint"):
            self.inpaint.set_busy(blocked)  # Inpaint keeps its editor open and only gates Edit image
        if dispatching:
            button.setToolTip(DISPATCH_REASON)
        elif button.toolTip() == DISPATCH_REASON:
            button.setToolTip("")

    def _unlisten_activity(self) -> None:
        """Detach before Qt destroys the page and its generation controls."""
        self._activity.unlisten(self._sync_activity)

    def _sync_activity(self) -> None:
        """A queue dispatch holds the state lock, so Generate queues behind it; every input stays editable."""
        self._sync_generate()

    def queue_created(self) -> None:
        self._previewed_seed = None

    def plan_choices(self) -> dict:
        """What the Queues composer offers: the listed checkpoints, every sampler, the current size and the LoRAs."""
        page = self.page
        return {
            "models": [page.modelCombo.itemText(index) for index in range(page.modelCombo.count())],
            "samplers": list(SAMPLERS),  # every sampler: a plan may cross several models
            "size": f"{page.widthSpinBox.value()}x{page.heightSpinBox.value()}",
            "prompt": page.promptEdit.toPlainText(),  # the template of a prompt variation
            "loras": dict(self.lora.all_choices()),  # identifier -> catalogue weight; unfiltered, models may differ
            "receipts": self.lora.confirmed_receipts(),  # bound for LoRA sets that name a custom cloud LoRA
        }

    def queue_edit_job(self):
        """The current Inpaint edit for the Queues tab, or None outside Inpaint mode."""
        return self.inpaint.queue_job() if self.inpaint.active else None

    def request_for_queue(self, *, preview: bool = False):
        """The validated request, backend and bound receipts for a new queue; raises ``FormError`` after marking.

        A random seed (form seed 0) is drawn once and kept until a queue is created (``queue_created``) or a seed is
        chosen, so repeated previews and the creation use the seeds the dry-run table shows, even after a rejection.
        """
        if self.inpaint.active and self.inpaint.queue_job() is None:
            raise FormError("Choose a source image and paint or select the area to change first.", None)
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
        if not resolved:
            self._previewed_seed = None  # a chosen seed ends the random one
        elif self._previewed_seed is None:
            self._previewed_seed = request.seed
        else:
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
                detail=detail,
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
    def edit_saved_image(self, path: str) -> bool:
        """Images handoff: open a saved output as the Inpaint source; nothing is submitted, so a running generation or
        queue keeps its own snapshot and is never affected."""
        self.inpaint.edit_saved_image(path)
        return True

    def generate(self, edit: EditJob | None = None) -> bool:
        """Start one single run; returns whether it started. Refused while another run or service call is active."""
        if self.inpaint.active and edit is None:
            self.inpaint.generate()
            return False
        if self._busy or self._generating:
            return False
        if self._activity.dispatching:
            self._queue_behind(edit)
            return False
        self._mark(None)
        note = self._snap_size()
        name, endpoint = self._backend()
        self._submitted_backend, self._submitted_endpoint = name, endpoint
        try:
            request = build_request(self._values(), backend=name, config_name=self._config_name, random_seed=True)
            request = self._with_reuse_lineage(self._allow_local_bridge_model(request))
        except FormError as exc:
            self._mark(exc.widget)
            diagnostic_event(
                log,
                "generation.rejected",
                level=logging.INFO,
                data={
                    "backend": name,
                    "field": exc.widget or "-",
                },
            )
            diagnostic_event(
                log,
                "generation.validation_failed",
                level=logging.DEBUG,
                data={
                    "reason": type(exc).__name__,
                },
            )
            self._status(Presentation("error", "Invalid request", str(exc)))
            return False
        if edit is not None and request.batch_size * request.batch_count != 1:
            self._status(Presentation("error", "Invalid request", "Edit image runs one image at a time."))
            return False
        self._reuse_source_identity = None
        self._request = request
        self._run_edit = edit is not None
        diagnostic_event(
            log,
            "generation.submitted",
            level=logging.INFO,
            data={
                "backend": name,
                "operation": "inpaint" if edit else "txt2img",
            },
        )
        diagnostic_event(
            log,
            "generation.request_validated",
            level=logging.DEBUG,
            data={
                "model_id": request.model_id,
                "width_px": request.width_px,
                "height_px": request.height_px,
                "steps": request.steps,
                "sampler_id": request.sampler_id.value,
                "seed": request.seed,
                "lora_count": len(request.loras),
            },
        )
        self.page.effectiveRequestBrowser.setPlainText(SNAPSHOT_NOTE + describe_request(request, name, endpoint))
        self.page.resultList.clear()
        self._set_result_actions(False)
        self.page.attemptIdentityLabel.setText("No durable attempt yet.")
        self.page.attemptIdentityLabel.setToolTip("")
        self.page.resultPreviewLabel.clear()
        self.page.resultPreviewLabel.setProperty("designation", "")
        self.page.previewHost.setMinimumHeight(PREVIEW_MIN_HEIGHT)
        self._status(Presentation("warning", "Generating", f"Waiting on the selected backend. {note or ''}".strip()))
        self._stage_started = time.monotonic()
        self._stage_seen = {}
        self._generating = True
        self._activity.set(
            generating=True,
            run_state=RunState(
                owner="generation",
                phase="running",
                detail="Preparing generation",
            ),
        )
        self._sync_generate()
        run = self._generate_edit if edit is not None else self._generate
        extra = (edit,) if edit is not None else ()
        self._future = self._runner.submit(
            run(
                name,
                endpoint,
                request,
                self._saved(),
                self.lora.receipt_ids(),
                self.page.triggerPrefixCheck.isChecked(),
                self.simulation(),
                *extra,
            ),
            self._generated,
        )
        return True

    def _queue_behind(self, edit: EditJob | None) -> None:
        """The window's queue dispatch owns the state lock: hand the request to the Queues page as a one-item queue
        that waits up next and starts by itself after the running queue (issue #125). Nothing is dispatched here."""
        if self.queue_behind is None:
            return
        try:
            request, name, endpoint, asset_ids = self.request_for_queue()
        except FormError as exc:
            self._status(Presentation("error", "Invalid request", str(exc)))
            return
        self._status(Presentation("ok", "Queuing behind the running queue", "The Queues page reports the outcome."))
        self.queue_behind(request, name, endpoint, asset_ids, edit)
        self.queue_created()

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

    async def _generate_edit(self, name, endpoint, request, saved, asset_ids, prefix, simulation, job):
        """One explicit owner-probe edit: the user confirmed this exact scope in the editor just now."""
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state, **simulation)
        permit = owner_probe_permit(selection, request.model_id, "inpaint", f"gui-{uuid4().hex[:12]}")
        prepared = await asyncio.to_thread(
            prepare_edit_inputs,
            job.source,
            job.mask,
            request.width_px,
            request.height_px,
            grow=job.grow,
            feather=job.feather,
        )
        return await generate_image_once(
            selection,
            self._state,
            request,
            prepared,
            permit=permit,
            strength=job.strength,
            preserve_outside=job.keep_outside,
            allow_image_probe=True,
            prefix=prefix,
            saved=saved,
            on_event=self._relay,
            asset_ids=asset_ids,
        )

    def _event(self, event: BackendEvent) -> None:
        if not self._generating:
            return  # a late event after completion never changes the settled view
        label = event.stage if event.kind == "progress" else {"preview": "first preview"}.get(event.kind, event.kind)
        if label:
            self._stage_seen.setdefault(label, time.monotonic() - self._stage_started)
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
        self._sync_generate()
        if failure is not None:
            diagnostic_event(
                log,
                "generation.failed",
                level=logging.WARNING,
                data=failure_fields(failure),
            )
            presentation = present_generation_failure(failure)
            self._status(presentation)
            activity_strip(self.page).complete("generation-result", presentation.headline, presentation.detail, "error")
            if dev_enabled():
                self._show_failure_details(failure)
            return
        diagnostic_event(
            log,
            "generation.outputs_saved",
            level=logging.INFO,
            data={
                "backend": outcome.backend_id,
                "outputs": len(outcome.output_paths),
            },
        )
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
            describe_request(outcome.request, outcome.backend_id, self._submitted_endpoint)
            + self._timing_text()
            + self._quality_text(outcome)
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
            if self._run_edit:  # only an edit's result belongs on the Inpaint canvas, whatever the mode is now
                self.inpaint.show_result(outcome.final_image, outcome.output_paths[0] if outcome.output_paths else None)

    def _quality_text(self, outcome) -> str:
        """Development mode: the export noise/blank detector's verdict on each saved output, also written to the log."""
        if not dev_enabled():
            return ""
        lines = []
        for index, path in enumerate(outcome.output_paths):
            result = assess_file(str(path))
            if result is None:
                continue
            reasons = ", ".join(result["reasons"]) or "none"
            diagnostic_event(
                log,
                "generation.image_quality",
                level=logging.DEBUG,
                data={
                    "output": index,
                    "label": result["label"],
                    "signals": reasons,
                },
            )
            lines.append(f"  output {index + 1}: {result['label']} (signals: {reasons})")
            lines.append("    " + ", ".join(f"{k}={v}" for k, v in result["metrics"].items()))
        return "\n\nimage quality (export detector, advisory):\n" + "\n".join(lines) if lines else ""

    def _timing_text(self) -> str:
        """Development mode: when each stage was first reported, in seconds since the request was submitted."""
        if not dev_enabled() or not self._stage_seen:
            return ""
        lines = [f"  {name}: {seconds:.1f} s" for name, seconds in self._stage_seen.items()]
        total = time.monotonic() - self._stage_started
        return "\n\nstage timings (first reported, since submit):\n" + "\n".join(lines) + f"\n  settled: {total:.1f} s"

    def _show_failure_details(self, failure: BaseException) -> None:
        """Development mode only: keep the request and the backend's failure evidence in the Attempt details box."""
        backend, endpoint = self._submitted_backend, self._submitted_endpoint
        self.page.attemptIdentityLabel.setText("Attempt failed (development mode details below).")
        self.page.effectiveRequestBrowser.setPlainText(
            describe_request(self._request, backend, endpoint)
            + "\n\nfailure:\n"
            + describe_failure(failure)
            + self._timing_text()
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
        self._sync_generate()
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
        diagnostic_event(
            log,
            "generation.inventory_refresh_requested",
            level=logging.INFO,
            data={
                "backend": name,
            },
        )
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
        # Re-select only a listed entry: setCurrentText() would append a carried-over identifier from another family
        # (for example the previous SDXL pick) to this family's list, where it then shows up in the search.
        combo.setCurrentIndex(combo.findText(current))

    def _step_checkpoint(self, offset: int) -> None:
        """Select the neighbouring entry of the current checkpoint list, which already reflects the active filters."""
        combo = self.page.modelCombo
        if combo.currentIndex() < 0:
            index = 0 if offset > 0 else combo.count() - 1  # nothing selected: Next takes the first, Previous the last
        else:
            index = combo.currentIndex() + offset
        if not 0 <= index < combo.count():
            return
        sampler = self.page.samplerCombo
        if not self._step_settle.isActive():
            self._step_sampler = sampler.currentText()
        # Browsing passes over models the user does not keep: LoRAs are pruned only for the model stepping stops on,
        # and the sampler chosen before browsing comes back on any model that allows it.
        self._step_settle.start()
        combo.setCurrentIndex(index)
        if self._step_sampler != sampler.currentText() and sampler.findText(self._step_sampler) >= 0:
            sampler.setCurrentText(self._step_sampler)

    def _checkpoint_steps_settled(self) -> None:
        if not self._updating_catalogue:
            self._filter_loras()

    def _sync_checkpoint_steppers(self, *_args) -> None:
        """Previous/next stop at the ends of the list rather than wrapping."""
        combo = self.page.modelCombo
        index, count = combo.currentIndex(), combo.count()
        # With nothing selected, both are enabled: Next takes the first entry and Previous the last.
        none = index < 0 and count > 0
        self.page.previousCheckpointButton.setEnabled(index > 0 or none)
        self.page.nextCheckpointButton.setEnabled(index + 1 < count)

    def _catalogue_domain(self) -> str:
        return self.page.generationDomainCombo.currentData() or "all"

    def _catalogue_filter_changed(self, *_args) -> None:
        if not self._updating_catalogue:
            self._rebuild_catalogue(preferred_checkpoint=self.page.modelCombo.currentText())

    def _catalogue_family_changed(self, *_args) -> None:
        if not self._updating_catalogue:
            self._rebuild_checkpoints(preferred_checkpoint=self.page.modelCombo.currentText())

    def _catalogue_checkpoint_changed(self, *_args) -> None:
        self.page.checkpointDetailsButton.setEnabled(bool(self.page.modelCombo.currentText().strip()))
        if not self._updating_catalogue:
            # While previous/next stepping is in progress, keep selected LoRAs until it settles.
            self._filter_loras(prune_selected=not self._step_settle.isActive())
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
            unclassified = self._catalogue.view().unclassified_checkpoints
            if unclassified:
                # Checkpoints the catalogue cannot place stay selectable without guessing a family for them.
                page.baseModelCombo.addItem("Unclassified", UNCLASSIFIED_FAMILY)
            family_index = page.baseModelCombo.findData(current_family)
            if family_index < 0 and preferred_checkpoint:
                entry = self._catalogue.checkpoint(preferred_checkpoint)
                if entry is not None:
                    family_index = page.baseModelCombo.findData(entry.base_model)
                elif preferred_checkpoint in unclassified:
                    family_index = page.baseModelCombo.findData(UNCLASSIFIED_FAMILY)
            page.baseModelCombo.setCurrentIndex(
                family_index if family_index >= 0 else (0 if page.baseModelCombo.count() else -1)
            )
        finally:
            self._updating_catalogue = False
        self._rebuild_checkpoints(preferred_checkpoint=preferred_checkpoint, preserve_selection=preserve_selection)
        view = self._catalogue.view()
        unknown, other = view.unclassified_checkpoints, view.other_files
        page.unclassifiedModelsButton.setVisible(bool(unknown or other))
        if other:
            page.unclassifiedModelsButton.setText(
                f"{len(unknown) + len(other)} unclassified file"
                + ("" if len(unknown) + len(other) == 1 else "s")
                + " found"
            )
        else:
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
        unclassified = set(self._catalogue.view().unclassified_checkpoints)
        if family == UNCLASSIFIED_FAMILY:
            entries = ()
            selectable = sorted(unclassified, key=str.casefold)
        else:
            entries = self._catalogue.checkpoints(
                domain=self._catalogue_domain(),
                base_model=family if isinstance(family, str) else None,
                include_niche=page.includeNicheCheck.isChecked(),
                include_unavailable=include_cloud,
            )
            selectable = [entry.identifier for entry in entries]
        allowed = {entry.identifier for entry in entries}
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

    def _compute_units_estimate(self) -> ComputeUnitEstimate:
        """Read the same current form settings for the meter and explicit inspector."""
        page = self.page
        model_id = page.modelCombo.currentText().strip()
        entry = self._catalogue.checkpoint(model_id, include_unavailable=True) if model_id else None
        return estimate_compute_units(
            model_id=model_id,
            base_model=entry.base_model if entry else None,
            width_px=page.widthSpinBox.value(),
            height_px=page.heightSpinBox.value(),
            steps=page.stepsSpinBox.value(),
            guidance=page.guidanceSpinBox.value(),
            has_loras=bool(self.lora.loras),
            operation="inpaint" if page.inpaintModeButton.isChecked() else "txt2img",
        )

    def _update_compute_units(self, *_args) -> None:
        """Show an offline, non-authoritative DT+ complexity estimate for one T2I image.

        Both Local Draw Things (which may bridge to DT+) and direct Cloud
        show the estimate. It is not evidence that a local execution uses DT+.
        The number never enters preflight, dispatch, saved jobs, quota or billing.
        Changes to cloud credentials and inventory do not trigger a provider call.
        """
        meter = self.page.computeUnitsMeter
        name, _ = self._backend()
        shown = name in ("local", "cloud")
        meter.setVisible(shown)
        if not shown:
            return
        estimate = self._compute_units_estimate()
        units = estimate.units
        if units is None:
            state, text, value = "unavailable", "unavailable", 0
        else:
            # Non-linear fill (compute_units_fill); at or above COMPUTE_UNITS_LIMIT the bar stays full and turns red.
            state = "over" if units >= COMPUTE_UNITS_LIMIT else "ok"
            text = f"{units:,}".replace(",", "\u202f")
            value = compute_units_meter_value(units)
        meter.setMaximum(COMPUTE_UNITS_METER_STEPS)
        meter.setValue(value)
        meter.setFormat("\u2002" + text)  # en space: the QSS cannot pad progress-bar text
        if meter.property("state") != state:
            meter.setProperty("state", state)
            meter.style().unpolish(meter)
            meter.style().polish(meter)
        # No hover overlay: the reason is exposed to assistive technology only.
        meter.setAccessibleDescription(
            f"{estimate.reason} Offline estimate; not a balance, entitlement check or billing quote."
        )

    def show_checkpoint_details(self) -> None:
        """Inspect local catalogue and last-observed provider evidence without dispatch."""
        model_id = self.page.modelCombo.currentText().strip()
        if not model_id:
            return
        details = resolve_checkpoint_metadata(
            model_id,
            catalogue=self._catalogue,
            inventory=self._inventory_snapshot,
        )
        text = details.as_text()
        if self._backend()[0] in ("local", "cloud"):
            estimate = self._compute_units_estimate()
            result = "unavailable" if estimate.units is None else f"{estimate.units:,}".replace(",", "\u202f") + " CU"
            text += (
                f"\n\nCompute Units: {result}\n"
                "Supported models: SD 1.x/2.x, SDXL/SSD, SD3, FLUX.1/2/3, "
                "Qwen-Image/2.1, Z-Image, Cosmos 2.5, Krea 2, Ideogram 4, "
                "HiDream I1/O1, ERNIE, MOSS, PixArt, AuraFlow and Würstchen."
            )
        InlineNotice.information(self.page, "Checkpoint details", text)

    def _show_unclassified_models(self) -> None:
        view = self._catalogue.view()
        unknown, other = view.unclassified_checkpoints, view.other_files
        if not unknown and not other:
            return
        text = (
            "These checkpoints are available on the active provider but are not classified by the DTFlow catalogue. "
            "They remain usable through the Checkpoint selector's custom-identifier action. "
            "DTFlow will not guess their family, generation type, quantization or compatible LoRAs.\n\n"
            + "\n".join(unknown)
        )
        if other:
            text += (
                "\n\nOther files listed by the provider without metadata (LoRAs, controls, encoders, upscalers or "
                "checkpoints; DTFlow cannot tell them apart). They are not offered as checkpoints:\n\n"
                + "\n".join(other)
            )
        InlineNotice.information(self.page, "Unclassified files", text)

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
        other = 0
        if dict(snapshot.diagnostics).get("model_metadata") != "present":
            # Metadata-less inventories (direct DT+ cloud) list every file as unknown: count what the catalogue
            # can prove instead of calling every file a model.
            view = self._catalogue.view()
            models = len({entry.identifier for entry in view.checkpoints} | set(view.unclassified_checkpoints))
            loras = len(view.loras)
            other = len(snapshot.entries) - models - loras
        try:
            observed = datetime.fromisoformat(snapshot.observed_at_utc).astimezone(UTC).strftime("%d %b %H:%M UTC")
        except ValueError:
            observed = snapshot.observed_at_utc
        counts = f"{models} model(s) · {loras} LoRA(s)" + (f" · {other} other file(s)" if other > 0 else "")
        lines = [counts, f"{self._inventory_how} · observed {observed}"]
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
            diagnostic_event(
                log,
                "generation.inventory_refresh_failed",
                level=logging.WARNING,
                data=failure_fields(failure),
            )
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
        self.lora.invalidate_prepare_ownership()
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
            diagnostic_event(
                log,
                "generation.configuration_scan_failed",
                level=logging.WARNING,
                data={
                    "reason": type(failure).__name__,
                },
            )
            combo.setCurrentIndex(-1)
            return
        paths, presets, preset_failure = result
        if preset_failure is not None:
            diagnostic_event(
                log,
                "generation.preset_scan_failed",
                level=logging.WARNING,
                data={
                    "reason": type(preset_failure).__name__,
                },
            )
        for preset in presets:
            combo.addItem(f"Preset · {preset['name']}", ("preset", preset))
        own = {preset["name"] for preset in presets}
        for preset in builtin_presets():
            if preset["name"] not in own:
                combo.addItem(f"DT · {preset['name']}", ("preset", preset))
        for path in paths:
            combo.addItem(f"Config · {path.name}", str(path))
        combo.setCurrentIndex(-1)

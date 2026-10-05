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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox, QPushButton, QWidget

from dtflow.composition import BACKENDS
from dtflow.core.models import (
    BackendEvent,
    CredentialProvider,
    Designation,
    InventorySnapshot,
    ValidationError,
    eligible_model_ids,
)
from dtflow.services.connection import (
    Presentation,
    present_generation_failure,
    present_interruption,
    present_outcome,
)
from dtflow.services.session import (
    cached_inventory,
    generate_once,
    read_attempt,
    refresh_inventory,
    selection_for,
)
from dtflow.ui.activity import Activity
from dtflow.ui.empty_hint import EmptyListHint
from dtflow.ui.generation_presenter import (
    FAKE_MODEL,
    SAMPLERS,
    FormError,
    FormValues,
    build_request,
    describe_request,
    form_from_document,
    form_to_document,
    read_configuration,
    save_configuration,
    snap_dimension,
    values_from_request,
)
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
    "modelCombo",
    "refreshInventoryButton",
    "generateButton",
    "cancelButton",
    "configurationCombo",
    "loadConfigurationButton",
    "saveConfigurationButton",
    "loraList",
    "triggerPrefixCheck",
    "assetList",
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
        self._listed_backend: str | None = None
        self._activity.listen(self._sync_activity)
        self._choose_save, self._choose_open = choose_save, choose_open
        self._configs = self._state / "configs"
        self._relay = EventRelay(self._event, page)
        self._future = None
        self._generating = False
        self._request = None
        self._busy = False
        self._config_name: str | None = None
        self._metadata_path: Path | None = None
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
        page.samplerCombo.addItems(SAMPLERS)
        page.generationBackendCombo.addItems(BACKENDS)
        page.generationBackendCombo.setToolTip("Chosen on the Connection page.")
        page.cancelButton.setEnabled(False)
        page.generateButton.clicked.connect(self.generate)
        page.cancelButton.clicked.connect(self.cancel)
        page.refreshInventoryButton.clicked.connect(self.refresh)
        page.saveConfigurationButton.clicked.connect(self.save)
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
        page.modelCombo.lineEdit().setPlaceholderText("Select or enter a model")
        page.promptEdit.setTabChangesFocus(True)
        for control in (page.expandPromptButton, page.detailsToggle):
            control.setEnabled(True)
        page.attemptDetails.hide()
        page.detailsToggle.toggled.connect(page.attemptDetails.setVisible)
        page.expandPromptButton.toggled.connect(
            lambda expanded: page.promptEdit.setFixedHeight(180 if expanded else 76)
        )
        page.resultPreviewLabel.enable_inspection()
        self.sync_backend()
        self._scan_configs()

    # -- context ---------------------------------------------------------------------------------------------------
    def sync_backend(self) -> None:
        """Mirror the Connection page's backend; it is read-only here so there is one selector."""
        name, _ = self._backend()
        self.page.generationBackendCombo.setCurrentText(name)
        if self._listed_backend is not None and self._listed_backend != name:
            self._fill_models(())  # a list from another provider is not offered for this one
            self.page.inventoryEvidenceLabel.setText("Inventory has not been refreshed.")
            self.page.inventoryEvidenceLabel.setToolTip("")
            self._listed_backend = None
        self.page.modelCombo.lineEdit().setPlaceholderText(
            f"{FAKE_MODEL} (default)" if name == "fake" else "Exact model ID, or Refresh inventory"
        )
        self.lora.sync_backend()

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
        page.samplerCombo.setCurrentText(values.sampler)

    def form_document(self) -> dict:
        """The remembered form values (credential-free, unvalidated)."""
        return form_to_document(self._values())

    def restore(self, document: object) -> None:
        """Apply a remembered form document; bad fields fall back to defaults."""
        values = form_from_document(document)
        self._apply(values)
        self.lora.set_loras(values.loras)

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

    def _request_for_check(self):
        """A request for LoRA preflight only: a blank prompt gets a placeholder because nothing is dispatched."""
        self._mark(None)
        name, _ = self._backend()
        values = self._values()
        try:
            return build_request(replace(values, prompt=values.prompt or "preflight check"), backend=name)
        except FormError as exc:
            self._mark(exc.widget)
            self.lora._show(self.page.compatibilityStatusLabel, Presentation("error", "Cannot check", str(exc)))
            return None

    # -- presentation ----------------------------------------------------------------------------------------------
    def _status(self, presentation: Presentation) -> None:
        label = self.page.generationStatusLabel
        label.setProperty("state", presentation.level)
        label.style().unpolish(label)
        label.style().polish(label)
        label.setText(f"{presentation.headline}. {presentation.detail}".strip())

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
        self.page.cancelButton.setEnabled(busy and self._generating)
        self.lora.set_enabled(not busy)

    def _sync_activity(self) -> None:
        """A queue dispatch holds the state lock, so single generation waits for it (and vice versa)."""
        self.page.generateButton.setEnabled(not self._busy and not self._activity.dispatching)
        if self._activity.dispatching:
            self._status(
                Presentation("warning", "Queue dispatch is running", "Single generation is unavailable until it stops.")
            )

    def request_for_queue(self):
        """The validated request, backend and bound receipts for a new queue; raises ``FormError`` after marking."""
        self._mark(None)
        self._snap_size()
        name, endpoint = self._backend()
        try:
            request = build_request(self._values(), backend=name, config_name=self._config_name)
        except FormError as exc:
            self._mark(exc.widget)
            raise
        return request, name, endpoint, self.lora.receipt_ids()

    def _progress(self, text: str, completed: int | None = None, total: int | None = None) -> None:
        bar = self.page.generationProgressBar
        if total:
            bar.setRange(0, total)
            bar.setValue(min(completed or 0, total))
        else:
            bar.setRange(0, 0)  # indeterminate: no server percentage is known
        bar.setFormat(text)

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
            request = build_request(self._values(), backend=name, config_name=self._config_name)
        except FormError as exc:
            self._mark(exc.widget)
            log.info("generate rejected backend=%s field=%s", name, exc.widget or "-")
            log.debug("generate validation message=%s", exc)
            self._status(Presentation("error", "Invalid request", str(exc)))
            return
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
        self._progress("Preparing")
        self._status(Presentation("warning", "Generating", f"Waiting on the selected backend. {note or ''}".strip()))
        self._generating = True
        self._activity.set(generating=True)
        self._set_busy(True)
        self._future = self._runner.submit(
            self._generate(
                name,
                endpoint,
                request,
                self._saved(),
                self.lora.receipt_ids(),
                self.page.triggerPrefixCheck.isChecked(),
            ),
            self._generated,
        )

    async def _generate(self, name, endpoint, request, saved, asset_ids, prefix):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state)
        return await generate_once(
            selection, self._state, request, prefix=prefix, saved=saved, on_event=self._relay, asset_ids=asset_ids
        )

    def _event(self, event: BackendEvent) -> None:
        if not self._busy:
            return  # a late event after completion never changes the settled view
        if event.kind == "progress":
            self._progress(event.stage or "Generating", event.completed, event.total)
        elif event.kind == "preview" and event.artifact and event.artifact.designation == Designation.PREVIEW:
            self._show_image(event.artifact.encoded, "preview")
            self._progress("Preview shown (not the final image)")
        elif event.kind in ("preparing", "submission_possible", "terminal"):
            self._progress(event.stage or event.kind)

    def _generated(self, outcome, failure) -> None:
        if isinstance(failure, asyncio.CancelledError):
            self._status(Presentation("warning", "Local stop requested", "Reading the recorded evidence."))
            request_id = self._request.request_id
            self._runner.submit(read_attempt(self._state, request_id), self._interrupted)
            return
        self._generating = False
        self._activity.set(generating=False)
        self._set_busy(False)
        if failure is not None:
            self._progress("Failed")
            log.warning("generate failed reason=%s", failure_reason(failure))
            self._status(present_generation_failure(failure))
            return
        log.info("generate saved backend=%s outputs=%d", outcome.backend_id, len(outcome.output_paths))
        bar = self.page.generationProgressBar
        bar.setRange(0, 1)
        bar.setValue(1)
        bar.setFormat("Saved")
        self._status(present_outcome(outcome))
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
        self._set_result_actions(True)
        self._metadata_path = Path(outcome.metadata_path)
        if len(outcome.output_paths) > 1:
            self.page.detailsToggle.setChecked(True)  # the output chooser lives in the details
        if outcome.final_image:
            self._show_image(outcome.final_image, "final", outcome.backend_id)

    # -- saved results ---------------------------------------------------------------------------------------------
    def _set_result_actions(self, available: bool) -> None:
        self.page.openOutputFolderButton.setEnabled(available)
        self.page.copyOutputPathButton.setEnabled(available)
        self.page.resultList.setEnabled(available)
        self.openFileButton.setEnabled(available)
        self.metadataButton.setEnabled(available)
        if not available:
            self._metadata_path = None

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
            QMessageBox.warning(self.page, "Metadata unavailable", "The saved metadata file could not be read.")
            return
        if len(content) > METADATA_LIMIT:
            content = content[:METADATA_LIMIT] + "\n… (truncated: the saved file is longer)"
        dialog = QMessageBox(self.page)
        dialog.setWindowTitle("Saved generation metadata")
        dialog.setText("Reproducibility metadata from the saved attempt.")
        dialog.setDetailedText(content)
        dialog.exec()

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
            self.page.cancelButton.setEnabled(False)
            self._future.cancel()

    def _interrupted(self, evidence, failure) -> None:
        self._generating = False
        self._activity.set(generating=False)
        self._set_busy(False)
        self._progress("Stopped locally")
        if failure is not None:
            self._status(present_generation_failure(failure))
            return
        self._status(present_interruption(evidence))
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
        self.page.inventoryEvidenceLabel.setText("Refreshing inventory…")
        log.info("inventory refresh requested backend=%s", name)
        self._runner.submit(self._refresh(name, endpoint, self._saved()), self._refreshed)

    async def _refresh(self, name, endpoint, saved):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state)
        return await refresh_inventory(selection, self._state, saved=saved, no_cache=self._no_cache)

    def _fill_models(self, ids) -> None:
        """Replace the drop-down entries, keeping whatever is typed (the box stays editable for unlisted models)."""
        combo = self.page.modelCombo
        current = combo.currentText()
        combo.clear()
        combo.addItems(ids)
        combo.setCurrentText(current)

    def _show_inventory(self, snapshot: InventorySnapshot, how: str) -> None:
        self.lora.set_inventory(snapshot)
        ids = eligible_model_ids(snapshot)
        self._fill_models(ids)
        self._listed_backend = self._backend()[0]
        label = self.page.inventoryEvidenceLabel
        label.setToolTip(f"{snapshot.backend_id} inventory {how}; observed {snapshot.observed_at_utc}.")
        try:
            observed = datetime.fromisoformat(snapshot.observed_at_utc).astimezone(UTC).strftime("%d %b %H:%M UTC")
        except ValueError:
            observed = snapshot.observed_at_utc
        label.setText(f"{len(ids)} model(s) · {how.split(' (')[0]}\nobserved {observed}")

    def _refreshed(self, snapshot, failure) -> None:
        self._set_busy(False)
        label = self.page.inventoryEvidenceLabel
        if failure is not None:
            shown = present_generation_failure(failure)
            log.warning("inventory refresh failed reason=%s", failure_reason(failure))
            label.setText(f"Inventory refresh failed. {shown.headline}: {shown.detail}")
            label.setToolTip("")
            return
        self._show_inventory(snapshot, "refreshed" if snapshot.refreshed else "as reported")

    def request_cached(self) -> None:
        """Prefill the lists from the disposable cache; a miss, a bypass or a stale answer changes nothing."""
        if self._no_cache:
            return
        name, endpoint = self._backend()
        self._runner.submit(
            self._cached(name, endpoint, self._saved()), lambda snapshot, failure: self._cached_loaded(name, snapshot)
        )

    async def _cached(self, name, endpoint, saved):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state)
        return await cached_inventory(selection, self._state, saved=saved)

    def _cached_loaded(self, name: str, snapshot) -> None:
        if snapshot is None or self._backend()[0] != name or self._listed_backend == name:
            return
        self._show_inventory(snapshot, "cached (press Refresh inventory for live)")

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
        path = self.page.configurationCombo.itemData(index)
        if path:
            self._load(Path(path))

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
        self._apply(values_from_request(request))
        self.lora.set_loras(request.loras, loaded=True)
        name, endpoint = self._backend()
        self.page.effectiveRequestBrowser.setPlainText(describe_request(request, name, endpoint))
        detail = f"{path.name} applied; values come from the file, with defaults only where it is silent."
        if request.loras:
            detail += " It names LoRA(s); no cloud receipt is bound."
        self._status(Presentation("ok", "Configuration loaded", detail))

    def _scan_configs(self) -> None:
        directory = self._configs

        async def scan():
            return await asyncio.to_thread(lambda: sorted(directory.glob("*.json")) if directory.is_dir() else [])

        self._runner.submit(scan(), self._scanned)

    def _scanned(self, paths, failure) -> None:
        combo = self.page.configurationCombo
        combo.clear()
        for path in paths or []:
            combo.addItem(path.name, str(path))
        combo.setCurrentIndex(-1)

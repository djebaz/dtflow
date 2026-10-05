"""Generate page controller: widgets in, shared services out. Owns no resolver, backend or persistence rules.

Every service call runs on the shared ``AsyncRunner``; backend events cross to the GUI thread through a coalescing
``EventRelay``. Result wording comes from the shared presentation mapping and from durable evidence, never from a button
press: a local stop is reported as a local stop, and a possibly submitted attempt stays "outcome unknown".
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QWidget

from dtflow.composition import BACKENDS
from dtflow.core.models import BackendEvent, CredentialProvider, Designation, ValidationError
from dtflow.services.connection import (
    Presentation,
    present_generation_failure,
    present_interruption,
    present_outcome,
)
from dtflow.services.session import generate_once, read_attempt, refresh_inventory, selection_for
from dtflow.ui.activity import Activity
from dtflow.ui.generation_presenter import (
    FAKE_MODEL,
    SAMPLERS,
    FormError,
    FormValues,
    build_request,
    describe_request,
    read_configuration,
    save_configuration,
    values_from_request,
)
from dtflow.ui.lora_panel import LoraAssetPanel
from dtflow.ui.runner import AsyncRunner, EventRelay

PREVIEW_BOX = 360  # bounded display size; the saved file is never resized
RESULT_PATH_ROLE = Qt.ItemDataRole.UserRole
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
        choose_save: Callable[[QWidget, Path], str] = _choose_save,
        choose_open: Callable[[QWidget, Path], str] = _choose_open,
    ):
        self.page = page
        self._runner = runner
        self._backend = backend_source
        self._saved = saved_source
        self._state = Path(state_dir)
        self._activity = activity or Activity()
        self._activity.listen(self._sync_activity)
        self._choose_save, self._choose_open = choose_save, choose_open
        self._configs = self._state / "configs"
        self._relay = EventRelay(self._event, page)
        self._future = None
        self._generating = False
        self._request = None
        self._busy = False
        self._config_name: str | None = None
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
        self.sync_backend()
        self._scan_configs()

    # -- context ---------------------------------------------------------------------------------------------------
    def sync_backend(self) -> None:
        """Mirror the Connection page's backend; it is read-only here so there is one selector."""
        name, _ = self._backend()
        self.page.generationBackendCombo.setCurrentText(name)
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

    def _show_image(self, encoded: bytes, designation: str) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(encoded):
            return
        label = self.page.resultPreviewLabel
        label.setPixmap(
            pixmap.scaled(
                PREVIEW_BOX, PREVIEW_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        label.setProperty("designation", designation)

    # -- generation ------------------------------------------------------------------------------------------------
    def generate(self) -> None:
        if self._busy:
            return
        self._mark(None)
        name, endpoint = self._backend()
        try:
            request = build_request(self._values(), backend=name, config_name=self._config_name)
        except FormError as exc:
            self._mark(exc.widget)
            self._status(Presentation("error", "Invalid request", str(exc)))
            return
        self._request = request
        self.page.effectiveRequestBrowser.setPlainText(describe_request(request, name, endpoint))
        self.page.resultList.clear()
        self._set_result_actions(False)
        self.page.attemptIdentityLabel.setText("No durable attempt yet.")
        self.page.resultPreviewLabel.clear()
        self.page.resultPreviewLabel.setProperty("designation", "")
        self._progress("Preparing")
        self._status(Presentation("warning", "Generating", "Waiting on the selected backend."))
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
            self._status(present_generation_failure(failure))
            return
        bar = self.page.generationProgressBar
        bar.setRange(0, 1)
        bar.setValue(1)
        bar.setFormat("Saved")
        self._status(present_outcome(outcome))
        self.lora.show_diagnostics(outcome.diagnostics)
        self.page.attemptIdentityLabel.setText(f"Attempt {outcome.attempt_id} on {outcome.backend_id}.")
        self.page.effectiveRequestBrowser.setPlainText(
            describe_request(outcome.request, outcome.backend_id, self._backend()[1])
        )
        for label, path in (*(("Output", p) for p in outcome.output_paths), ("Metadata", outcome.metadata_path)):
            item = QListWidgetItem(f"{label}: {path}")
            item.setData(RESULT_PATH_ROLE, str(path))
            self.page.resultList.addItem(item)
        self._set_result_actions(True)
        if outcome.final_image:
            self._show_image(outcome.final_image, "final")

    # -- saved results ---------------------------------------------------------------------------------------------
    def _set_result_actions(self, available: bool) -> None:
        self.page.openOutputFolderButton.setEnabled(available)
        self.page.copyOutputPathButton.setEnabled(available)

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
            self.page.attemptIdentityLabel.setText(f"Attempt {evidence.attempt_id}: recorded state {evidence.state}.")

    # -- inventory -------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if self._busy:
            return
        name, endpoint = self._backend()
        self._set_busy(True)
        self.page.inventoryEvidenceLabel.setText("Refreshing inventory…")
        self._runner.submit(self._refresh(name, endpoint, self._saved()), self._refreshed)

    async def _refresh(self, name, endpoint, saved):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state)
        return await refresh_inventory(selection, self._state, saved=saved)

    def _refreshed(self, snapshot, failure) -> None:
        self._set_busy(False)
        label = self.page.inventoryEvidenceLabel
        if failure is not None:
            shown = present_generation_failure(failure)
            label.setText(f"Inventory refresh failed. {shown.headline}: {shown.detail}")
            return
        self.lora.set_inventory(snapshot)
        combo = self.page.modelCombo
        current = combo.currentText()
        combo.clear()
        combo.addItems([entry.identifier for entry in snapshot.entries if entry.kind == "model"])
        combo.setCurrentText(current)
        how = "refreshed" if snapshot.refreshed else "as reported"
        label.setText(f"{snapshot.backend_id} inventory {how}; observed {snapshot.observed_at_utc}.")

    # -- configuration ---------------------------------------------------------------------------------------------
    def save(self) -> None:
        self._mark(None)
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

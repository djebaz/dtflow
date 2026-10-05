"""LoRA selection and cloud-asset presentation for the Generate page.

Compatibility diagnostics come from the shared preflight service and receipts from the shared asset service; this
module only collects choices and renders what those services return. The asset list is this machine's local registry,
not DT+ account inventory, and Prepare is the one action that can upload (always behind a confirmation).
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QListWidgetItem, QMessageBox, QWidget

from dtflow.core.models import LoRASelection, ResolvedGenerationRequest, ValidationError
from dtflow.services.connection import (
    Presentation,
    present_asset_failure,
    present_generation_failure,
    present_preflight,
    present_reconciliation,
)
from dtflow.services.session import check_loras, list_assets, prepare_asset, reconcile_asset, selection_for
from dtflow.ui.runner import AsyncRunner

UPLOAD_NOTICE = (
    "Preparing this file may upload its bytes to DT+ if the server does not already have it. Uploading itself does "
    "not consume credits, but a later generation that uses the file may. Nothing else in DTFlow uploads. Continue?"
)
CLOUD_ONLY = "Asset actions need the cloud backend (choose it on the Connection page)."
LORA_WEIGHT_RANGE = (-100.0, 100.0)  # a dialog bound only; the shared request model requires a finite weight


def _choose_lora(parent: QWidget, candidates: list[tuple[str, float | None]]) -> tuple[str, float] | None:
    names = [name for name, _ in candidates]
    name, accepted = QInputDialog.getItem(parent, "Add LoRA", "Exact LoRA identifier", names, 0, True)
    if not accepted or not name.strip():
        return None
    default = dict(candidates).get(name.strip()) or 1.0
    weight, accepted = QInputDialog.getDouble(parent, "LoRA weight", "Weight", default, *LORA_WEIGHT_RANGE, 2)
    return (name.strip(), weight) if accepted else None


def _choose_asset_file(parent: QWidget, directory: Path) -> str:
    return QFileDialog.getOpenFileName(parent, "Prepare converted LoRA file", str(directory), "LoRA files (*.ckpt)")[0]


def _confirm_upload(parent: QWidget, path: Path) -> bool:
    answer = QMessageBox.question(parent, "Prepare cloud asset", f"{path.name}\n\n{UPLOAD_NOTICE}")
    return answer == QMessageBox.StandardButton.Yes


class LoraAssetPanel:
    def __init__(
        self,
        page: QWidget,
        *,
        runner: AsyncRunner,
        backend_source: Callable[[], tuple[str, str | None]],
        saved_source: Callable,
        state_dir: Path,
        build_request: Callable[[], ResolvedGenerationRequest | None],
        is_busy: Callable[[], bool],
        set_busy: Callable[[bool], None],
    ):
        self.page = page
        self._runner, self._backend, self._saved = runner, backend_source, saved_source
        self._state, self._build, self._is_busy, self._set_busy = Path(state_dir), build_request, is_busy, set_busy
        self.choose_lora, self.choose_asset_file, self.confirm_upload = (
            _choose_lora,
            _choose_asset_file,
            _confirm_upload,
        )
        self.loras: tuple[LoRASelection, ...] = ()
        self._receipts: dict[str, str] = {}  # selected LoRA identifier -> local receipt id (never exported)
        self._candidates: list[tuple[str, float | None]] = []
        self._records: dict[str, dict] = {}
        page.addLoraButton.clicked.connect(self.add)
        page.removeLoraButton.clicked.connect(self.remove)
        page.checkLorasButton.clicked.connect(self.check)
        page.prepareAssetButton.clicked.connect(self.prepare)
        page.reconcileAssetButton.clicked.connect(self.reconcile)
        page.useReceiptButton.clicked.connect(self.use_receipt)
        self.sync_backend()
        self.refresh_assets()

    # -- shared helpers --------------------------------------------------------------------------------------------
    @staticmethod
    def _show(label, presentation: Presentation) -> None:
        label.setProperty("state", presentation.level)
        label.style().unpolish(label)
        label.style().polish(label)
        label.setText(f"{presentation.headline}. {presentation.detail}".strip())

    def receipt_ids(self) -> tuple[str, ...]:
        selected = {item.identifier for item in self.loras}
        return tuple(asset for name, asset in self._receipts.items() if name in selected)

    def set_enabled(self, enabled: bool) -> None:
        """Called by the page while a generation or other service call is in flight."""
        cloud = self._backend()[0] == "cloud"
        for name in ("addLoraButton", "removeLoraButton", "checkLorasButton"):
            getattr(self.page, name).setEnabled(enabled)
        for name in ("prepareAssetButton", "reconcileAssetButton", "useReceiptButton"):
            getattr(self.page, name).setEnabled(enabled and cloud)

    def sync_backend(self) -> None:
        cloud = self._backend()[0] == "cloud"
        self.set_enabled(not self._is_busy())
        if not cloud:
            self._show(self.page.assetStatusLabel, Presentation("unknown", "Cloud only", CLOUD_ONLY))

    def set_inventory(self, snapshot) -> None:
        """Remember the LoRA identifiers the backend listed, as suggestions for Add; typing any identifier still works."""
        self._candidates = [(e.identifier, e.default_weight) for e in snapshot.entries if e.kind == "lora"]

    # -- selection -------------------------------------------------------------------------------------------------
    def receipts(self) -> dict[str, str]:
        """The selected LoRA identifier -> receipt id bindings, for remembering the form per provider."""
        return dict(self._receipts)

    def set_loras(self, loras, *, loaded: bool = False, receipts: dict[str, str] | None = None) -> None:
        self.loras = tuple(loras)
        selected = {item.identifier for item in self.loras}
        if receipts is not None:
            self._receipts = dict(receipts)
        self._receipts = {name: asset for name, asset in self._receipts.items() if name in selected}
        self._render_loras()
        text = (
            "Loaded configuration names LoRA(s). No cloud receipt is bound and compatibility is not checked."
            if loaded and self.loras
            else "Compatibility not checked."
        )
        self._show(self.page.compatibilityStatusLabel, Presentation("unknown", text, ""))

    def _render_loras(self) -> None:
        self.page.loraList.clear()
        for item in self.loras:
            text = f"{item.identifier} @ {item.weight}"
            if item.identifier in self._receipts:
                text += f" [receipt {self._receipts[item.identifier][:8]}]"
            self.page.loraList.addItem(text)

    def add(self) -> None:
        candidates = list(self._candidates)
        known = {name for name, _ in candidates}
        candidates += [
            (record["identifier"], None) for record in self._records.values() if record["identifier"] not in known
        ]
        choice = self.choose_lora(self.page, candidates)
        if choice is None:
            return
        try:
            selection = LoRASelection(*choice)
        except ValidationError as exc:
            self._show(self.page.compatibilityStatusLabel, Presentation("error", "LoRA not added", str(exc)))
            return
        self.set_loras((*[x for x in self.loras if x.identifier != selection.identifier], selection))

    def remove(self) -> None:
        row = self.page.loraList.currentRow()
        if row >= 0:
            self.set_loras(self.loras[:row] + self.loras[row + 1 :])

    # -- compatibility ---------------------------------------------------------------------------------------------
    def check(self) -> None:
        if self._is_busy():
            return
        if not self.loras:
            self._show(self.page.compatibilityStatusLabel, present_preflight(()))
            return
        request = self._build()
        if request is None:
            return
        name, endpoint = self._backend()
        self._set_busy(True)
        self._show(self.page.compatibilityStatusLabel, Presentation("warning", "Checking", ""))
        self._runner.submit(self._check(name, endpoint, request, self._saved()), self._checked)

    async def _check(self, name, endpoint, request, saved):
        selection = await asyncio.to_thread(selection_for, name, endpoint, self._state)
        return await check_loras(selection, self._state, request, asset_ids=self.receipt_ids(), saved=saved)

    def _checked(self, diagnostics, failure) -> None:
        self._set_busy(False)
        label = self.page.compatibilityStatusLabel
        self._show(label, present_generation_failure(failure) if failure else present_preflight(diagnostics))

    def show_diagnostics(self, diagnostics) -> None:
        if diagnostics:
            self._show(self.page.compatibilityStatusLabel, present_preflight(diagnostics))

    # -- assets ----------------------------------------------------------------------------------------------------
    def refresh_assets(self) -> None:
        self._runner.submit(list_assets(self._state), self._listed)

    def _listed(self, records, failure) -> None:
        status = self.page.assetStatusLabel
        if failure is not None:
            self._show(status, Presentation("warning", "Registry unavailable", present_asset_failure(failure).detail))
            return
        self._records = {record["asset_id"]: record for record in records}
        selected = self._selected_id()
        self.page.assetList.clear()
        for record in records:
            text = (
                f"{record['identifier']} · {record['state']} ({record['evidence']}) · "
                f"{record['sha256'][:8]} · {record['size']} B"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, record["asset_id"])  # Qt.UserRole
            item.setToolTip(f"Receipt {record['asset_id']} on {record['origin']}")
            self.page.assetList.addItem(item)
            if record["asset_id"] == selected:
                self.page.assetList.setCurrentItem(item)

    def _selected_id(self) -> str | None:
        item = self.page.assetList.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def prepare(self) -> None:
        if self._is_busy() or self._backend()[0] != "cloud":
            return
        chosen = self.choose_asset_file(self.page, self._state)
        if not chosen or not self.confirm_upload(self.page, Path(chosen)):
            return
        self._set_busy(True)
        self._show(self.page.assetStatusLabel, Presentation("warning", "Preparing", "This may upload the file."))
        self._runner.submit(
            prepare_asset(self._state, Path(chosen), provenance="gui", saved=self._saved()), self._prepared
        )

    def _prepared(self, record, failure) -> None:
        self._set_busy(False)
        status = self.page.assetStatusLabel
        if failure is not None:
            self._show(status, present_asset_failure(failure))
        else:
            self._show(
                status,
                Presentation(
                    "ok" if record["state"] == "confirmed" else "warning",
                    "Receipt " + record["state"],
                    f"{record['identifier']} ({record['evidence']}).",
                ),
            )
        self.refresh_assets()

    def reconcile(self) -> None:
        asset_id = self._selected_id()
        if self._is_busy() or self._backend()[0] != "cloud" or asset_id is None:
            return
        self._set_busy(True)
        self._show(self.page.assetStatusLabel, Presentation("warning", "Reconciling", "Metadata check only."))
        self._runner.submit(reconcile_asset(self._state, asset_id, saved=self._saved()), self._reconciled)

    def _reconciled(self, outcome, failure) -> None:
        self._set_busy(False)
        status = self.page.assetStatusLabel
        self._show(status, present_asset_failure(failure) if failure else present_reconciliation(outcome[1]))
        self.refresh_assets()

    def use_receipt(self) -> None:
        asset_id, status = self._selected_id(), self.page.assetStatusLabel
        record = self._records.get(asset_id or "")
        if record is None:
            self._show(status, Presentation("error", "No receipt selected", "Select a receipt in the list first."))
        elif record["state"] != "confirmed":
            self._show(
                status,
                Presentation("error", "Receipt not usable", f"It is {record['state']}; reconcile it to confirm."),
            )
        elif record["identifier"] not in {item.identifier for item in self.loras}:
            self._show(
                status,
                Presentation(
                    "error", "Add the LoRA first", f"Receipts bind by identifier: add {record['identifier']}."
                ),
            )
        else:
            self._receipts[record["identifier"]] = asset_id
            self._render_loras()
            self._show(
                status,
                Presentation(
                    "ok",
                    "Receipt bound",
                    "The server re-validates it before dispatch; it is never exported to a config.",
                ),
            )

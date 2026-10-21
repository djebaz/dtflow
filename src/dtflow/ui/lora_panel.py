"""LoRA selection and cloud-asset presentation for the Generate page.

Compatibility diagnostics come from the shared preflight service and receipts from the shared asset service; this
module only collects choices and renders what those services return. The asset list is this machine's local registry,
not DT+ account inventory. Selecting a file through Prepare can upload it without a second confirmation.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QWidget

from dtflow.core.models import BackendError, LoRASelection, RemoteOutcome, ResolvedGenerationRequest, ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.prepare_preferences import load_prepare_folder, save_prepare_folder
from dtflow.services.connection import (
    Presentation,
    present_asset_failure,
    present_generation_failure,
    present_preflight,
    present_reconciliation,
)
from dtflow.services.session import check_loras, list_assets, prepare_asset, reconcile_asset, selection_for
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.search_select import choose_searchable

UPLOAD_NOTICE = (
    "Prepare accepts multiple converted LoRA files and may upload each to DT+ if the server does not already have it. "
    "Uploading does not consume credits, but a later generation using this LoRA may."
)
DEFAULT_LORA_WEIGHT = 1.0
CLOUD_ONLY = "Asset actions need the cloud backend (choose it on the Connection page)."
log = logging.getLogger(__name__)


def _choose_asset_file(parent: QWidget, directory: Path) -> list[str]:
    return QFileDialog.getOpenFileNames(parent, "Prepare converted LoRA files", str(directory), "LoRA files (*.ckpt)")[
        0
    ]


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
        self.choose_asset_file = _choose_asset_file
        self._prepare_files: tuple[str, ...] = ()
        self._prepare_index = 0
        self._prepare_counts = {"confirmed": 0, "failed": 0, "unresolved": 0}
        self._ownership_revision = 0
        self._last_backend = self._backend()
        self._prepare_backend = None
        self._prepare_revision = 0
        self._prepare_saved = None
        self._prepare_last = (None, None)
        self._registry_generation = 0
        self.loras: tuple[LoRASelection, ...] = ()
        self._receipts: dict[str, str] = {}  # selected LoRA identifier -> local receipt id (never exported)
        self._candidates: list[tuple[str, float | None]] = []
        self._records: dict[str, dict] = {}
        self._bind_after_list: str | None = None  # identifier to bind once the registry list has been refreshed
        self._add_when_bound = False  # a Prepare also adds the LoRA to the list; a plain add never needs to
        page.addLoraButton.clicked.connect(self.add)
        page.removeLoraButton.clicked.connect(self.remove)
        page.checkLorasButton.clicked.connect(self.check)
        page.prepareAssetButton.clicked.connect(self.prepare)
        page.prepareAssetButton.setToolTip(UPLOAD_NOTICE)
        self.sync_backend()
        self.refresh_assets()

    # -- shared helpers --------------------------------------------------------------------------------------------
    @property
    def preparing(self) -> bool:
        return bool(self._prepare_files)

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
        for name in ("prepareAssetButton",):
            getattr(self.page, name).setEnabled(enabled and cloud)

    def sync_backend(self) -> None:
        backend = self._backend()
        if backend != self._last_backend:
            self.invalidate_prepare_ownership()
            self._last_backend = backend
        cloud = self._backend()[0] == "cloud"
        self.set_enabled(not self._is_busy())
        if not cloud:
            self._show(self.page.assetStatusLabel, Presentation("unknown", "Cloud only", CLOUD_ONLY))
        else:
            self.bind_selected()
            self._render_loras()

    def set_inventory(self, snapshot) -> None:
        """Remember raw backend LoRAs until the catalogue controller supplies a compatible candidate set."""
        self._inventory_candidates = (
            [(e.identifier, e.default_weight) for e in snapshot.entries if e.kind == "lora"] if snapshot else []
        )
        self._candidates = list(self._inventory_candidates)

    def set_candidates(self, entries, *, prune_selected: bool = False) -> None:
        """Use compatible catalogue LoRAs, or raw inventory suggestions when the checkpoint is unclassified."""
        if entries is None:
            self._candidates = list(getattr(self, "_inventory_candidates", ()))
            return
        weights = {name: weight for name, weight in getattr(self, "_inventory_candidates", self._candidates)}
        self._candidates = [(entry.identifier, weights.get(entry.identifier)) for entry in entries]
        if prune_selected:
            allowed = {entry.identifier for entry in entries}
            kept = tuple(item for item in self.loras if item.identifier in allowed)
            if kept != self.loras:
                self.set_loras(kept)

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
        self.bind_selected()
        self._render_loras()
        text = (
            "Loaded configuration names LoRA(s). No cloud receipt is bound and compatibility is not checked."
            if loaded and any(item.identifier not in self._receipts for item in self.loras)
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

    def choices(self) -> list[tuple[str, float | None]]:
        """(identifier, catalogue weight) of every LoRA offered for selection, custom-LoRA records included."""
        candidates = list(self._candidates)
        known = {name for name, _ in candidates}
        return candidates + [
            (record["identifier"], None) for record in self._records.values() if record["identifier"] not in known
        ]

    def confirmed_receipts(self) -> dict[str, str]:
        """Identifier -> confirmed local receipt id of the custom LoRAs that can be bound (cloud only)."""
        return {r["identifier"]: asset for asset, r in self._records.items() if r["state"] == "confirmed"}

    def add(self) -> None:
        candidates = self.choices()

        def add_selection(choice):
            if self._is_busy():
                return
            try:
                selection = LoRASelection(*choice)
            except ValidationError as exc:
                self._show(self.page.compatibilityStatusLabel, Presentation("error", "LoRA not added", str(exc)))
                return
            self.set_loras((*[x for x in self.loras if x.identifier != selection.identifier], selection))
            self._auto_bind(selection.identifier)

        choose_searchable(
            self.page,
            title="Add LoRA",
            items=[name for name, _ in candidates],
            custom_label="Add custom identifier",
            custom_prompt="Exact LoRA identifier",
            weights=dict(candidates),
            callback=add_selection,
        )

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
        if self._runner.closed:
            return
        self._registry_generation += 1
        generation = self._registry_generation

        def listed(records, failure):
            if generation == self._registry_generation:
                self._listed(records, failure)

        self._runner.submit(list_assets(self._state), listed)

    def _listed(self, records, failure) -> None:
        status = self.page.assetStatusLabel
        if failure is not None:
            diagnostic_event(
                log, "ui.asset_registry_unavailable", level=logging.WARNING, data={"reason": "read_failed"}
            )
            self._show(status, Presentation("warning", "Registry unavailable", present_asset_failure(failure).detail))
            return
        self._records = {record["asset_id"]: record for record in records}
        # a receipt the registry no longer confirms (reconcile said the server lost the file) must not stay bound
        self._receipts = {
            name: asset
            for name, asset in self._receipts.items()
            if self._records.get(asset, {}).get("state") == "confirmed"
        }
        self._render_loras()
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
        if self._bind_after_list is not None:
            identifier, self._bind_after_list = self._bind_after_list, None
            add, self._add_when_bound = self._add_when_bound, False
            if add and identifier not in {item.identifier for item in self.loras}:
                if any(r["identifier"] == identifier and r["state"] == "confirmed" for r in self._records.values()):
                    self.set_loras((*self.loras, LoRASelection(identifier, DEFAULT_LORA_WEIGHT)))
            self._bind_confirmed(identifier)
        self.bind_selected()

    def _selected_id(self) -> str | None:
        item = self.page.assetList.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def prepare(self) -> None:
        if self.preparing or self._is_busy() or self._runner.closed:
            return
        backend = self._backend()
        if backend[0] != "cloud":
            return
        revision = self._ownership_revision
        folder = self._state
        preference_error = False
        try:
            remembered = load_prepare_folder(self._state)
            if remembered and Path(remembered).is_dir() and os.access(remembered, os.R_OK | os.X_OK):
                folder = Path(remembered)
        except (OSError, ValueError, ValidationError):
            preference_error = True
        chosen = self.choose_asset_file(self.page, folder)
        if isinstance(chosen, str):
            chosen = [chosen] if chosen else []  # retain injected single-file picker compatibility
        if not chosen:
            return
        if preference_error:
            self._prepare_folder_warning("read_failed")
        else:
            try:
                save_prepare_folder(self._state, str(Path(chosen[0]).absolute().parent))
            except (OSError, ValueError, ValidationError):
                self._prepare_folder_warning("write_failed")
        if self._runner.closed or self._is_busy() or self._backend() != backend or revision != self._ownership_revision:
            return  # Cancelled picker or backend/ownership changed while choosing.
        self._prepare_files = tuple(dict.fromkeys(os.path.abspath(path) for path in chosen))
        self._prepare_index = 0
        self._prepare_counts = {"confirmed": 0, "failed": 0, "unresolved": 0}
        self._prepare_backend, self._prepare_revision = backend, revision
        self._prepare_saved = self._saved()
        self._registry_generation += 1  # old list callbacks cannot replace newly prepared receipts
        self._set_busy(True)
        activity_strip(self.page).start(
            "asset", "Preparing cloud assets", detail="May upload each selected file; waiting for receipts."
        )
        self._prepare_next()

    def _prepare_folder_warning(self, reason: str) -> None:
        diagnostic_event(log, "ui.prepare_folder_unavailable", level=logging.WARNING, data={"reason": reason})
        activity_strip(self.page).complete(
            "asset-folder",
            "Prepare folder not remembered",
            "Folder preferences could not be read or saved. Selected files can still be prepared.",
            "warning",
        )

    def invalidate_prepare_ownership(self) -> None:
        """Credential/backend changes stop admission after the currently admitted file settles."""
        self._ownership_revision += 1

    def _prepare_next(self) -> None:
        if self._runner.closed:
            return  # runner/service shutdown owns settlement; never submit another file
        if (
            self._prepare_index >= len(self._prepare_files)
            or self._backend() != self._prepare_backend
            or self._ownership_revision != self._prepare_revision
            or self._saved() is not self._prepare_saved
        ):
            self._finish_prepare()
            return
        number, total = self._prepare_index + 1, len(self._prepare_files)
        detail = f"Preparing {number} of {total}. May upload this file; waiting for receipt."
        self._show(self.page.assetStatusLabel, Presentation("warning", "Preparing", detail))
        activity_strip(self.page).update("asset", 0, 0, detail=detail)
        self._runner.submit(
            prepare_asset(
                self._state, Path(self._prepare_files[self._prepare_index]), provenance="gui", saved=self._prepare_saved
            ),
            self._prepared,
        )

    def _prepared(self, record, failure) -> None:
        if self._prepare_files:
            self._prepare_last = record, failure
            state = record.get("state") if record is not None and failure is None else None
            if failure is not None:
                definite = (
                    isinstance(failure, BackendError) and failure.failure.remote_outcome != RemoteOutcome.UNKNOWN
                ) or isinstance(failure, ValidationError)
                outcome = "failed" if definite else "unresolved"
            else:
                outcome = "confirmed" if state == "confirmed" else "failed" if state == "failed" else "unresolved"
            self._prepare_counts[outcome] += 1
            if outcome != "confirmed":
                diagnostic_event(
                    log,
                    "ui.asset_prepare_incomplete",
                    level=logging.WARNING,
                    data={
                        "outcome": outcome,
                        "reason": "service_error" if failure is not None else "receipt_not_confirmed",
                        "item": self._prepare_index + 1,
                        "total": len(self._prepare_files),
                        "remote_outcome": (
                            failure.failure.remote_outcome.value if isinstance(failure, BackendError) else "unverified"
                        ),
                        "code": failure.failure.code if isinstance(failure, BackendError) else "unclassified",
                    },
                )
            if record is not None and failure is None:
                self._records[record["asset_id"]] = record
                # Single-file completion adds/binds in the existing final registry callback.
                if (
                    state == "confirmed"
                    and len(self._prepare_files) > 1
                    and self._backend() == self._prepare_backend
                    and self._ownership_revision == self._prepare_revision
                ):
                    identifier = record["identifier"]
                    if identifier not in {item.identifier for item in self.loras}:
                        self.loras = (*self.loras, LoRASelection(identifier, DEFAULT_LORA_WEIGHT))
                    self._receipts[identifier] = record["asset_id"]
                    self._render_loras()
            self._prepare_index += 1
            self._prepare_next()
            return
        self._present_prepared(record, failure)

    def _finish_prepare(self) -> None:
        total, done = len(self._prepare_files), self._prepare_index
        self._prepare_files = ()
        self._prepare_saved = None
        self._set_busy(False)
        if total == 1 and done == 1:
            same_owner = self._backend() == self._prepare_backend and self._ownership_revision == self._prepare_revision
            self._present_prepared(*self._prepare_last, bind=same_owner)
            return
        counts = self._prepare_counts
        detail = (
            f"{counts['confirmed']} confirmed, {counts['failed']} failed, "
            f"{counts['unresolved']} unresolved, {total - done} unstarted. No automatic retries."
        )
        state = "success" if counts["confirmed"] == total else "error" if counts["failed"] == total else "partial"
        activity_strip(self.page).complete("asset", "Cloud preparation finished", detail, state)
        level = "ok" if state == "success" else "error" if state == "error" else "warning"
        self._show(self.page.assetStatusLabel, Presentation(level, "Cloud preparation finished", detail))
        self.refresh_assets()

    def _present_prepared(self, record, failure, *, bind: bool = True) -> None:
        self._set_busy(False)
        strip = activity_strip(self.page)
        presentation = (
            present_asset_failure(failure)
            if failure
            else Presentation(
                "ok" if record["state"] == "confirmed" else "warning",
                "Receipt " + record["state"],
                f"{record['identifier']} ({record['evidence']}).",
            )
        )
        strip.complete(
            "asset",
            presentation.headline,
            presentation.detail,
            "partial" if presentation.level == "warning" else "error" if failure else "success",
        )
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
            if bind:
                self._bind_after_list, self._add_when_bound = record["identifier"], True
        self.refresh_assets()

    def _start_reconcile(self, asset_id: str, bind: str | None = None) -> None:
        if self._is_busy() or self._backend()[0] != "cloud":
            return
        self._set_busy(True)
        self._show(self.page.assetStatusLabel, Presentation("warning", "Reconciling", "Metadata check only."))
        self._runner.submit(
            reconcile_asset(self._state, asset_id, saved=self._saved()),
            lambda outcome, failure: self._reconciled(outcome, failure, bind),
        )

    def _reconciled(self, outcome, failure, bind: str | None = None) -> None:
        self._set_busy(False)
        status = self.page.assetStatusLabel
        self._show(status, present_asset_failure(failure) if failure else present_reconciliation(outcome[1]))
        if bind is not None and failure is None:
            self._bind_after_list = bind
        self.refresh_assets()

    def _auto_bind(self, identifier: str) -> None:
        """On cloud, reconcile the receipt of a LoRA that was just added (a metadata check) and bind it if confirmed."""
        if self._backend()[0] != "cloud":
            return
        known = [r for r in self._records.values() if r["identifier"] == identifier and r["state"] != "failed"]
        if known:
            known.sort(key=lambda r: r["state"] != "confirmed")
            self._start_reconcile(known[0]["asset_id"], bind=identifier)

    def bind_selected(self) -> None:
        """Bind the confirmed receipt of every selected LoRA that has none (local registry only, no network)."""
        if self._backend()[0] != "cloud":
            return
        for item in self.loras:
            if item.identifier not in self._receipts:
                self._bind_confirmed(item.identifier, announce=False)

    def _bind_confirmed(self, identifier: str, *, announce: bool = True) -> bool:
        """Bind the confirmed receipt for a selected LoRA identifier; ``True`` when one was found."""
        if identifier not in {item.identifier for item in self.loras}:
            return False
        for asset_id, record in self._records.items():
            if record["identifier"] == identifier and record["state"] == "confirmed":
                self._receipts[identifier] = asset_id
                self._render_loras()
                if announce:
                    self._show(
                        self.page.assetStatusLabel,
                        Presentation("ok", "Receipt bound", f"{identifier} uses its confirmed cloud receipt."),
                    )
                return True
        return False

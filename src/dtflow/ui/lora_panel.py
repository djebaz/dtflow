"""LoRA selection and cloud-asset presentation for the Generate page.

Compatibility diagnostics come from the shared preflight service and receipts from the shared asset service; this
module only collects choices and renders what those services return. The asset list is this machine's local registry,
not DT+ account inventory. Selecting a file through Prepare can upload it without a second confirmation.
"""

import asyncio
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem, QWidget

from dtflow.ui.file_dialogs import QFileDialog
from dtflow.core.models import BackendError, LoRASelection, RemoteOutcome, ResolvedGenerationRequest, ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.logging_setup import development_mode
from dtflow.services.catalogue import Catalogue, FAMILY_LABELS
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
from dtflow.ui.lora_selection_editor import edit_lora_selection

UPLOAD_NOTICE = (
    "Prepare accepts multiple converted LoRA files and may upload each to DT+ if the server does not already have it. "
    "Uploading does not consume credits, but a later generation using this LoRA may."
)
DEFAULT_LORA_WEIGHT = 1.0
CLOUD_ONLY = "Asset actions need the cloud provider (choose it on the Connection page)."
log = logging.getLogger(__name__)


def _choose_asset_file(parent: QWidget, directory: Path) -> list[str]:
    return QFileDialog.getOpenFileNames(parent, "Prepare converted LoRA files", str(directory), "LoRA files (*.ckpt)")[
        0
    ]


def _family_evidence_label(value: str | None, source: str | None, *, conflict: bool = False) -> str:
    """Display the effective architecture's provenance without claiming file verification."""
    if not value:
        return "Unknown (file unclassified)"
    label = FAMILY_LABELS.get(value, value)
    if source == "user":
        return f"{label} (user selected, conflicts with file)" if conflict else f"{label} (user selected)"
    return f"{label} (file)" if source == "file" else f"{label} (unverified)"


class LoraAssetPanel:
    def __init__(
        self,
        page: QWidget,
        *,
        runner: AsyncRunner,
        backend_source: Callable[[], tuple[str, str | None]],
        saved_source: Callable,
        state_dir: Path,
        catalogue: Catalogue | None = None,
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
        self._prepare_issues: list[str] = []
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
        self._records: dict[tuple[str, str], dict] = {}
        self._inventory_families: dict[str, str] = {}
        self._checkpoint_version: str | None = None
        self._catalogue = catalogue if catalogue is not None else Catalogue(self._state)
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
        text = f"{presentation.headline}. {presentation.detail}".strip()
        # A wrapping label is as wide as its longest word: let a file name break after "_", "-" and "." inside it.
        label.setText(re.sub(r"(?<=\w)([_.\-])(?=\w)", "\\1\u200b", text))

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
        self._inventory_families = (
            {
                entry.identifier: entry.architecture
                for entry in snapshot.entries
                if entry.kind == "lora" and entry.architecture
            }
            if snapshot
            else {}
        )
        self._inventory_candidates = (
            [(e.identifier, e.default_weight) for e in snapshot.entries if e.kind == "lora"] if snapshot else []
        )
        self._candidates = list(self._inventory_candidates)

    def set_candidates(self, entries, *, checkpoint_version: str | None = None) -> None:
        """Offer only known-compatible LoRAs; keep selected mismatches visible for correction."""
        self._checkpoint_version = checkpoint_version
        if entries is None:
            self._candidates = list(getattr(self, "_inventory_candidates", ()))
        else:
            weights = {name: weight for name, weight in getattr(self, "_inventory_candidates", self._candidates)}
            self._candidates = [(entry.identifier, weights.get(entry.identifier)) for entry in entries]
        self._render_loras()
        if any(not self._candidate_compatible(item.identifier) for item in self.loras):
            self._show(
                self.page.compatibilityStatusLabel,
                Presentation("warning", "Incompatible LoRA selected", "Remove it or choose a matching checkpoint."),
            )

    def _candidate_family(self, name: str) -> str | None:
        """Prefer the selected receipt, then unambiguous confirmed file evidence, then Echo."""
        receipt = self._receipts.get(name)
        if receipt is not None:
            record = self._records.get((receipt, name))
            if record is not None and record.get("state") == "confirmed":
                return record.get("family")
        versions = {
            record.get("family")
            for record in self._records.values()
            if record["identifier"] == name and record["state"] == "confirmed"
        }
        if versions:
            return next(iter(versions)) if len(versions) == 1 else None
        return self._inventory_families.get(name)

    def _candidate_compatible(self, name: str) -> bool:
        candidate = self._candidate_family(name)
        return self._checkpoint_version is None or candidate is None or candidate == self._checkpoint_version

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

    def family_labels(self) -> dict[str, str]:
        """Family evidence for display only; actual identifiers remain unchanged in requests.

        A user-selected exact version takes precedence for dispatch, while detection
        conflicts remain visible. Differing receipts without a binding stay Unknown.
        """
        family = {
            entry.identifier: f"{FAMILY_LABELS.get(entry.base_model, entry.base_model)} (catalogue)"
            for entry in self._catalogue.lora_definitions()
            if entry.base_model
        }
        for name, value in self._inventory_families.items():
            family.setdefault(name, f"{FAMILY_LABELS.get(value, value)} (provider)")
        records: dict[str, list[dict]] = {}
        for (asset_id, name), record in self._records.items():
            if record.get("state") != "confirmed":
                continue
            if name in self._receipts and self._receipts[name] != asset_id:
                continue
            records.setdefault(name, []).append(record)
        for name, entries in records.items():
            values = {(record.get("family"), record.get("family_source")) for record in entries}
            if len(values) != 1:
                family[name] = "Unknown (conflicting receipts)"
                continue
            value, source = next(iter(values))
            family[name] = _family_evidence_label(
                value, source, conflict=any(record.get("architecture_conflict") for record in entries)
            )
        return family

    def _render_loras(self) -> None:
        labels = self.family_labels()
        self.page.loraList.clear()
        for item in self.loras:
            text = f"{item.identifier} @ {item.weight} · {labels.get(item.identifier, 'Unknown')}"
            if not self._candidate_compatible(item.identifier):
                text += f" · incompatible with {self._checkpoint_version}"
            if item.identifier in self._receipts:
                text += f" [receipt {self._receipts[item.identifier][:8]}]"
            entry = QListWidgetItem(text)
            entry.setToolTip(text)  # a long name is elided in the narrow rail
            self.page.loraList.addItem(entry)

    def choices(self) -> list[tuple[str, float | None]]:
        """(identifier, catalogue weight) of every LoRA offered for selection, custom-LoRA records included."""
        candidates = [(name, weight) for name, weight in self._candidates if self._candidate_compatible(name)]
        known = {name for name, _ in candidates}
        for record in self._records.values():
            name = record["identifier"]
            if name not in known and self._candidate_compatible(name):
                candidates.append((name, None))
                known.add(name)
        return candidates

    def all_choices(self) -> list[tuple[str, float | None]]:
        """Include every provider and local LoRA for multi-checkpoint queue composition."""
        listed = list(getattr(self, "_inventory_candidates", ()))
        known = {name for name, _ in listed}
        for name, weight in self._candidates:
            if name not in known:
                listed.append((name, weight))
                known.add(name)
        for record in self._records.values():
            name = record["identifier"]
            if name not in known:
                listed.append((name, None))
                known.add(name)
        return listed

    def confirmed_receipts(self) -> dict[str, str]:
        """Identifier -> confirmed local receipt id of the custom LoRAs that can be bound (cloud only)."""
        return {
            record["identifier"]: record["asset_id"]
            for record in self._records.values()
            if record["state"] == "confirmed"
        }

    def add(self) -> None:
        """Edit the whole selection: check, uncheck and weigh every offered LoRA, then apply once."""
        candidates = self.choices()

        def apply_selection(chosen):
            if self._is_busy():
                return
            try:
                selection = tuple(LoRASelection(name, weight) for name, weight in chosen)
            except ValidationError as exc:
                self._show(self.page.compatibilityStatusLabel, Presentation("error", "LoRAs not changed", str(exc)))
                return
            before = {item.identifier for item in self.loras}
            self.set_loras(selection)
            for item in selection:
                if item.identifier not in before:
                    self._auto_bind(item.identifier)

        edit_lora_selection(
            self.page,
            candidates=[name for name, _ in candidates],
            selected=self.loras,
            suggestions=dict(candidates),
            families=self.family_labels(),
            callback=apply_selection,
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

    def show_checkpoint_architecture_unknown(self) -> None:
        """Warn when selected LoRAs have no exact checkpoint version to compare against."""
        if self.loras:
            self._show(
                self.page.compatibilityStatusLabel,
                Presentation(
                    "warning",
                    "Checkpoint architecture unknown",
                    "DTFlow cannot verify whether the selected LoRAs match this checkpoint. "
                    "Prepared LoRAs with unknown architectures are rejected.",
                ),
            )

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

        self._runner.submit(list_assets(self._state, named=True), listed)

    def _listed(self, records, failure) -> None:
        status = self.page.assetStatusLabel
        if failure is not None:
            diagnostic_event(
                log, "ui.asset_registry_unavailable", level=logging.WARNING, data={"reason": "read_failed"}
            )
            self._show(status, Presentation("warning", "Registry unavailable", present_asset_failure(failure).detail))
            return
        self._records = {(record["asset_id"], record["identifier"]): record for record in records}
        # a receipt the registry no longer confirms (reconcile said the server lost the file) must not stay bound
        self._receipts = {
            name: asset
            for name, asset in self._receipts.items()
            if self._records.get((asset, name), {}).get("state") == "confirmed"
        }
        self._render_loras()
        selected = self._selected_id()
        self.page.assetList.clear()
        for record in records:
            value = record.get("family")
            source = record.get("family_source")
            family = _family_evidence_label(value, source, conflict=bool(record.get("architecture_conflict")))
            text = (
                f"{record['identifier']} · {family} · "
                f"{record['state']} ({record['evidence']}) · {record['sha256'][:8]} · {record['size']} B"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (record["asset_id"], record["identifier"]))  # Qt.UserRole
            evidence = (
                f"\nDetected: {record.get('detected_version') or 'Unknown'}"
                f"\nUser selected: {record.get('declared_version') or 'None'}"
            )
            item.setToolTip(
                f"LoRA architecture: {family}{evidence}\nReceipt {record['asset_id']} on {record['origin']}"
            )
            self.page.assetList.addItem(item)
            if (record["asset_id"], record["identifier"]) == selected:
                self.page.assetList.setCurrentItem(item)
        if self._bind_after_list is not None:
            identifier, self._bind_after_list = self._bind_after_list, None
            add, self._add_when_bound = self._add_when_bound, False
            if add and identifier not in {item.identifier for item in self.loras}:
                if any(r["identifier"] == identifier and r["state"] == "confirmed" for r in self._records.values()):
                    self.set_loras((*self.loras, LoRASelection(identifier, DEFAULT_LORA_WEIGHT)))
            self._bind_confirmed(identifier)
        self.bind_selected()

    def _selected_id(self) -> tuple[str, str] | None:
        item = self.page.assetList.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def prepare(self) -> None:
        if self.preparing or self._is_busy() or self._runner.closed:
            return
        backend = self._backend()
        if backend[0] != "cloud":
            return
        revision = self._ownership_revision
        # The shared file dialog opens at the folder remembered for this picker, else at the state directory.
        chosen = self.choose_asset_file(self.page, self._state)
        if isinstance(chosen, str):
            chosen = [chosen] if chosen else []  # retain injected single-file picker compatibility
        if not chosen:
            return
        if self._runner.closed or self._is_busy() or self._backend() != backend or revision != self._ownership_revision:
            return  # Cancelled picker or backend/ownership changed while choosing.
        self._prepare_files = tuple(dict.fromkeys(os.path.abspath(path) for path in chosen))
        self._prepare_index = 0
        self._prepare_counts = {"confirmed": 0, "failed": 0, "unresolved": 0}
        self._prepare_issues = []
        self._prepare_backend, self._prepare_revision = backend, revision
        self._prepare_saved = self._saved()
        self._registry_generation += 1  # old list callbacks cannot replace newly prepared receipts
        self._set_busy(True)
        activity_strip(self.page).start(
            "asset", "Preparing cloud assets", detail="May upload each selected file; waiting for receipts."
        )
        self._prepare_next()

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
                explanation = (
                    present_asset_failure(failure)
                    if failure is not None
                    else Presentation(
                        "warning", "Receipt not confirmed",
                        f"State: {state or 'unavailable'}. Review the local receipt before trying again.",
                    )
                )
                if len(self._prepare_issues) < 8:
                    filename = Path(self._prepare_files[self._prepare_index]).name
                    self._prepare_issues.append(
                        f"{self._prepare_index + 1}/{len(self._prepare_files)} {filename}: {explanation.detail}"
                    )
                data = {
                    "outcome": outcome,
                    "reason": "service_error" if failure is not None else "receipt_not_confirmed",
                    "item": self._prepare_index + 1,
                    "total": len(self._prepare_files),
                    "remote_outcome": (
                        failure.failure.remote_outcome.value if isinstance(failure, BackendError) else "unverified"
                    ),
                    "code": (
                        failure.failure.code if isinstance(failure, BackendError)
                        else type(failure).__name__ if failure is not None
                        else state or "receipt_not_confirmed"
                    ),
                }
                if development_mode():
                    # Full per-file evidence stays in dev logs, without full paths.
                    data.update(
                        filename=Path(self._prepare_files[self._prepare_index]).name,
                        error_headline=explanation.headline,
                        error_detail=explanation.detail,
                    )
                diagnostic_event(
                    log,
                    "ui.asset_prepare_incomplete",
                    level=logging.WARNING,
                    data=data,
                    # The existing sanitized diagnostic pipeline owns traceback
                    # projection. Do not capture arbitrary provider exceptions.
                    exc_info=(
                        (type(failure), failure, failure.__traceback__)
                        if development_mode() and isinstance(failure, ValidationError)
                        else False
                    ),
                )
            if record is not None and failure is None:
                self._records[(record["asset_id"], record["identifier"])] = record
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
        if self._prepare_issues:
            detail += "\n" + "\n".join(self._prepare_issues)
            unseen = counts["failed"] + counts["unresolved"] - len(self._prepare_issues)
            if unseen:
                detail += f"\n{unseen} more unsuccessful file(s); see -x Logs for all details."
        state = "error" if counts["failed"] else "success" if counts["confirmed"] == total else "partial"
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
                    f"{record['identifier']} · {self.family_labels().get(record['identifier'], 'Unknown')} "
                    f"({record['evidence']}).",
                ),
            )
            if bind:
                self._bind_after_list, self._add_when_bound = record["identifier"], True
        self.refresh_assets()

    def _start_reconcile(self, asset_id: str, bind: str | None = None) -> None:
        if self._is_busy() or self._backend()[0] != "cloud":
            return
        # A prior async asset-list refresh must not rebind an older confirmed receipt
        # after this fresh check establishes that the selected key has lost it.
        self._registry_generation += 1
        self._bind_after_list = None
        self._set_busy(True)
        self._show(self.page.assetStatusLabel, Presentation("warning", "Reconciling", "Metadata check only."))
        self._runner.submit(
            reconcile_asset(self._state, asset_id, identifier=bind, saved=self._saved()),
            lambda outcome, failure: self._reconciled(outcome, failure, bind),
        )

    def _reconciled(self, outcome, failure, bind: str | None = None) -> None:
        self._set_busy(False)
        status = self.page.assetStatusLabel
        self._show(status, present_asset_failure(failure) if failure else present_reconciliation(outcome[1]))
        if bind is not None and failure is None and outcome[1].status == "confirmed":
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
        for (asset_id, name), record in self._records.items():
            if name == identifier and record["state"] == "confirmed":
                self._receipts[identifier] = asset_id
                self._render_loras()
                if announce:
                    self._show(
                        self.page.assetStatusLabel,
                        Presentation("ok", "Receipt bound", f"{identifier} uses its confirmed cloud receipt."),
                    )
                return True
        return False

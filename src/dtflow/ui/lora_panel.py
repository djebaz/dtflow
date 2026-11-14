"""LoRA selection and cloud-asset presentation for the Generate page.

Compatibility diagnostics come from the shared preflight service and receipts from the shared asset service; this
module only collects choices and renders what those services return. The asset list is this machine's local registry,
not DT+ account inventory. Selecting a file through Prepare can upload it without a second confirmation.
"""

import asyncio
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QListWidgetItem, QWidget
from shiboken6 import isValid

from dtflow.core.models import (
    BackendError,
    LoRASelection,
    NameAlreadyAcceptedError,
    RemoteOutcome,
    ResolvedGenerationRequest,
    ValidationError,
)
from dtflow.diagnostics import asset_ref_data, event as diagnostic_event
from dtflow.logging_setup import development_mode
from dtflow.services.catalogue import Catalogue, FAMILY_LABELS, bundled_exact_version
from dtflow.core.models import KeyHoldings
from dtflow.services.dt_storage import reported_free_space, storage_refusal, storage_size
from dtflow.services.storage_probe import probe_content
from dtflow.services.connection import (
    Presentation,
    present_asset_failure,
    present_generation_failure,
    present_preflight,
    present_reconciliation,
)
from dtflow.services.lora_convert import COLAB_HINT, ConversionError, ConversionProgress, supported_families
from dtflow.services.lora_convert.detect import inspect_source
from dtflow.services.lora_convert.errors import UnsupportedFamilyError
from dtflow.services.lora_convert_prepare import convert_for_prepare
from dtflow.services.lora_digest import DigestCancelled, hash_file
from dtflow.services.lora_family import DETECTABLE_FAMILIES, LoraFamily, detect_lora_family, validate_converted_lora
from dtflow.services.lora_naming import LoraPrepareMetadata, display_label
from dtflow.services.lora_store import LoraStore
from dtflow.services.prepare_progress import (
    CONFIRMING,
    CONVERTING,
    EXISTS,
    UPLOADING,
    PrepareEvent,
    PrepareProgress,
)
from dtflow.services.session import (
    accepted_managed_identifier,
    check_loras,
    list_assets,
    occupied_managed_lora_names,
    saved_lora_trigger_words,
    prepare_asset,
    reconcile_asset,
    selection_for,
    storage_listing,
)
from dtflow.ui.activity_bar import clock
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.file_dialogs import QFileDialog
from dtflow.ui.latest_relay import LatestRelay
from dtflow.ui.lora_selection_editor import _display_label, edit_lora_selection
from dtflow.ui.prepare_lora_editor import PrepareBatchEditor
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.source_details_view import safe_tooltip

UPLOAD_NOTICE = (
    "Select converted .ckpt or source .safetensors LoRAs, review metadata and conversion family, "
    "then explicitly Prepare ready rows. "
    "Preparing may upload to DT+ without consuming credits; later generation may consume credits."
)
DEFAULT_LORA_WEIGHT = 1.0
CLOUD_ONLY = "Asset actions need the cloud provider (choose it on the Connection page)."
log = logging.getLogger(__name__)


def _choose_asset_file(parent: QWidget, directory: Path) -> list[str]:
    return QFileDialog.getOpenFileNames(
        parent, "Prepare LoRA files", str(directory), "LoRA files (*.ckpt *.safetensors)"
    )[0]


def _family_evidence_label(value: str | None, source: str | None, *, conflict: bool = False) -> str:
    """Display the effective architecture's provenance without claiming file verification."""
    if not value:
        return "Unknown (file unclassified)"
    label = FAMILY_LABELS.get(value, value)
    if source == "user":
        return f"{label} (user selected, conflicts with file)" if conflict else f"{label} (user selected)"
    return f"{label} (file)" if source == "file" else f"{label} (unverified)"


def _short_reason(reason: str) -> str:
    """One concise sentence of a failure: no tensor-name examples, no command hints, no Colab hint (shown once)."""
    reason = reason.replace(COLAB_HINT, "")
    reason = re.sub(r"\s*\(for example [^)]*\)", "", reason)
    reason = re.sub(r"\s*Pass --\S.*?(?=\.\s|$)(\.)?", "", reason).strip()
    reason = re.sub(r"\s*,?\s*or\s*$", "", reason).strip()
    return reason if reason.endswith((".", "!", "?")) else reason + "."


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
        self.open_storage: Callable[..., None] = lambda **_: None  # the window mounts the shared overlay
        self.refresh_holdings: Callable[[], None] = lambda: None  # the window re-reads the key's stored list
        self._holdings: KeyHoldings | None = None
        self._prepare_storage_revision = 0
        self._prepare_files: tuple[str, ...] = ()
        self._prepare_metadata: dict[str, LoraPrepareMetadata] = {}
        self._prepare_source_sha: dict[str, str] = {}
        self._prepare_editor: PrepareBatchEditor | None = None
        self._prepare_editor_context = None
        self._hash_paused: tuple | None = None
        self._prepare_index = 0
        self._prepare_counts = {"confirmed": 0, "failed": 0, "unresolved": 0}
        self._inspecting = False
        self._prepare_final: dict[str, str] = {}  # last outcome per distinct file in this run
        self._prepare_issues: list[tuple[str, str, str]] = []
        self._ownership_revision = 0
        self._last_backend = self._backend()
        self._prepare_backend = None
        self._prepare_revision = 0
        self._prepare_saved = None
        self._prepare_last = (None, None)
        self._prepare_progress: PrepareProgress | None = None
        self._prepare_relay: LatestRelay | None = None
        self._prepare_stop = False  # "Stop after current file": nothing new starts once set
        self._prepare_convert_cancel = threading.Event()
        runner.shutdown_started.connect(self._prepare_convert_cancel.set)
        self._prepare_rendered = 0.0
        self._prepare_editor_text, self._prepare_editor_at = "", 0.0
        self._prepare_timer = QTimer(page)  # keeps elapsed, ETA and the stall state moving between byte reports
        self._prepare_timer.setInterval(500)
        self._prepare_timer.timeout.connect(self._render_prepare)
        self._registry_generation = 0
        self.loras: tuple[LoRASelection, ...] = ()
        self._receipts: dict[str, str] = {}  # selected LoRA identifier -> local receipt id (never exported)
        self._candidates: list[tuple[str, float | None]] = []
        self._records: dict[tuple[str, str], dict] = {}
        self._inventory_families: dict[str, str] = {}
        self._inventory_checkpoints: tuple[str, ...] = ()
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
    def _show(label, presentation: Presentation, tooltip: str = "") -> None:
        label.setProperty("state", presentation.level)
        label.style().unpolish(label)
        label.style().polish(label)
        text = f"{presentation.headline}. {presentation.detail}".strip()
        # A wrapping label is as wide as its longest word: let a file name break after "_", "-" and "." inside it.
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setText(re.sub(r"(?<=\w)([_.\-])(?=\w)", "\\1\u200b", text))
        label.setToolTip(safe_tooltip(tooltip))

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
        self._inventory_checkpoints = (
            tuple(entry.identifier for entry in snapshot.entries if entry.kind == "model") if snapshot else ()
        )
        self._inventory_candidates = (
            [(e.identifier, e.default_weight) for e in snapshot.entries if e.kind == "lora"] if snapshot else []
        )
        self._candidates = list(self._inventory_candidates)

    def eligible_architectures(self) -> tuple[str, ...]:
        """Exact architectures offered for a LoRA: everything DTFlow's file detector can recognise plus what the
        active backend lists. A filename or broad catalogue label is never evidence."""
        versions = [*DETECTABLE_FAMILIES, *self._inventory_families.values()]
        versions += [
            version
            for identifier in self._inventory_checkpoints
            if (version := bundled_exact_version(identifier)) is not None
        ]
        return tuple(dict.fromkeys(versions))

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

    # -- DT+ key holdings (hints only) -------------------------------------------------------------------------------
    def set_holdings(self, holdings: KeyHoldings | None) -> None:
        """Latest comparison of this key's receipts with DT+'s stored list; None when unknown (not cloud, unread)."""
        self._holdings = holdings
        if holdings is not None and holdings.marked_stale and not self._runner.closed:
            self.refresh_assets()  # receipts just marked stale must stop looking confirmed here

    def storage_marks(self) -> dict[str, str]:
        """Picker hint per custom LoRA: held by the key, or would need a re-attach. Nothing is hidden or blocked."""
        holdings = self._holdings
        if holdings is None or self._backend()[0] != "cloud":
            return {}
        marks = {}
        for name in {record["identifier"] for record in self._records.values()}:
            if name in holdings.held:
                marks[name] = "on DT+ key" if holdings.checked else "receipt on key"
            else:
                marks[name] = "needs re-attach"
        return marks

    def missing_size(self) -> int:
        """Total size of the selected custom LoRAs this key is not known to hold (what a re-attach could cost)."""
        holdings = self._holdings
        if holdings is None:
            return 0
        sizes = {record["identifier"]: record["size"] for record in self._records.values()}
        return sum(sizes.get(item.identifier, 0) for item in self.loras if item.identifier not in holdings.held)

    def offer_storage(self, failure) -> bool:
        """After a storage refusal: show what was needed and offer the storage panel. Nothing is deleted here."""
        if not storage_refusal(failure):
            return False
        needed = self.missing_size() or None
        free = reported_free_space(failure)
        text = (
            f"Needs about {storage_size(needed)}; "
            f"{storage_size(free) if free is not None else 'unknown free space'} on this DT+ key. "
            "Open DT+ storage to choose what to delete. Nothing is deleted without your confirmation."
            if needed
            else "Open DT+ storage to choose what to delete. Nothing is deleted without your confirmation."
        )
        feedback = self.page.window()._inline_feedback
        if feedback.active():
            return False  # never replace an editor or pending consent
        feedback.show("DT+ storage is full", text, "warning")
        feedback.set_accept("Open DT+ storage", lambda: (feedback.dismiss(), self.open_storage(needed=needed)))
        return True

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

    def refresh_labels(self) -> None:
        """Redraw the selection after the saved short names changed; identifiers and weights are untouched."""
        self._render_loras()

    def _render_loras(self) -> None:
        labels = self.family_labels()
        self.page.loraList.clear()
        for item in self.loras:
            text = f"{_display_label(item.identifier)} @ {item.weight:g}"
            if not self._candidate_compatible(item.identifier):
                text += f" · incompatible with {self._checkpoint_version}"
            entry = QListWidgetItem(text)
            detail = (
                f"{item.identifier}\nWeight: {item.weight:g}\nArchitecture: {labels.get(item.identifier, 'Unknown')}"
            )
            if item.identifier in self._receipts:
                detail += f"\nConfirmed receipt: {self._receipts[item.identifier]}"
            entry.setToolTip(detail)  # precise identifiers remain available on hover
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
            marks=self.storage_marks(),
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
        refresh_hints = getattr(self.page.window(), "_lora_hint_refresh", None)
        if refresh_hints is not None:
            refresh_hints()
        self.page.assetList.clear()
        for record in records:
            value = record.get("family")
            source = record.get("family_source")
            family = _family_evidence_label(value, source, conflict=bool(record.get("architecture_conflict")))
            text = (
                f"{display_label(record['identifier'])} · {family} · "
                f"{record['state']} ({record['evidence']}) · {record['sha256'][:8]} · {record['size']} B"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (record["asset_id"], record["identifier"]))  # Qt.UserRole
            evidence = (
                f"\nDetected: {record.get('detected_version') or 'Unknown'}"
                f"\nUser selected: {record.get('declared_version') or 'None'}"
            )
            item.setToolTip(
                f"{record['identifier']}\nLoRA architecture: {family}{evidence}\nReceipt {record['asset_id']} on {record['origin']}"
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
        """File selection opens the actual in-window metadata editor; no upload occurs yet."""
        if self.preparing or self._is_busy() or self._runner.closed:
            return
        backend = self._backend()
        if backend[0] != "cloud":
            return
        revision = self._ownership_revision
        chosen = self.choose_asset_file(self.page, self._state)
        if isinstance(chosen, str):
            chosen = [chosen] if chosen else []
        if not chosen:
            return
        if self._runner.closed or self._is_busy() or self._backend() != backend or revision != self._ownership_revision:
            return
        # One row per file: spellings that differ only by case, a \\?\ prefix or a symlink are the same file.
        unique: dict[str, str] = {}
        for path in chosen:
            unique.setdefault(os.path.normcase(os.path.realpath(path)), os.path.abspath(path))
        paths = tuple(unique.values())
        if self._prepare_editor is not None:
            self._prepare_editor.cancel_hashing()  # a replaced editor must not keep reading files
        holder: list[PrepareBatchEditor] = []
        self._prepare_editor = PrepareBatchEditor(
            self.page,
            paths,
            on_prepare=self._authorize_prepare,
            on_inspect_progress=lambda *report: self._inspect_progress(holder[0] if holder else None, *report),
            on_storage=self._open_prepare_storage,
        )
        editor = self._prepare_editor
        holder.append(editor)
        # One progress model from opening the editor to the end of the run: inspection is its first phase.
        if self._inspecting:
            activity_strip(self.page).finish("asset")
            self._inspecting = False
        self._prepare_progress = PrepareProgress()
        self._prepare_editor_context = (backend, revision, self._saved())
        self.refresh_prepare_storage()
        # The provider's current LoRA inventory is the only authority for selectable
        # architectures. A catalogue label or filename is never support evidence.
        eligible = self.eligible_architectures()
        self._runner.submit(
            self._inspect_prepare_files(paths),
            lambda result, failure: self._inspected_prepare_editor(editor, eligible, result, failure),
        )
        self._hash_next(editor, paths, 0)

    def _inspect_progress(self, editor, done_bytes: int | None, total_bytes: int, finished: int, files: int) -> None:
        """Inspection of the selected files as the first phase of the global Prepare progress, in the shared bottom
        bar (``done_bytes=None``: inspection is over, or its editor was closed or dismissed)."""
        if self._prepare_editor is not None and editor is not self._prepare_editor:
            return  # a report of a replaced editor
        if not isValid(self.page):
            return  # the window is being torn down; the editor's last report arrives after the page is gone
        strip = activity_strip(self.page)
        if strip is None:
            return
        if done_bytes is None:
            if self._prepare_progress is not None:
                self._prepare_progress.end_inspection()
            if self._inspecting:
                self._inspecting = False
                if not self.preparing:
                    strip.finish("asset")
            if not self.preparing and (editor is None or not editor.active()):
                self._end_prepare_progress()  # closed without preparing: the model ends with the editor
            return
        progress = self._prepare_progress
        if progress is None or self.preparing:
            return  # the run reports itself; files still being read are already counted per file
        progress.inspect(done_bytes, total_bytes, finished, files)
        if not self._inspecting:
            self._inspecting = True
            strip.start(
                "asset", "Preparing cloud assets", detail=f"Reading {files} selected file(s); nothing is uploaded."
            )
        self._render_prepare()

    def _hash_next(self, editor, paths: tuple[str, ...], index: int) -> None:
        """Hash the selected files one after another in the background; Prepare reuses the remembered digests."""
        if index >= len(paths) or self._runner.closed:
            return
        if not editor.active() or editor is not self._prepare_editor:
            editor.cancel_hashing()  # closed, dismissed or replaced: stop reading and end the bar job
            return
        if self.preparing:
            # A run is reading and uploading: do not compete with it for the disk. Resumed when it ends.
            self._hash_paused = (editor, paths, index)
            self._inspect_progress(editor, None, 0, 0, len(paths))
            return
        path = Path(paths[index])

        async def hashing():
            sha, size = await asyncio.to_thread(
                hash_file, path, editor.hash_cancelled.is_set, lambda n: editor.note_read(path, n)
            )
            accepted = None
            if path.suffix.casefold() == ".ckpt":
                try:
                    accepted = await accepted_managed_identifier(self._state, sha, size)
                except Exception:  # an unreadable registry must not discard a good content hash
                    pass
            return sha, size, accepted

        def done(result, failure) -> None:
            if isinstance(failure, DigestCancelled) or self._runner.closed:
                editor.cancel_hashing()
                return
            if editor.active():
                sha, size, accepted = result if failure is None else (None, None, None)
                editor.set_identity(path, sha, size, accepted)
            else:
                editor.cancel_hashing()
            self._hash_next(editor, paths, index + 1)

        self._runner.submit(hashing(), done)

    async def _inspect_prepare_files(self, paths: tuple[str, ...]):
        """Inspect file headers off the GUI thread and read existing canonical name reservations."""

        def inspect() -> tuple[tuple[LoraFamily, ...], tuple, tuple[str | None, ...]]:
            evidence: list[LoraFamily] = []
            detections: list = []
            descriptive: list = []
            reasons: list[str | None] = []
            for item in paths:
                path = Path(item)
                if path.suffix.casefold() == ".safetensors":
                    # Header-only structural detection (#402); conversion itself still validates the source.
                    inspected = inspect_source(path)  # one header read: structure and descriptive metadata
                    detection = inspected.detection
                    evidence.append(LoraFamily())
                    detections.append(detection)
                    descriptive.append(inspected.metadata)  # display and suggestions only (#414)
                    reasons.append(detection.reason if detection.outcome == "unreadable" else None)
                    continue
                evidence.append(detect_lora_family(path))
                detections.append(None)
                descriptive.append(None)
                try:
                    # The same container check Prepare runs later: a non-LoRA is flagged now, not after Prepare.
                    validate_converted_lora(path)
                    reasons.append(None)
                except ValidationError as exc:
                    reasons.append(str(exc))
            return tuple(evidence), tuple(detections), tuple(reasons), tuple(descriptive)

        evidence, detections, invalid, descriptive = await asyncio.to_thread(inspect)
        occupied = await occupied_managed_lora_names(self._state)
        conversions = await asyncio.to_thread(lambda: LoraStore(self._state).entries())
        converter_families = await asyncio.to_thread(supported_families)
        try:
            saved_triggers = await saved_lora_trigger_words(self._state)
            saved_unreadable = False
        except Exception:  # Prepare still works; but then no suggestion may replace what might be a saved value
            saved_triggers, saved_unreadable = {}, True
        return (
            evidence,
            occupied,
            invalid,
            conversions,
            converter_families,
            detections,
            descriptive,
            saved_triggers,
            saved_unreadable,
        )

    def _inspected_prepare_editor(self, editor, eligible, result, failure) -> None:
        if self._runner.closed or not editor.active() or editor is not self._prepare_editor:
            return
        if failure is not None:
            self._show(
                self.page.assetStatusLabel,
                Presentation("error", "Could not inspect selected LoRAs", str(failure)),
            )
            return
        if result is not None:
            context = self._prepare_editor_context
            if not self._owns_prepare_context(context):
                return  # a late advisory/inspection result belongs to another owner
            evidence, occupied, invalid, conversions, converter_families, detections, *extra = result
            descriptive, saved_triggers, saved_unreadable = extra[:3] if len(extra) >= 3 else ((), {}, False)
            editor.set_inspection(
                evidence,
                eligible,
                occupied,
                invalid,
                conversions,
                converter_families,
                detections,
                source_metadata=descriptive,
                saved_triggers=saved_triggers,
                saved_triggers_unreadable=saved_unreadable,
            )

    def _owns_prepare_context(self, context) -> bool:
        return (
            context is not None
            and self._backend()[0] == "cloud"
            and context[0] == self._backend()
            and context[1] == self._ownership_revision
            and context[2] is self._saved()
        )

    def _open_prepare_storage(self) -> None:
        context = self._prepare_editor_context
        if self._runner.closed or self.preparing or self._is_busy() or not self._owns_prepare_context(context):
            return
        self.open_storage()

    def refresh_prepare_storage(self) -> None:
        """Refresh an open editor after closing storage, never retry its failed rows."""
        editor, context = self._prepare_editor, self._prepare_editor_context
        if (
            self._runner.closed
            or self.preparing
            or self._is_busy()
            or editor is None
            or not editor.active()
            or not self._owns_prepare_context(context)
        ):
            return
        self._prepare_storage_revision += 1
        revision = self._prepare_storage_revision

        def listed(listing, failure):
            if (
                self._runner.closed
                or revision != self._prepare_storage_revision
                or editor is not self._prepare_editor
                or not editor.active()
                or not self._owns_prepare_context(context)
            ):
                return
            if failure is not None:
                diagnostic_event(log, "assets.prepare_storage_unavailable", data={"count": len(editor.rows)})
            editor.set_storage(
                listing.free_space if listing and failure is None else None,
                listing.files if listing and failure is None else (),
            )

        self._runner.submit(storage_listing(self._state, probe_content(), saved=context[2]), listed)

    def _authorize_prepare(self, chosen: list[tuple[Path, LoraPrepareMetadata]]) -> None:
        """Admit only explicitly approved, metadata-complete rows under the existing owner."""
        if not chosen or self._runner.closed:
            return
        backend = self._backend()
        editor_context = self._prepare_editor_context
        if (
            backend[0] != "cloud"
            or editor_context is None
            or backend != editor_context[0]
            or self._ownership_revision != editor_context[1]
            or self._saved() is not editor_context[2]
            or (self._is_busy() and not self.preparing)
        ):
            return
        if self.preparing and (
            backend != self._prepare_backend
            or self._ownership_revision != self._prepare_revision
            or self._saved() is not self._prepare_saved
        ):
            return
        if self._prepare_editor is not None:
            chosen = self._prepare_editor.prioritize_held(chosen)
        # A file that already settled may be sent again by an explicit Re-Prepare; one still queued may not.
        queued = set(self._prepare_files[self._prepare_index :]) if self.preparing else set()
        selected = {name: metadata for path, metadata in chosen if (name := str(path)) not in queued}
        if not selected:
            return
        editor = self._prepare_editor
        if editor is not None:
            self._prepare_source_sha.update(
                {str(row.path): row.sha256 for row in editor.rows if str(row.path) in selected and row.sha256}
            )
        if self.preparing:
            self._prepare_files += tuple(selected)
            self._prepare_metadata.update(selected)
            self._track_prepare_files(selected)
        else:
            self._prepare_files = tuple(selected)
            self._prepare_metadata = dict(selected)
            self._prepare_index = 0
            self._prepare_counts = {"confirmed": 0, "failed": 0, "unresolved": 0}
            self._prepare_final = {}
            self._prepare_issues = []
            self._prepare_backend, self._prepare_revision = backend, self._ownership_revision
            self._prepare_saved = self._saved()
            self._registry_generation += 1
            self._set_busy(True)
            self._prepare_stop = False
            self._prepare_convert_cancel.clear()
            self._inspecting = False
            if self._prepare_progress is None:
                self._prepare_progress = PrepareProgress()
            self._prepare_progress.begin_run()
            self._track_prepare_files(selected)
            activity_strip(self.page).start(
                "asset",
                "Preparing cloud assets",
                stop=self._request_prepare_stop,
                stop_text="Stop after current file",
                detail="Only approved ready rows were admitted.",
            )
            self._prepare_timer.start()
            self._prepare_next()
        if self._prepare_editor is not None:
            self._prepare_editor.admitted([Path(p) for p in selected])
            self._prepare_editor.form.prepareStorageButton.setEnabled(False)

    def _track_prepare_files(self, selected) -> None:
        """Register admitted files with the progress model, in the same order as ``_prepare_files``."""
        for name in selected:
            try:
                size = Path(name).stat().st_size
            except OSError:
                size = 0
            self._prepare_progress.add_file(name, size, convert=Path(name).suffix.casefold() == ".safetensors")

    def _request_prepare_stop(self) -> None:
        """Stop after the current file: it settles normally (no half-finished upload); later files never start."""
        self._prepare_stop = True

    def _on_prepare_event(self, index: int, event: PrepareEvent) -> None:
        progress = self._prepare_progress
        if progress is None:
            return
        progress.event(index, event)
        editor = self._prepare_editor
        if editor is not None and event.name and index < len(self._prepare_files):
            editor.prepared_name(Path(self._prepare_files[index]), event.name)
        if (
            event.stage in (EXISTS, UPLOADING, CONFIRMING)
            and self._prepare_editor is not None
            and index < len(self._prepare_files)
        ):
            # Any later stage proves the registry accepted this row's name: Re-Prepare cannot be needed any more.
            # The relay is latest-wins, so a fast file may skip stages and a failure may come before any later one;
            # that path never sets ``checked`` and relies on ``row.outcome`` in ``PrepareBatchEditor.all_checked``.
            self._prepare_editor.checked(Path(self._prepare_files[index]))
            self._release_prepare_editor_if_all_checked()
        if time.monotonic() - self._prepare_rendered >= 0.1:
            self._render_prepare()

    def _render_prepare(self) -> None:
        progress = self._prepare_progress
        if progress is None or not (self.preparing or self._inspecting):
            return
        self._prepare_rendered = time.monotonic()
        snap = progress.snapshot()
        strip = activity_strip(self.page)
        if not self.preparing:
            strip.update(
                "asset",
                int(snap.fraction * 1000) if snap.fraction is not None else 0,
                1000 if snap.fraction is not None else 0,
                detail=f"{snap.wide}; nothing is uploaded.",
                elapsed=clock(snap.elapsed),
                stage=snap.stage,
                wide=snap.wide,
                count=snap.count,
            )
            return
        total = len(self._prepare_files)
        known = snap.fraction is not None
        eta = ""
        if snap.eta is not None:
            seconds = snap.eta if snap.eta < 60 else 5 * round(snap.eta / 5)
            eta = f"~{clock(seconds)} left"
        detail = f"Preparing {min(self._prepare_index + 1, total)} of {total}. {snap.wide}".strip()
        editor = self._prepare_editor
        if self._prepare_index < total and snap.stage:
            # The existing bar, selected editor row and status label use one progress snapshot.
            # Widget updates are throttled; the bottom bar itself updates on each event.
            summary = f"Preparing {self._prepare_index + 1} of {total} · {snap.stage}" + (f" · {eta}" if eta else "")
            now = time.monotonic()
            if summary != self._prepare_editor_text and now - self._prepare_editor_at >= 1.0:
                self._prepare_editor_text, self._prepare_editor_at = summary, now
                if editor is not None and editor.active():
                    editor.progress(Path(self._prepare_files[self._prepare_index]), snap.stage, summary)
                self._show(self.page.assetStatusLabel, Presentation("warning", "Preparing", summary))
        # Stop only acts between files: nothing to stop once the last admitted file is running.
        strip.set_stop_visible("asset", not self._prepare_stop and self._prepare_index < total - 1)
        strip.update(
            "asset",
            int(snap.fraction * 1000) if known else 0,
            1000 if known else 0,
            detail=detail,
            elapsed=clock(snap.elapsed),
            eta=eta,
            stage=snap.stage,
            wide=snap.wide,
            count=snap.count,
        )

    def _end_prepare_progress(self) -> None:
        self._prepare_timer.stop()
        self._inspecting = False
        self._prepare_progress = None
        if self._prepare_relay is not None:
            self._prepare_relay.close()
            self._prepare_relay.deleteLater()
            self._prepare_relay = None

    def _release_prepare_editor_if_all_checked(self) -> None:
        """Release the overlay once every row got past the checks that can demand Re-Prepare.

        A row that is not admitted yet (Needs architecture, Name conflict), is still being checked, or failed
        keeps the editor open, so its Re-Prepare action stays reachable. Only this editor is dismissed; an
        unrelated armed consent or notice is left alone.
        """
        editor = self._prepare_editor
        if editor is None or not editor.all_checked():
            return
        editor.close()
        self._prepare_editor = self._prepare_editor_context = None

    def invalidate_prepare_ownership(self) -> None:
        """Credential/backend changes stop admission after the currently admitted file settles."""
        self._ownership_revision += 1

    def _prepare_next(self) -> None:
        if self._runner.closed:
            return  # runner/service shutdown owns settlement; never submit another file
        if (
            self._prepare_stop
            or self._prepare_index >= len(self._prepare_files)
            or self._backend() != self._prepare_backend
            or self._ownership_revision != self._prepare_revision
            or self._saved() is not self._prepare_saved
        ):
            self._finish_prepare()
            return
        number, total = self._prepare_index + 1, len(self._prepare_files)
        detail = f"Preparing {number} of {total}. May upload this file; waiting for receipt."
        self._show(self.page.assetStatusLabel, Presentation("warning", "Preparing", detail))
        index = self._prepare_index
        source = Path(self._prepare_files[index])
        metadata = self._prepare_metadata[str(source)]
        converting = source.suffix.casefold() == ".safetensors"
        if self._prepare_relay is not None:
            self._prepare_relay.close()
            self._prepare_relay.deleteLater()
        # One relay per file: a late report of a settled file is dropped instead of moving the next one.
        relay = self._prepare_relay = LatestRelay(lambda event: self._on_prepare_event(index, event), self.page)
        self._prepare_progress.start_file(index, convert=converting)
        self._render_prepare()
        converted_sha: list[str | None] = [None]
        converted_size: list[int | None] = [None]

        async def run_one():
            path = source
            forward = relay
            if converting:
                spec = next(
                    (
                        candidate
                        for candidate in supported_families()
                        if candidate.exact_architecture == metadata.exact_architecture
                    ),
                    None,
                )
                if spec is None:
                    raise UnsupportedFamilyError(
                        "The chosen LoRA source family is not supported by the converter.",
                        hint=COLAB_HINT,
                    )

                def conversion_progress(event: ConversionProgress) -> None:
                    labels = {
                        "reading": "Reading source",
                        "converting": "Converting",
                        "writing": "Writing checkpoint",
                        "finishing": "Finishing conversion",
                        "reused": "Reusing conversion",
                    }
                    # Preserve a monotonic conversion phase across the converter's independent
                    # tensor-conversion and checkpoint-writing counters.
                    fraction = min(max(event.done / event.total, 0.0), 1.0) if event.total else 0.0
                    portion = (
                        0.8 * fraction
                        if event.stage == "converting"
                        else 0.8 + 0.2 * fraction
                        if event.stage == "writing"
                        else 1.0
                        if event.stage in ("finishing", "reused")
                        else 0.0
                    )
                    relay(
                        PrepareEvent(CONVERTING, int(portion * 1000), 1000, label=labels.get(event.stage, "Converting"))
                    )

                try:
                    result = await convert_for_prepare(
                        self._state,
                        source,
                        spec.key,
                        metadata.identifier,
                        progress=conversion_progress,
                        cancel=self._prepare_convert_cancel,
                        allow_experimental=spec.experimental,
                        metadata=metadata,
                    )
                except (ConversionError, ValidationError):
                    raise
                except Exception as exc:
                    # The provider has not been contacted; even an unexpected local error is definite.
                    diagnostic_event(
                        log,
                        "lora_convert.prepare_unexpected_failure",
                        level=logging.ERROR,
                        data={
                            **asset_ref_data(
                                filename=source.name, source_sha256=self._prepare_source_sha.get(str(source))
                            ),
                            "family": spec.key,
                            "error_type": type(exc).__name__,
                        },
                    )
                    raise ConversionError("Local conversion failed before any upload began.") from exc
                path = result.retained.path
                prepared_metadata = result.metadata or metadata
                converted_sha[0] = result.retained.sha256
                converted_size[0] = result.retained.size
                output_size = result.retained.size
                shown_name = result.retained.path.name  # the managed checkpoint, not the selected source
                relay(
                    PrepareEvent(
                        CONVERTING,
                        1,
                        1,
                        label="Reused conversion" if result.reused else "Converted",
                        output_size=output_size,
                        name=shown_name,
                    )
                )

                def forward(event: PrepareEvent) -> None:
                    # The relay may coalesce stage changes; carry the actual output size and the latest managed
                    # checkpoint name (an accepted-name rename replaces it) in every asset event.
                    nonlocal shown_name
                    shown_name = event.name or shown_name
                    relay(PrepareEvent(event.stage, event.done, event.total, event.label, output_size, shown_name))

            return await prepare_asset(
                self._state,
                path,
                provenance="gui",
                saved=self._prepare_saved,
                metadata=prepared_metadata if converting else metadata,
                progress=forward,
            )

        self._runner.submit(
            run_one(),
            lambda record, failure: self._prepared(
                record, failure, converted_sha=converted_sha[0], converted_size=converted_size[0]
            ),
        )

    def _prepare_log_reference(self, record: dict | None, *, converted_sha: str | None = None) -> dict:
        """Correlate per-file Prepare diagnostics without logging names or paths."""
        path = Path(self._prepare_files[self._prepare_index])
        source_sha = self._prepare_source_sha.get(str(path))
        if source_sha is None and self._prepare_editor is not None:
            row = next((item for item in self._prepare_editor.rows if item.path == path), None)
            source_sha = row.sha256 if row is not None else None
        reference = asset_ref_data(
            filename=path.name,
            source_sha256=source_sha,
            sha256=(
                record.get("sha256")
                if record is not None
                else converted_sha or (source_sha if path.suffix.casefold() == ".ckpt" else None)
            ),
            asset_id=record.get("asset_id") if record is not None else None,
        )
        if record is not None and record.get("state") == "confirmed" and reference.get("asset_id"):
            # Preserve the receipt field in existing Prepare timing diagnostics.
            reference["receipt"] = reference["asset_id"]
        return reference

    def _prepared(
        self, record, failure, *, converted_sha: str | None = None, converted_size: int | None = None
    ) -> None:
        if self._prepare_files:
            self._prepare_last = record, failure
            state = record.get("state") if record is not None and failure is None else None
            if failure is not None:
                definite = (
                    isinstance(failure, BackendError) and failure.failure.remote_outcome != RemoteOutcome.UNKNOWN
                ) or isinstance(failure, (ValidationError, ConversionError))
                outcome = "failed" if definite else "unresolved"
            else:
                outcome = "confirmed" if state == "confirmed" else "failed" if state == "failed" else "unresolved"
            self._prepare_counts[outcome] += 1
            settled = (
                "existing" if outcome == "confirmed" and (record or {}).get("evidence") == "already_exists" else outcome
            )
            self._prepare_final[self._prepare_files[self._prepare_index]] = settled
            self._settle_prepare_progress(record, outcome, converted_sha=converted_sha)
            if self._prepare_editor is not None:
                detail = present_asset_failure(failure).detail if failure is not None else None
                if storage_refusal(failure):
                    self._prepare_storage_revision += 1  # discard any older advisory snapshot still in flight
                    free = reported_free_space(failure)
                    size = converted_size
                    if size is None and Path(self._prepare_files[self._prepare_index]).suffix.casefold() == ".ckpt":
                        try:
                            size = Path(self._prepare_files[self._prepare_index]).stat().st_size
                        except OSError:
                            pass
                    needed = storage_size(size) if size is not None else "unknown size"
                    available = storage_size(free) if free is not None else "unknown free space"
                    detail = f"Needs {needed}; {available} on this DT+ key. Free storage, then explicitly Re-Prepare."
                    self._prepare_editor.set_storage(free, needed=size)
                self._prepare_editor.progress(Path(self._prepare_files[self._prepare_index]), None)
                self._prepare_editor.completed(
                    Path(self._prepare_files[self._prepare_index]),
                    "Prepared" if outcome == "confirmed" else "Failed" if outcome == "failed" else "Outcome unknown",
                    can_reprepare=isinstance(failure, NameAlreadyAcceptedError) or storage_refusal(failure),
                    storage_failure=storage_refusal(failure),
                    detail=detail,
                )
                self._release_prepare_editor_if_all_checked()
            if outcome != "confirmed":
                explanation = (
                    present_asset_failure(failure)
                    if failure is not None
                    else Presentation(
                        "warning",
                        "Receipt not confirmed",
                        f"State: {state or 'unavailable'}. Review the local receipt before trying again.",
                    )
                )
                if len(self._prepare_issues) < 8:
                    filename = Path(self._prepare_files[self._prepare_index]).name
                    self._prepare_issues.append(
                        (f"{self._prepare_index + 1}/{len(self._prepare_files)}", filename, explanation.detail)
                    )
                if isinstance(failure, ConversionError):
                    reason = "conversion_failed"
                elif isinstance(failure, NameAlreadyAcceptedError):
                    reason = "accepted_name_conflict"
                elif isinstance(failure, ValidationError):
                    reason = "validation_failed"
                elif isinstance(failure, BackendError):
                    # An API error does not necessarily mean the remote operation failed.
                    reason = "backend_error"
                else:
                    reason = "unexpected_error" if failure is not None else "receipt_not_confirmed"
                data = {
                    **self._prepare_log_reference(record, converted_sha=converted_sha),
                    "outcome": outcome,
                    "reason": reason,
                    "item": self._prepare_index + 1,
                    "total": len(self._prepare_files),
                    "code": (
                        failure.failure.code
                        if isinstance(failure, BackendError)
                        else failure.code
                        if isinstance(failure, ConversionError)
                        else type(failure).__name__
                        if failure is not None
                        else state or "receipt_not_confirmed"
                    ),
                }
                if isinstance(failure, BackendError):
                    data["remote_outcome"] = failure.failure.remote_outcome.value
                elif isinstance(failure, ConversionError):
                    # Conversion runs before provider negotiation, so nothing was dispatched.
                    data["remote_outcome"] = RemoteOutcome.NOT_STARTED.value
                if development_mode():
                    # Keep a stable error type, never an arbitrary provider message or full source basename.
                    data["error_type"] = type(failure).__name__ if failure is not None else "receipt_not_confirmed"
                diagnostic_event(
                    log,
                    "ui.asset_prepare_incomplete",
                    level=logging.WARNING,
                    data=data,
                    # Avoid exception text/tracebacks containing the user's full source basename.
                    # The stable error type, code, file hint and SHA prefix are sufficient here.
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

    def _settle_prepare_progress(self, record, outcome: str, *, converted_sha: str | None = None) -> None:
        """Close the file in the progress model and log its phase timings (seconds per MiB) for weight tuning."""
        progress, index = self._prepare_progress, self._prepare_index
        if progress is None:
            return
        if record is not None and record.get("evidence") == "already_exists":
            # Deliberately a second EXISTS: the service already sent one, but the per-file relay is latest-wins and
            # may have dropped it, and a fast "already on DT+" file must still leave the totals.
            progress.event(index, PrepareEvent(EXISTS))
        timings = progress.settle(index, outcome)
        diagnostic_event(
            log,
            "ui.asset_prepare_timing",
            level=logging.INFO,
            data={
                **self._prepare_log_reference(record, converted_sha=converted_sha),
                "item": index + 1,
                "total": len(self._prepare_files),
                "outcome": outcome,
                **timings,
            },
        )
        if self._prepare_relay is not None:
            self._prepare_relay.close()  # late byte reports of this file are dropped

    def _drop_dead_editor(self) -> None:
        """Forget an editor the user dismissed, so it cannot receive progress or checks from a later run."""
        editor = self._prepare_editor
        if editor is not None and not editor.active():
            editor.cancel_hashing()
            self._prepare_editor = self._prepare_editor_context = None

    def _resume_hashing(self) -> None:
        paused, self._hash_paused = self._hash_paused, None
        if paused is not None:
            self._hash_next(*paused)

    def _finish_prepare(self) -> None:
        total, done = len(self._prepare_files), self._prepare_index
        stopped, unstarted = self._prepare_stop, self._prepare_files[self._prepare_index :]
        self._prepare_stop = False
        self._end_prepare_progress()
        if stopped and self._prepare_editor is not None:
            self._prepare_editor.released([Path(p) for p in unstarted])
        self._prepare_files = ()
        self._prepare_metadata = {}
        self._prepare_source_sha = {}
        self._prepare_saved = None
        self._set_busy(False)
        self._resume_hashing()
        if self._prepare_editor is not None and self._prepare_editor.active():
            self._prepare_editor.form.prepareStorageButton.setEnabled(True)
        if total == 1 and done == 1:
            same_owner = self._backend() == self._prepare_backend and self._ownership_revision == self._prepare_revision
            self._present_prepared(*self._prepare_last, bind=same_owner)
            return
        self._release_prepare_editor_if_all_checked()
        self._drop_dead_editor()
        editor_open = self._prepare_editor is not None and self._prepare_editor.active()
        # Count distinct files by their last outcome: a Re-Prepared file is one file, not two.
        final = list(self._prepare_final.values())
        prepared, existing = final.count("confirmed"), final.count("existing")
        failed, unresolved = final.count("failed"), final.count("unresolved")
        unstarted = total - done
        # A run that prepared something is mostly a success: its problem files "need attention", they did not fail the run.
        mostly_fine = bool(prepared + existing)
        problem = "needs attention" if mostly_fine else "failed"
        parts = [f"{prepared} prepared"]
        if existing:
            parts.append(f"{existing} already on DT+")
        if failed:
            parts.append(f"{failed} {problem}")
        if unresolved:
            parts.append(f"{unresolved} unresolved")
        if unstarted:
            parts.append(f"{unstarted} unstarted")
        detail = ", ".join(parts) + ". No automatic retries."
        state = "error" if failed and not mostly_fine else "partial" if failed or unresolved or unstarted else "success"
        level = "ok" if state == "success" else "error" if state == "error" else "warning"
        if editor_open:
            # One surface at a time: the open editor shows the result, so the bar job just ends quietly.
            self._prepare_editor.set_summary(detail)
            activity_strip(self.page).finish("asset")
            self._show(self.page.assetStatusLabel, Presentation(level, "Cloud preparation finished", detail))
            self.refresh_assets()
            return
        title = "Cloud preparation finished"
        panel_detail, full_detail, toast_detail = detail, detail, detail
        if self._prepare_issues:
            # Short reasons in the panel; the exact messages stay in the tooltip and in -x Logs.
            lines = [f"{index} {name}: {_short_reason(reason)}" for index, name, reason in self._prepare_issues]
            hint = "\n" + COLAB_HINT if any(COLAB_HINT in reason for _, _, reason in self._prepare_issues) else ""
            panel_detail = detail + "\n" + "\n".join(lines) + hint
            full_detail = detail + "\n" + "\n".join(f"{i} {n}: {r}" for i, n, r in self._prepare_issues)
            unseen = max(0, failed + unresolved - len(self._prepare_issues))
            if unseen:
                more = f"\n{unseen} more unsuccessful file(s); see -x Logs for all details."
                panel_detail += more
                full_detail += more
            if mostly_fine:
                toast_detail = detail + " Details in the side panel and -x Logs."
            else:
                toast_detail = full_detail
        detail = toast_detail
        if stopped and state != "error":
            state, title = "stopped", "Preparation stopped"
        activity_strip(self.page).complete("asset", title, detail, state)
        level = "ok" if state == "success" else "error" if state == "error" else "warning"
        self._show(self.page.assetStatusLabel, Presentation(level, title, panel_detail), tooltip=full_detail)
        self.refresh_assets()

    def _present_prepared(self, record, failure, *, bind: bool = True) -> None:
        self._set_busy(False)
        strip = activity_strip(self.page)
        self._drop_dead_editor()
        presentation = (
            present_asset_failure(failure)
            if failure
            else Presentation(
                "ok" if record["state"] == "confirmed" else "warning",
                "Receipt " + record["state"],
                f"{display_label(record['identifier'])} ({record['evidence']}).",
            )
        )
        editor_open = self._prepare_editor is not None and self._prepare_editor.active()
        if editor_open:
            # One surface at a time: the open editor shows the row's reason and Re-Prepare.
            self._prepare_editor.set_summary(presentation.headline)
            strip.finish("asset")
        else:
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
                    f"{display_label(record['identifier'])} · {self.family_labels().get(record['identifier'], 'Unknown')} "
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
                        Presentation(
                            "ok", "Receipt bound", f"{display_label(identifier)} uses its confirmed cloud receipt."
                        ),
                    )
                return True
        return False

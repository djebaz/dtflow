"""Native in-window LoRA Prepare editor using DTFlow's canonical Designer forms.

The editor collects one explicit authorization per ready row. It never talks to
the provider; the existing asset controller owns all submissions and outcomes.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QSizePolicy, QWidget
from shiboken6 import isValid

from dtflow.services.catalogue import FAMILY_LABELS
from dtflow.services.lora_family import LoraFamily
from dtflow.services.lora_naming import (
    LoraPrepareMetadata,
    ManagedName,
    NamingRequest,
    allocate_batch_names,
    architecture_shortcode,
    suggest_short_name,
)
from dtflow.ui.inline_feedback import feedback

_SHORT_NAME = re.compile(r"^[A-Za-z0-9_]{1,20}$")


def _load(name: str, parent: QWidget | None = None) -> QWidget:
    """Load the tracked Qt Designer form with the application's active stylesheet."""
    file = QFile(str(Path(__file__).with_name("forms") / name))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"LoRA Prepare form unavailable: {name}")
    try:
        widget = QUiLoader().load(file, parent)
    finally:
        file.close()
    if widget is None:
        raise OSError(f"LoRA Prepare form could not be loaded: {name}")
    return widget


@dataclass
class _Row:
    path: Path
    widget: QWidget
    suggested: str | None
    alternatives: tuple[str, ...]
    detected: str | None = None
    manual_name: bool = False
    manual_family: bool = False
    manual_format: bool = False
    admitted: bool = False
    outcome: str | None = None
    reprepare: bool = False
    checked: bool = False
    can_reprepare: bool = False
    invalid: str | None = None
    failure: str | None = None
    sha256: str | None = None
    size: int | None = None
    phase: str | None = None  # live "Uploading 43%" style text while this row is being prepared
    accepted: str | None = None  # managed filename this exact content already has in the local registry
    accepted_conflict: bool = False
    hashing: bool = True  # background content hash still running (cleared when it ends, even if unreadable)


class _FileElision(QObject):
    """Keep source filenames middle-elided to their live Qt column width."""

    def __init__(self, parent: QWidget, rows: list[_Row]):
        super().__init__(parent)
        self.rows = rows

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self.refresh)
        return False

    def refresh(self):
        for row in self.rows:
            label = row.widget.prepareSourceFile
            if not isValid(label):
                continue
            label.setText(
                label.fontMetrics().elidedText(
                    row.path.name,
                    Qt.TextElideMode.ElideMiddle,
                    max(35, label.width() - 4),
                )
            )


class _RowFocus(QObject):
    """Follow keyboard focus and pointer selection without intercepting controls."""

    def __init__(self, parent: QWidget, owner: "PrepareBatchEditor"):
        super().__init__(parent)
        self.owner = owner

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            for row in self.owner.rows:
                if row is not self.owner.selected and (watched is row.widget or row.widget.isAncestorOf(watched)):
                    self.owner._select(row)
                    break
        return False


class PrepareBatchEditor:
    """Own editable rows, readiness and visual outcomes for one selected batch."""

    def __init__(
        self,
        parent: QWidget,
        paths: Sequence[str],
        *,
        on_prepare: Callable[[list[tuple[Path, LoraPrepareMetadata]]], None],
    ):
        self.parent = parent
        self.hash_cancelled = threading.Event()
        self.form = _load("prepare_lora_batch.ui")
        self.panel = feedback(parent)
        self._on_prepare = on_prepare
        self.rows: list[_Row] = []
        self.occupied: tuple[ManagedName, ...] = ()
        self.architectures: tuple[str, ...] = ()
        self.inspected = False
        self.selected: _Row | None = None
        self._updating = False
        self._focus_selection = _RowFocus(self.form, self)

        self.form.prepareEmpty.hide()
        for path in dict.fromkeys(Path(p) for p in paths):
            row_widget = _load("prepare_lora_row.ui", self.form.prepareBatchRows)
            suggestion = suggest_short_name(path.name)
            row = _Row(path, row_widget, suggestion.name, suggestion.alternatives)
            self.rows.append(row)
            row_widget.prepareSourceFile.setToolTip(path.name)
            row_widget.prepareSourceFile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row_widget.prepareShortName.setText(suggestion.name or "")
            row_widget.prepareFormat.setCurrentIndex(1 if re.search(r"(?i)_lora_q8p\.ckpt$", path.name) else 0)
            row_widget.prepareReprepare.clicked.connect(lambda _checked=False, item=row: self._reprepare(item))
            row_widget.prepareCopySource.clicked.connect(
                lambda _checked=False, name=path.name: QGuiApplication.clipboard().setText(name)
            )
            row_widget.prepareShortName.textEdited.connect(lambda _s, item=row: self._edited_name(item))
            row_widget.prepareArchitecture.currentIndexChanged.connect(lambda _idx, item=row: self._edited_family(item))
            row_widget.prepareFormat.currentIndexChanged.connect(lambda _idx, item=row: self._edited_format(item))
            row_widget.prepareTriggerWords.textChanged.connect(lambda _s: self.refresh())
            row_widget.mousePressEvent = lambda event, item=row: self._select(item)
            self.form.prepareBatchRowsLayout.insertWidget(len(self.rows) - 1, row_widget)
            row_widget.prepareRowStatus.setText("Inspecting file…")
            row_widget.prepareStatusDot.setProperty("state", "unknown")
            for control in (
                row_widget,
                row_widget.prepareSourceFile,
                row_widget.prepareCopySource,
                row_widget.prepareArchitecture,
                row_widget.prepareShortName,
                row_widget.prepareFormat,
                row_widget.prepareTriggerWords,
            ):
                control.installEventFilter(self._focus_selection)
        self.selected = self.rows[0] if self.rows else None
        self._elision = _FileElision(self.form, self.rows)
        for row in self.rows:
            row.widget.prepareSourceFile.installEventFilter(self._elision)
        self.form.prepareFileCount.setText(f"{len(self.rows)} selected files")
        self.panel.editor("Prepare LoRAs", self.form, "Inspecting files…", self.authorize, primary=True)
        self.refresh()
        QTimer.singleShot(0, self._elision.refresh)

    def active(self) -> bool:
        """Whether the main window still owns this editor's widget."""
        return isValid(self.form) and self.panel._editor is self.form

    def set_identity(self, path: Path, sha256: str | None, size: int | None, accepted: str | None = None) -> None:
        """Attach the background content hash (``None`` when unreadable) and any accepted managed filename."""
        for row in self.rows:
            if row.path == path:
                row.sha256, row.size, row.accepted, row.hashing = sha256, size, accepted, False
                break
        self.refresh()

    def progress(self, path: Path, text: str | None, summary: str | None = None) -> None:
        """Show the live phase of one row (``None`` clears it) and an overall line next to the file count."""
        if not self.active():
            return
        for row in self.rows:
            if row.path == path:
                row.phase = text
                break
        if summary is not None:
            self.form.prepareFileCount.setText(f"{len(self.rows)} selected files · {summary}")
        self.refresh()

    def set_summary(self, text: str) -> None:
        """One-line run result shown inside the editor while it stays open."""
        if self.active():
            self.form.prepareFileCount.setText(f"{len(self.rows)} selected files · {text}")

    def cancel_hashing(self) -> None:
        """Stop the background reads (editor closed, dismissed or replaced)."""
        self.hash_cancelled.set()

    def all_checked(self) -> bool:
        """Whether every row is admitted and past the local checks, with no failure left to Re-Prepare.

        The registry refuses a changed name for content that already has an accepted one (Re-Prepare needed)
        while the file is still being checked, so the editor must stay open until every row got past that step.
        ``row.checked`` comes from a later stage event; a file that fails or settles first never reports one, so a
        known outcome counts as past checking too (this fallback is what lets a failed or instant row release).
        """
        return bool(self.rows) and all(
            row.invalid  # not a LoRA: never admitted, nothing left to do for it here
            or (
                row.admitted
                and (row.checked or row.outcome is not None)
                and not (row.outcome == "Failed" and row.can_reprepare)
            )
            for row in self.rows
        )

    def checked(self, path: Path) -> None:
        """Record that the registry accepted this row's name, so it can no longer need Re-Prepare."""
        for row in self.rows:
            if row.path == path:
                row.checked = True
                break

    def close(self) -> None:
        """Dismiss this editor only while it still owns the panel; never touch an unrelated consent or notice."""
        self.cancel_hashing()
        if self.active():
            self.panel.dismiss()

    def _select(self, row: _Row):
        self.selected = row
        self.refresh()

    def _edited_name(self, row: _Row):
        row.manual_name = True
        self._select(row)

    def _edited_family(self, row: _Row):
        if self._updating:
            return
        row.manual_family = True
        self._select(row)

    def _edited_format(self, row: _Row):
        if self._updating:
            return
        row.manual_format = True
        self._select(row)

    def set_inspection(
        self,
        evidence: Sequence[LoraFamily],
        architectures: Sequence[str],
        occupied: Sequence[ManagedName],
        invalid: Sequence[str | None] | None = None,
    ):
        """Apply read-only file evidence and verified backend choices on the GUI thread."""
        if not self.active():
            return
        self.architectures = tuple(dict.fromkeys(v for v in architectures if architecture_shortcode(v) is not None))
        self.occupied = tuple(occupied)
        self._updating = True
        for index, (row, result) in enumerate(zip(self.rows, evidence, strict=True)):
            row.detected = result.family
            row.invalid = invalid[index] if invalid else None
            row.widget.prepareFileEvidence.setText("Detected from file" if result.family else "Unknown inspection")
            combo = row.widget.prepareArchitecture
            combo.clear()
            combo.addItem("Select architecture…", None)
            for version in self.architectures:
                combo.addItem(FAMILY_LABELS.get(version, version), version)
            index = combo.findData(row.detected)
            combo.setCurrentIndex(max(0, index))
        self._updating = False
        self.inspected = True
        self.refresh()

    def _allocation(self):
        requests = [
            NamingRequest(
                short_name=row.widget.prepareShortName.text().strip() or None,
                exact_architecture=row.widget.prepareArchitecture.currentData(),
                format_code=row.widget.prepareFormat.currentText(),
                manually_edited=row.manual_name,
                replace_existing=row.reprepare,
                alternatives=row.alternatives,
                sha256=row.sha256,
                size=row.size,
            )
            for row in self.rows
        ]
        return allocate_batch_names(requests, self.occupied)

    def refresh(self):
        """Recompute every row's visible readiness and the selected managed name."""
        if not self.active() or self._updating:
            return
        self._updating = True
        try:
            allocations = self._allocation() if self.inspected else [None] * len(self.rows)
            ready = 0
            assigned = 0
            for row, allocation in zip(self.rows, allocations, strict=True):
                row.accepted_conflict = False
                family = row.widget.prepareArchitecture.currentData()
                if family and not row.invalid:
                    assigned += 1
                if allocation and allocation.short_name and not row.manual_name and not row.admitted:
                    if allocation.short_name != row.widget.prepareShortName.text():
                        row.widget.prepareShortName.setText(allocation.short_name)
                if row.admitted:
                    message = row.outcome or row.phase or "Preparing…"
                    state = "ok" if message in ("Ready", "Prepared") else "error" if message == "Failed" else "warning"
                elif not self.inspected:
                    message, state = "Inspecting file…", "unknown"
                elif row.invalid:
                    message, state = "Not a valid LoRA", "error"
                elif row.hashing:
                    # The content hash decides whether the file already has an accepted name; wait for it.
                    message, state = "Reading file…", "unknown"
                elif allocation is None or not family:
                    message, state = "Needs architecture", "warning"
                elif (
                    row.accepted
                    and not row.reprepare
                    and allocation.filename
                    and allocation.conflict is None
                    and allocation.filename.casefold() != row.accepted.casefold()
                ):
                    # Known before Prepare: this content already has another accepted name. Re-Prepare is the
                    # explicit choice to rename it; until then the row is not sent (it would be refused).
                    message, state = "Already accepted under another name", "warning"
                    row.accepted_conflict = True
                elif allocation.conflict is not None:
                    message, state = (
                        "Name conflict" if "collision" in allocation.conflict else "Check short name",
                        "error",
                    )
                else:
                    message, state = "Ready", "ok"
                    ready += 1

                row.widget.prepareRowStatus.setText(message)
                row.widget.prepareRowStatus.setToolTip(
                    row.invalid
                    or row.failure
                    or (f"Accepted filename: {row.accepted}" if row.accepted_conflict else "")
                )
                row.widget.prepareRowStatus.setProperty("state", state)
                row.widget.prepareStatusDot.setProperty("state", state)
                for label in (row.widget.prepareRowStatus, row.widget.prepareStatusDot):
                    label.style().unpolish(label)
                    label.style().polish(label)
                source_note = row.widget.prepareFileEvidence
                source_note.setText(
                    "Inspecting file…"
                    if not self.inspected
                    else "Declared by user"
                    if row.manual_family
                    else "Detected from file"
                    if row.detected
                    else "Unknown inspection"
                )
                family_note = row.widget.prepareArchitectureSource
                if not self.inspected:
                    family_note.setText("Inspecting file…")
                elif family:
                    if row.manual_family:
                        family_note.setText(
                            "User declared · file differs"
                            if row.detected and row.detected != family
                            else "User declared · unverified"
                        )
                    else:
                        family_note.setText("File-derived evidence")
                elif row.detected:
                    family_note.setText("Detected; backend support unverified")
                else:
                    family_note.setText("Required · not file-detected")
                family_note.setProperty("state", "error" if not family and self.inspected else "unknown")
                family_note.style().unpolish(family_note)
                family_note.style().polish(family_note)
                row.widget.prepareArchitecture.setProperty("invalid", self.inspected and not family)
                row.widget.prepareArchitecture.style().unpolish(row.widget.prepareArchitecture)
                row.widget.prepareArchitecture.style().polish(row.widget.prepareArchitecture)
                short_name = row.widget.prepareShortName.text().strip()
                name_error = (
                    self.inspected
                    and not row.admitted
                    and (
                        not _SHORT_NAME.fullmatch(short_name)
                        or (
                            allocation is not None
                            and allocation.conflict not in (None, "missing_architecture", "missing_format")
                        )
                    )
                )
                row.widget.prepareShortName.setProperty("invalid", name_error)
                row.widget.prepareShortName.style().unpolish(row.widget.prepareShortName)
                row.widget.prepareShortName.style().polish(row.widget.prepareShortName)
                row.widget.prepareReprepare.setVisible(
                    (row.admitted and row.outcome == "Failed" and row.can_reprepare) or row.accepted_conflict
                )
                if row.accepted_conflict:
                    row.widget.prepareReprepare.setToolTip(
                        f"This content is already accepted as {row.accepted}. Re-Prepare renames it to the name shown "
                        "here: a new filename and a new DT+ upload; earlier receipts keep the old name. "
                        "Nothing is sent until you press the Prepare button."
                    )
                row.widget.setProperty("checkedSelection", row is self.selected)
                row.widget.style().unpolish(row.widget)
                row.widget.style().polish(row.widget)

            valid = [row for row in self.rows if not row.invalid]
            invalid = len(self.rows) - len(valid)
            prepared = sum(row.outcome == "Prepared" for row in self.rows)
            running = [row for row in self.rows if row.admitted and row.outcome is None]
            self.form.prepareSelectedCount.setText(f"Selected: {len(self.rows)}")
            self.form.prepareAssignedCount.setText(f"Architectures assigned: {assigned}/{len(valid)}")
            self.form.prepareReadyCount.setText(
                f"Prepared: {prepared} · Ready: {ready}" + (f" · Not valid: {invalid}" if invalid else "")
            )
            button = self.panel.form.inlineEditorAcceptButton
            if not self.inspected:
                button.setText("Inspecting files…")
            elif running and ready == 0:
                button.setText(f"Preparing… {len(self.rows) - len(running) - invalid}/{len(self.rows) - invalid}")
            else:
                button.setText(f"Prepare {ready} ready LoRA{'s' if ready != 1 else ''}")
            button.setEnabled(ready > 0)
            if self.selected is not None:
                index = self.rows.index(self.selected)
                allocation = allocations[index]
                self.form.prepareManagedFilename.setText(
                    allocation.filename if allocation and allocation.filename else "Complete required fields to preview"
                )
                architecture = self.selected.widget.prepareArchitecture.currentData()
                short = self.selected.widget.prepareShortName.text().strip()
                tag = architecture_shortcode(architecture) if architecture else None
                self.form.prepareDisplayLabel.setText(f"{short}[{tag}]" if short and tag else "No display label yet")
            QTimer.singleShot(0, self._elision.refresh)
        finally:
            self._updating = False

    def authorize(self):
        """Admit exactly the currently ready and unsubmitted rows, once per click."""
        if not self.active() or not self.inspected:
            return
        allocations = self._allocation()
        accepted: list[tuple[Path, LoraPrepareMetadata]] = []
        for row, allocated in zip(self.rows, allocations, strict=True):
            version = row.widget.prepareArchitecture.currentData()
            if (
                row.admitted
                or row.invalid
                or row.hashing
                or not version
                or not allocated.ready
                or not allocated.short_name
            ):
                continue
            fmt = row.widget.prepareFormat.currentText()
            source = (
                "user"
                if row.manual_format
                else "filename_hint"
                if re.search(r"(?i)_lora_(?:q8p|f16)\.ckpt$", row.path.name)
                else "converter_default"
            )
            metadata = LoraPrepareMetadata(
                short_name=allocated.short_name,
                exact_architecture=version,
                format_code=fmt,
                format_source=source,
                trigger_words=row.widget.prepareTriggerWords.text().strip(),
                declared_version=version if row.manual_family or version != row.detected else None,
                replace_existing=row.reprepare,
            )
            accepted.append((row.path, metadata))
        if not accepted:
            return
        self._on_prepare(accepted)

    def admitted(self, paths: Sequence[Path]):
        """Lock accepted rows while leaving incomplete rows editable."""
        if not self.active():
            return
        names = set(paths)
        for row in self.rows:
            if row.path not in names:
                continue
            row.admitted = True
            row.outcome = None
            row.checked = False
            for control in (
                row.widget.prepareArchitecture,
                row.widget.prepareShortName,
                row.widget.prepareFormat,
                row.widget.prepareTriggerWords,
            ):
                control.setEnabled(False)
        self.refresh()

    def _reprepare(self, row: _Row):
        """Unlock one failed row for editing; it is sent again only by the next explicit Prepare action."""
        if not self.active():
            return
        if not row.admitted and row.accepted_conflict:
            row.reprepare = True  # renames the accepted name on the next Prepare
            self.refresh()
            return
        if not row.admitted or row.outcome != "Failed" or not row.can_reprepare:
            return
        row.admitted, row.outcome, row.reprepare, row.checked = False, None, True, False
        for control in (
            row.widget.prepareArchitecture,
            row.widget.prepareShortName,
            row.widget.prepareFormat,
            row.widget.prepareTriggerWords,
        ):
            control.setEnabled(True)
        self.refresh()

    def completed(self, path: Path, outcome: str, *, can_reprepare: bool = False, detail: str | None = None):
        """Reflect the provider result without automatically authorizing another row."""
        if not self.active():
            return
        for row in self.rows:
            if row.path == path:
                row.outcome = outcome
                row.can_reprepare = outcome == "Failed" and can_reprepare
                row.failure = detail
                break
        self.refresh()

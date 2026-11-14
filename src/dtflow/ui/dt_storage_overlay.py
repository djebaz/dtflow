"""In-window DT+ storage panel over Connections (#440): list the selected key's stored files, delete chosen ones.

Listing is one explicit metadata request made for a retained LoRA's real identity; DT+ answers with its file list only
when the account is over its storage limit. Delete is owner-confirmed (a second click on the same button), runs once
with no retry, and never touches local files, retained copies or saved results."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QFrame, QListWidgetItem, QVBoxLayout, QWidget

from dtflow.core.models import AssetContent, StorageListing, StoredAsset
from dtflow.services.session import delete_storage, storage_listing
from dtflow.ui.runner import AsyncRunner

_DELETE_TEXT = "Delete selected from DT+…"
_PROBE_NAME = "dtflow-storage-probe.ckpt"
_PROBE_FALLBACK = 10 * 1024**3  # TEMPORARY 10 GiB trial (#440), revert after the owner run


def megabytes(size: int) -> str:
    """Decimal MB, the unit DT+ account screens use; one decimal below 10 MB."""
    value = size / 1_000_000
    return f"{value:.1f} MB" if value < 10 else f"{value:.0f} MB"


def _files(count: int) -> str:
    return f"{count} file" + ("" if count == 1 else "s")


def _load_form() -> QWidget:
    file = QFile(str(Path(__file__).with_name("forms") / "dt_storage_overlay.ui"))
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError("DT+ storage form unavailable.")
    try:
        form = QUiLoader().load(file)
    finally:
        file.close()
    if form is None:
        raise OSError("DT+ storage form could not be loaded.")
    return form


class _Resize(QObject):
    def __init__(self, host: QWidget, overlay: QWidget):
        super().__init__(overlay)
        self.host, self.overlay = host, overlay
        host.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.overlay.setGeometry(self.host.rect())
        return False


class DtStorageOverlay(QObject):
    """Opens over ``host``; ``saved`` returns the credential provider; ``on_changed`` runs after files were deleted."""

    def __init__(
        self,
        host: QWidget,
        runner: AsyncRunner,
        state_dir: Path,
        saved: Callable[[], object],
        *,
        on_changed: Callable[[], None],
        on_closed: Callable[[], None],
    ):
        super().__init__(host)
        self.runner, self.state_dir, self._saved = runner, Path(state_dir), saved
        self._on_changed, self._on_closed = on_changed, on_closed
        self.overlay = QFrame(host)
        self.overlay.setObjectName("dtStorageOverlay")
        self.overlay.setStyleSheet("#dtStorageOverlay { background-color: rgba(0, 0, 0, 150); }")
        self.overlay.setVisible(False)
        layout = QVBoxLayout(self.overlay)
        self.form = _load_form()
        layout.addStretch(1)
        layout.addWidget(self.form, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        _Resize(host, self.overlay)
        self.form.dtStorageClose.clicked.connect(self.close)
        self.form.dtStorageRefresh.clicked.connect(self.refresh)
        self.form.dtStorageProbe.clicked.connect(self.probe)
        self.form.dtStorageDelete.clicked.connect(self._delete_clicked)
        self.form.dtStorageList.itemChanged.connect(self._selection_changed)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.overlay)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close)
        self._content: AssetContent | None = None
        self._files: tuple[StoredAsset, ...] = ()
        self._busy = False
        self._armed = False
        self._free: int | None = None  # last free space DT+ reported, to size the dummy lookup
        self._note = ""  # outcome of the last delete, kept above the re-read listing

    def is_open(self) -> bool:
        return self.overlay.isVisible()

    def open(self, content: AssetContent | None) -> None:
        """Show the panel; with a retained LoRA's real local identity, read the listing once."""
        self._content = content
        self._error("")
        self._files = ()
        self.form.dtStorageList.clear()
        self.form.dtStorageList.setVisible(False)
        self._selection_changed()
        self.overlay.setGeometry(self.overlay.parentWidget().rect())
        self.overlay.raise_()
        self.overlay.setVisible(True)
        if content is None:
            self.form.dtStorageStatus.setText(
                "No LoRA selected. Use Check with dummy file, or close this and select a LoRA first."
            )
        else:
            self.refresh()
        self.form.dtStorageClose.setFocus()

    def close(self) -> None:
        if self._busy:
            return
        self.overlay.setVisible(False)
        self._on_closed()

    # -- state helpers --------------------------------------------------------------------------------------------
    def _error(self, text: str) -> None:
        self.form.dtStorageError.setText(text)
        self.form.dtStorageError.setVisible(bool(text))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.form.dtStorageRefresh.setEnabled(not busy and self._content is not None)
        self.form.dtStorageProbe.setEnabled(not busy)
        self.form.dtStorageClose.setEnabled(not busy)
        self.form.dtStorageList.setEnabled(not busy)
        self._selection_changed()

    def _disarm(self) -> None:
        self._armed = False
        self.form.dtStorageDelete.setText(_DELETE_TEXT)

    def _chosen(self) -> tuple[StoredAsset, ...]:
        chosen = []
        for row in range(self.form.dtStorageList.count()):
            item = self.form.dtStorageList.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                chosen.append(self._files[item.data(Qt.ItemDataRole.UserRole)])
        return tuple(chosen)

    def _selection_changed(self, *_args) -> None:
        self._disarm()
        chosen = self._chosen()
        total = sum(item.size for item in chosen)
        self.form.dtStorageSelection.setText(
            f"{len(chosen)} of {len(self._files)} selected, {megabytes(total)}." if self._files else ""
        )
        self.form.dtStorageDelete.setEnabled(bool(chosen) and not self._busy)

    # -- listing --------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if self._busy or self._content is None or self.runner.closed:
            return
        self._error("")
        self._files = ()
        self.form.dtStorageList.clear()
        self.form.dtStorageList.setVisible(False)
        self.form.dtStorageStatus.setText("Reading DT+ storage…")
        self._set_busy(True)
        self.runner.submit(storage_listing(self.state_dir, self._content, saved=self._saved()), self._listed)

    def probe(self) -> None:
        """One lookup for a made-up file bigger than the free space, so DT+ answers with its over-limit list.

        The hash is random per click and never stored; nothing is uploaded and any upload URL DT+ returns is ignored."""
        if self._busy or self.runner.closed:
            return
        size = _PROBE_FALLBACK  # TEMPORARY: always the trial size
        self._content = AssetContent("lora", _PROBE_NAME, secrets.token_hex(32), size, provenance="storage probe")
        self.refresh()

    def _listed(self, listing: StorageListing | None, failure) -> None:
        self._set_busy(False)
        note, self._note = self._note, ""
        lead = f"{note} " if note else ""
        if failure is not None:
            self.form.dtStorageStatus.setText(note)
            self._error(str(failure) or "DT+ storage could not be read.")
            return
        if listing is None or not listing.over_limit:
            self.form.dtStorageStatus.setText(
                lead
                + "DT+ did not report a storage limit problem, so it returned no file list. Nothing more to delete."
            )
            return
        self._files = listing.files
        self._free = listing.free_space if listing.free_space is not None else self._free
        self.form.dtStorageList.setVisible(bool(listing.files))
        free = (
            f"About {megabytes(listing.free_space)} free." if listing.free_space is not None else "Free space unknown."
        )
        total = sum(item.size for item in listing.files)
        skipped = f" {listing.skipped} entries could not be read and are not shown." if listing.skipped else ""
        self.form.dtStorageStatus.setText(
            f"{lead}The storage limit is exceeded. {free} {_files(len(listing.files))} stored, {megabytes(total)}.{skipped}"
        )
        self.form.dtStorageList.blockSignals(True)
        for index, item in enumerate(listing.files):
            row = QListWidgetItem(f"{item.name}    {megabytes(item.size)}")
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Unchecked)
            row.setData(Qt.ItemDataRole.UserRole, index)
            self.form.dtStorageList.addItem(row)
        self.form.dtStorageList.blockSignals(False)
        self._selection_changed()

    # -- delete ---------------------------------------------------------------------------------------------------
    def _delete_clicked(self) -> None:
        chosen = self._chosen()
        if self._busy or not chosen or self.runner.closed:
            return
        total = megabytes(sum(item.size for item in chosen))
        if not self._armed:
            self._armed = True
            self.form.dtStorageDelete.setText(f"Confirm: delete {_files(len(chosen))}, {total}")
            self._error("")
            self.form.dtStorageSelection.setText(
                f"This permanently deletes {_files(len(chosen))} ({total}) from the selected DT+ key. "
                "Click the button again to confirm, or change the selection to cancel."
            )
            return
        self._disarm()
        self._error("")
        self.form.dtStorageStatus.setText("Deleting from DT+…")
        self._set_busy(True)
        self.runner.submit(delete_storage(self.state_dir, chosen, saved=self._saved()), self._deleted)

    def _deleted(self, result: dict | None, failure) -> None:
        self._set_busy(False)
        if failure is not None:
            self.form.dtStorageStatus.setText("")
            self._error(str(failure) or "The files were not deleted.")
            return
        deleted = int(result.get("deleted", 0)) if result else 0
        updated = result.get("receipts_updated", True) if result else True
        matched = int(result.get("receipts_invalidated", 0)) if result else 0
        note = (
            f" {matched} local upload record{'' if matched == 1 else 's'} updated."
            if updated
            else " Local upload records could not be updated; they will be re-checked on the next run."
        )
        self._on_changed()
        self._note = f"Deleted {_files(deleted)} from DT+.{note}"
        self.refresh()

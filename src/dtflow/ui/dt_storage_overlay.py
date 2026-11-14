"""In-window DT+ storage panel over Connections (#440): list the selected key's stored files, delete chosen ones.

Listing is one explicit metadata request made for a retained LoRA's real identity; DT+ answers with its file list only
when the account is over its storage limit. Delete is owner-confirmed (a second click on the same button), runs once
with no retry, and never touches local files, retained copies or saved results."""

from __future__ import annotations

import html
import secrets
from pathlib import Path
from typing import Awaitable, Callable

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QFrame, QListWidgetItem, QVBoxLayout, QWidget

from dtflow.core.models import AssetContent, StorageListing, StoredAsset
from dtflow.services.session import delete_storage, storage_listing
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.theme import load_tokens

_DELETE_TEXT = "Delete selected from DT+…"
_PROBE_NAME = "dtflow-storage-probe.ckpt"
_PROBE_FALLBACK = (
    30 * 1024**3
)  # free space unknown: larger than any account headroom we expect (10 GiB worked, 30 GiB for margin)


def megabytes(size: int) -> str:
    """Decimal MB, the unit DT+ account screens use; one decimal below 10 MB."""
    value = size / 1_000_000
    return f"{value:.1f} MB" if value < 10 else f"{value:.0f} MB"


def gigabytes(size: int) -> str:
    """Decimal GB with one decimal, for totals and free space."""
    return f"{size / 1_000_000_000:.1f} GB"


def _bold(text: str) -> str:
    """A value drawn with the emphasis weight; the labels using it are rich text."""
    return f'<span style="font-weight:{load_tokens()["type.weightEmphasis"]}">{html.escape(text)}</span>'


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
        active_credential: Callable[[], Awaitable[tuple[str, str | None] | None]],
        on_changed: Callable[[], None],
        on_closed: Callable[[], None],
    ):
        super().__init__(host)
        self.runner, self.state_dir, self._saved = runner, Path(state_dir), saved
        self._on_changed, self._on_closed = on_changed, on_closed
        self._active_credential = active_credential
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
        self.form.dtStorageDelete.clicked.connect(self._delete_clicked)
        self.form.dtStorageList.itemChanged.connect(self._selection_changed)
        self.form.dtStorageList.itemClicked.connect(self._toggle_row)  # a click anywhere on the row ticks it
        for label in (self.form.dtStorageStatus, self.form.dtStorageSelection):
            label.setTextFormat(Qt.TextFormat.RichText)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.overlay)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close)
        self._files: tuple[StoredAsset, ...] = ()
        self._busy = False
        self._armed = False
        self._free: dict[
            str, int
        ] = {}  # free space DT+ last reported, by opaque key identity, to size the re-read lookup
        self._note = ""  # outcome of the last delete, kept above the re-read listing

    def is_open(self) -> bool:
        return self.overlay.isVisible()

    def open(self) -> None:
        """Show the panel and read the listing once with a dummy-file lookup (see ``_probe_content``).

        Free space learned on an earlier open belongs to whichever key was selected then, so each open starts from the
        large unknown-size lookup; only re-reads within this open reuse the key's own free space."""
        self._free = {}
        self._error("")
        self._files = ()
        self.form.dtStorageList.clear()
        self.form.dtStorageList.setVisible(False)
        self._selection_changed()
        self.overlay.setGeometry(self.overlay.parentWidget().rect())
        self.overlay.raise_()
        self.overlay.setVisible(True)
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
        self.form.dtStorageRefresh.setEnabled(not busy)
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

    def _toggle_row(self, item: QListWidgetItem) -> None:
        if self._busy:
            return
        ticked = item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked if ticked else Qt.CheckState.Checked)

    def _selection_changed(self, *_args) -> None:
        self._disarm()
        chosen = self._chosen()
        total = sum(item.size for item in chosen)
        self.form.dtStorageSelection.setText(
            f"{_bold(str(len(chosen)))} of {len(self._files)} selected, {_bold(gigabytes(total))}."
            if self._files
            else ""
        )
        self.form.dtStorageDelete.setEnabled(bool(chosen) and not self._busy)

    # -- listing --------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if self._busy or self.runner.closed:
            return
        self._error("")
        self._files = ()
        self.form.dtStorageList.clear()
        self.form.dtStorageList.setVisible(False)
        self.form.dtStorageStatus.setText("Reading DT+ storage…")
        self._set_busy(True)
        self.runner.submit(self._read(), self._listed)

    async def _read(self) -> tuple[str | None, StorageListing]:
        """Lookup sized for the key that is selected right now; its identity comes back with the answer."""
        identity = await self._active_credential()
        key = identity[0] if identity else None
        listing = await storage_listing(self.state_dir, self._probe_content(key), saved=self._saved())
        return key, listing

    def _probe_content(self, key: str | None) -> AssetContent:
        """A made-up file bigger than the free space, so DT+ answers with its over-limit list when the key is full.

        The hash is random per lookup and never stored; nothing is uploaded and any upload URL DT+ returns is ignored."""
        free = self._free.get(key) if key is not None else None
        size = (free + 1024**2) if free is not None else _PROBE_FALLBACK
        return AssetContent("lora", _PROBE_NAME, secrets.token_hex(32), size, provenance="storage probe")

    def _listed(self, result: tuple[str | None, StorageListing] | None, failure) -> None:
        self._set_busy(False)
        key, listing = result if result is not None else (None, None)
        note, self._note = self._note, ""
        lead = f"{html.escape(note)} " if note else ""
        if failure is not None:
            self.form.dtStorageStatus.setText(html.escape(note))
            self._error(str(failure) or "DT+ storage could not be read.")
            return
        if listing is None or not listing.over_limit:
            self.form.dtStorageStatus.setText(
                lead + "DT+ returned no file list. This key has plenty of free space, so there is nothing to free."
            )
            return
        self._files = listing.files
        if key is not None and listing.free_space is not None:
            self._free[key] = listing.free_space
        self.form.dtStorageList.setVisible(bool(listing.files))
        free = f"Free: {_bold(gigabytes(listing.free_space))}." if listing.free_space is not None else "Free: unknown."
        total = sum(item.size for item in listing.files)
        skipped = f" {listing.skipped} entries could not be read and are not shown." if listing.skipped else ""
        self.form.dtStorageStatus.setText(
            f"{lead}Stored: {_bold(_files(len(listing.files)))}, {_bold(gigabytes(total))}. {free}{skipped}"
        )
        self.form.dtStorageList.blockSignals(True)
        for index, item in enumerate(listing.files):
            row = QListWidgetItem(f"{item.name}    {megabytes(item.size)}")
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
        total = gigabytes(sum(item.size for item in chosen))
        if not self._armed:
            self._armed = True
            self.form.dtStorageDelete.setText(f"Confirm: delete {_files(len(chosen))}, {total}")
            self._error("")
            self.form.dtStorageSelection.setText(
                f"This permanently deletes {_bold(_files(len(chosen)))} ({_bold(total)}) from the selected DT+ key. "
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
        self._free = {}  # deleting changed the free space of the key just used
        self._on_changed()
        self._note = f"Deleted {_files(deleted)} from DT+.{note}"
        self.refresh()

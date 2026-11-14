"""In-window DT+ storage panel (#440/#442): list the selected key's stored files, delete chosen ones.

Listing is one explicit metadata request for a made-up oversized identity; DT+ answers with its file list only
when the account is over its storage limit. Delete is owner-confirmed (a second click on the same button), runs once
with no retry, and never touches local files, retained copies or saved results."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Awaitable, Callable

from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QComboBox,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from dtflow.core.models import AssetContent, StorageListing, StoredAsset
from dtflow.services.dt_storage import select_to_free, shortfall, storage_size
from dtflow.services.session import delete_storage, storage_listing
from dtflow.services.storage_probe import probe_content
from dtflow.ui.runner import AsyncRunner
from dtflow.ui.theme import load_tokens

_DELETE_TEXT = "Delete selected from DT+…"


def megabytes(size: int) -> str:
    """Decimal MB, the unit DT+ account screens use; one decimal below 10 MB."""
    value = size / 1_000_000
    return f"{value:.1f} MB" if value < 10 else f"{value:.0f} MB"


_METER_WARN_FROM = 0.8  # the meter turns amber once this share of the storage is used
_METER_RED_FROM = 0.9  # and red from here


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


class SegmentMeter(QWidget):
    """Used share of the key's storage as a row of small blocks: accent, warning from 80%, error from 90%."""

    HEIGHT, WIDTH, GAP = 10, 23, 3
    TONES = {"ok": 0.42, "warn": 0.62, "over": 0.72}  # share of the status colour over the track (muted, not flashing)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("dtStorageMeter")
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        colors = load_tokens()
        self._track = QColor(colors["color.barTrack"])
        self._fills = {
            state: self._muted(QColor(colors[token]), self.TONES[state])
            for state, token in (("ok", "color.accent"), ("warn", "color.warning"), ("over", "color.error"))
        }
        self.share = 0.0
        self.state = "ok"

    def _muted(self, color: QColor, t: float) -> QColor:
        return QColor(
            round(self._track.red() + (color.red() - self._track.red()) * t),
            round(self._track.green() + (color.green() - self._track.green()) * t),
            round(self._track.blue() + (color.blue() - self._track.blue()) * t),
        )

    def set_share(self, share: float) -> None:
        self.share = min(max(share, 0.0), 1.0)
        self.state = "over" if self.share >= _METER_RED_FROM else "warn" if self.share >= _METER_WARN_FROM else "ok"
        self.setToolTip(f"{self.share:.0%} of this key's storage is used.")
        self.setAccessibleDescription(f"{self.share:.0%} of this key's storage is used.")
        self.update()

    def count(self) -> int:
        return max(1, (self.width() + self.GAP) // (self.WIDTH + self.GAP))

    def filled(self) -> int:
        return round(self.share * self.count())

    def sizeHint(self) -> QSize:
        return QSize(200, self.HEIGHT)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        count, filled = self.count(), self.filled()
        step = (self.width() - self.WIDTH) / max(1, count - 1) if count > 1 else 0
        for index in range(count):
            painter.setBrush(self._fills[self.state] if index < filled else self._track)
            painter.drawRoundedRect(QRectF(index * step, 0, self.WIDTH, self.HEIGHT), 2, 2)
        painter.end()


class _RangeSelect(QObject):
    """Shift+click ticks every visible row between the last clicked row and this one; a plain click toggles as usual."""

    def __init__(self, listing: QListWidget):
        super().__init__(listing)
        self.listing = listing
        self.anchor: int | None = None
        listing.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.MouseButtonPress or event.button() != Qt.MouseButton.LeftButton:
            return False
        item = self.listing.itemAt(event.position().toPoint())
        if item is None:
            return False
        row = self.listing.row(item)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.anchor is not None:
            low, high = sorted((self.anchor, row))
            self.listing.blockSignals(True)
            for index in range(low, high + 1):
                candidate = self.listing.item(index)
                if not candidate.isHidden():
                    candidate.setSelected(True)
            self.listing.blockSignals(False)
            self.listing.itemSelectionChanged.emit()
            self.anchor = row
            return True  # the range is already applied; do not also toggle the clicked row
        self.anchor = row
        return False


class _Resize(QObject):
    def __init__(self, host: QWidget, overlay: QWidget):
        super().__init__(overlay)
        self.host, self.overlay = host, overlay
        host.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.overlay.setGeometry(self.host.rect())
            if self.overlay.property("nestedStorage"):
                card = self.overlay.layout().itemAt(0).widget()
                card.setMaximumSize(min(800, max(1, self.host.width() - 64)), min(560, max(1, self.host.height() - 64)))
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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self.form, 1)  # the card takes the room of the central panel
        self._host = host
        self._resize = _Resize(host, self.overlay)
        self.form.dtStorageClose.clicked.connect(self.close)
        self.form.dtStorageRefresh.clicked.connect(self.refresh)
        self.form.dtStorageDelete.clicked.connect(self._delete_clicked)
        self.sort_combo = QComboBox(self.form)
        self.sort_combo.setAccessibleName("Sort files")
        self.sort_combo.addItems(["Largest first", "Name A\u2013Z"])
        self.sort_combo.setToolTip("Order of the stored files.")
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        self.select_unused = QPushButton("\u2611\ufe0e Cloud only", self.form)
        self.select_unused.setAccessibleName("Select Cloud only files")
        self.select_unused.setToolTip(
            "Select all visible Cloud only files: no matching managed copy is known in lora_files. "
            "Nothing is deleted until you confirm."
        )
        self.select_unused.clicked.connect(self._select_unused)
        self.select_unused.setVisible(False)
        self.select_free = QPushButton("Select to free space", self.form)
        self.select_free.setToolTip(
            "Tick files this computer has no record of first, then the oldest ones this computer confirmed, until "
            "the space a generation needs is covered. DTFlow does not store when a LoRA was last used, so this is "
            "an estimate of recent use. Files it uses may come back at the next generation. Nothing is deleted until "
            "you confirm."
        )
        self.select_free.clicked.connect(self._select_to_free)
        self.select_free.setVisible(False)
        self.meter = SegmentMeter(self.form)
        layout = self.form.layout()
        layout.insertWidget(layout.indexOf(self.form.dtStorageStatus) + 1, self.meter)
        self.meter.setVisible(False)
        self.form.dtStorageList.itemChanged.connect(self._selection_changed)
        self.search = QLineEdit(self.form)
        self.search.setObjectName("dtStorageSearch")
        self.search.setPlaceholderText("Search anywhere in the name…")
        self.search.setAccessibleName("Stored files search")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_rows)
        # Search, order and the bulk-select action share one row above the list, so the bottom buttons keep their room.
        self._tools = QWidget(self.form)
        tools = QHBoxLayout(self._tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        self.search.setMinimumWidth(60)  # the search box gives way first; the two buttons keep their full text
        for fixed in (self.sort_combo, self.select_unused, self.select_free):
            fixed.setMinimumWidth(fixed.sizeHint().width())
            fixed.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.sort_combo)
        tools.addWidget(self.select_unused)
        tools.addWidget(self.select_free)
        layout.insertWidget(layout.indexOf(self.form.dtStorageList), self._tools)
        layout.setStretchFactor(self.form.dtStorageList, 1)
        # While the list is hidden (reading, or nothing to list) a spacer takes the spare height, so the texts and
        # buttons keep their natural spacing instead of the layout spreading them out.
        self._filler = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        layout.insertItem(layout.indexOf(self.form.dtStorageSelection), self._filler)
        layout.setStretch(layout.indexOf(self._filler), 1)
        # A click anywhere on a row ticks it: the list is multi-select and the check marks follow the selection.
        self.form.dtStorageList.itemSelectionChanged.connect(self._sync_checks)
        self._range = _RangeSelect(self.form.dtStorageList)
        for label in (self.form.dtStorageStatus, self.form.dtStorageSelection):
            label.setTextFormat(Qt.TextFormat.RichText)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.overlay)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close)
        self._files: tuple[StoredAsset, ...] = ()
        self._local: frozenset[str] | None = None  # delete keys with a local receipt; None when not known
        self._file_keys: frozenset[str] | None = None
        self._seen: dict[str, str] = {}  # delete key -> last local confirmation, to order what to free first
        self._needed: int | None = None  # bytes a failed generation needed, set only by that offer
        self._missing: int | None = None
        self._busy = False
        self._revision = 0
        self._listed_key: str | None = None
        self._refresh_pending = False
        self._armed = False
        self._free: dict[
            str, int
        ] = {}  # free space DT+ last reported, by opaque key identity, to size the re-read lookup
        self._note = ""  # outcome of the last delete, kept above the re-read listing
        QApplication.instance().focusChanged.connect(self._keep_focus)

    def _keep_focus(self, previous, current) -> None:
        if (
            self.overlay.isVisible()
            and self.overlay.property("nestedStorage")
            and current is not None
            and current.window() is self.overlay.window()
            and not self.overlay.isAncestorOf(current)
        ):
            self.form.dtStorageClose.setFocus()

    def is_open(self) -> bool:
        return self.overlay.isVisible()

    def open(self, *, above: QWidget | None = None, needed: int | None = None) -> None:
        """Show the panel and read the listing once with a dummy-file lookup (see ``_probe_content``).

        Free space learned on an earlier open belongs to whichever key was selected then, so each open starts from the
        large unknown-size lookup; only re-reads within this open reuse the key's own free space.

        ``needed`` (bytes a refused generation required) adds a size line and a helper that ticks what to free."""
        self._needed, self._missing = needed, None
        host = above if above is not None else self._host
        self._resize.host.removeEventFilter(self._resize)
        self.overlay.setParent(host)
        self._resize.host = host
        host.installEventFilter(self._resize)
        nested = above is not None
        self.overlay.setProperty("nestedStorage", nested)
        self.form.setMaximumSize(
            min(800, max(1, host.width() - 64)) if nested else 16777215,
            min(560, max(1, host.height() - 64)) if nested else 16777215,
        )
        self.overlay.layout().setAlignment(Qt.AlignmentFlag.AlignCenter if nested else Qt.AlignmentFlag(0))
        self._free = {}
        self._error("")
        self._files = ()
        self._local = None
        self._seen = {}
        self.select_free.setVisible(False)
        self._clear_list()
        self._show_list(False)
        self.meter.setVisible(False)
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
        self._mark_danger(False)

    def _mark_danger(self, armed: bool) -> None:
        # Same red armed treatment as Confirm delete in the Images gallery.
        button = self.form.dtStorageDelete
        button.setProperty("confirmationPending", armed)
        button.setProperty("confirmationDanger", armed)
        button.style().unpolish(button)
        button.style().polish(button)

    def _chosen(self) -> tuple[StoredAsset, ...]:
        chosen = []
        for row in range(self.form.dtStorageList.count()):
            item = self.form.dtStorageList.item(row)
            index = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked and isinstance(index, int) and 0 <= index < len(self._files):
                chosen.append(self._files[index])
        return tuple(chosen)

    def _sorted(self, files: tuple[StoredAsset, ...]) -> tuple[StoredAsset, ...]:
        if self.sort_combo.currentIndex() == 1:
            return tuple(sorted(files, key=lambda item: (item.name.casefold(), item.size)))
        return tuple(sorted(files, key=lambda item: (-item.size, item.name.casefold())))

    def _fill_rows(self, keep: tuple[StoredAsset, ...]) -> None:
        """Show the files in the chosen order, re-ticking those in ``keep``."""
        self._files = self._sorted(self._files)
        kept = {item.key for item in keep}
        self._disarm()
        self.form.dtStorageList.blockSignals(True)
        self.form.dtStorageList.clear()
        for index, item in enumerate(self._files):
            title = f"{item.name}    {megabytes(item.size)}"
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.AccessibleTextRole, title)
            row.setCheckState(Qt.CheckState.Checked if item.key in kept else Qt.CheckState.Unchecked)
            row.setData(Qt.ItemDataRole.UserRole, index)
            self.form.dtStorageList.addItem(row)
            card = QWidget(self.form.dtStorageList.viewport())
            card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout = QHBoxLayout(card)
            layout.setContentsMargins(28, 3, 8, 3)
            label = QLabel(title, card)
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label, 1)
            cloud_only = self._file_keys is not None and item.key not in self._file_keys
            if cloud_only:
                pill = QLabel("Cloud only", card)
                pill.setObjectName("storageCloudPill")
                pill.setToolTip(
                    "Stored on this DT+ key; no matching managed file is known in lora_files. Other folders are not checked."
                )
                layout.addWidget(pill)
                row.setData(Qt.ItemDataRole.AccessibleTextRole, f"{title}, {pill.text()}")
            self.form.dtStorageList.setItemWidget(row, card)
            # Measure after parenting/polishing so the inherited font and pill QSS are included.
            card.ensurePolished()
            label.ensurePolished()
            if cloud_only:
                pill.ensurePolished()
            layout.invalidate()
            layout.activate()
            hint = layout.sizeHint()
            row.setSizeHint(QSize(0, max(32, hint.height())))
            row.setSelected(item.key in kept)
        self.form.dtStorageList.blockSignals(False)
        self._filter_rows(self.search.text())
        self._selection_changed()

    def _select_unused(self) -> None:
        """Tick visible Cloud only rows, replacing the selection; delete still asks again."""
        if self._busy or self._file_keys is None:
            return
        listing = self.form.dtStorageList
        listing.blockSignals(True)
        for row in range(listing.count()):
            item = listing.item(row)
            stored = self._files[item.data(Qt.ItemDataRole.UserRole)]
            item.setSelected(stored.key not in self._file_keys and not item.isHidden())
        listing.blockSignals(False)
        self._sync_checks()

    def _select_to_free(self) -> None:
        """Tick the files to free first (see ``select_to_free``), replacing the selection; delete still asks again."""
        if self._busy or not self._missing:
            return
        chosen = {item.key for item in select_to_free(self._files, self._seen, self._missing)}
        listing = self.form.dtStorageList
        listing.blockSignals(True)
        for row in range(listing.count()):
            item = listing.item(row)
            item.setSelected(self._files[item.data(Qt.ItemDataRole.UserRole)].key in chosen and not item.isHidden())
        listing.blockSignals(False)
        self._sync_checks()

    def _sort_changed(self, _index: int) -> None:
        if self._files and not self._busy:
            self._fill_rows(keep=self._chosen())

    def _show_meter(self, used: int, limit: int | None) -> None:
        self.meter.setVisible(bool(limit))
        if limit:
            self.meter.set_share(used / limit)

    def _clear_list(self) -> None:
        """Empty the list without its selection signal running against the file tuple being replaced."""
        listing = self.form.dtStorageList
        listing.blockSignals(True)
        listing.clear()
        listing.blockSignals(False)

    def _show_list(self, visible: bool) -> None:
        self.form.dtStorageList.setVisible(visible)
        self._tools.setVisible(visible)
        self._filler.changeSize(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum if visible else QSizePolicy.Policy.Expanding
        )
        layout = self.form.layout()
        layout.setStretch(layout.indexOf(self._filler), 0 if visible else 1)
        layout.invalidate()

    def _sync_checks(self) -> None:
        """Ticked rows are the selected rows; the check mark and the selection style follow each other."""
        listing = self.form.dtStorageList
        listing.blockSignals(True)
        for row in range(listing.count()):
            item = listing.item(row)
            item.setCheckState(Qt.CheckState.Checked if item.isSelected() else Qt.CheckState.Unchecked)
        listing.blockSignals(False)
        self._selection_changed()

    def _filter_rows(self, text: str) -> None:
        needle = text.strip().casefold()
        listing = self.form.dtStorageList
        for row in range(listing.count()):
            item = listing.item(row)
            name = self._files[item.data(Qt.ItemDataRole.UserRole)].name
            item.setHidden(bool(needle) and needle not in name.casefold())

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
        self.select_unused.setEnabled(
            not self._busy
            and self._file_keys is not None
            and any(item.key not in self._file_keys for item in self._files)
        )

    # -- listing --------------------------------------------------------------------------------------------------
    def key_changed(self) -> None:
        """Discard the old key's presentation/consent immediately and re-read an open panel."""
        self._revision += 1
        self._listed_key = None
        self._free, self._seen = {}, {}
        self._files, self._local, self._file_keys = (), None, None
        self._needed = self._missing = None
        self._note = ""
        self._disarm()
        self._clear_list()
        self._selection_changed()
        self._show_list(False)
        self.meter.hide()
        self._error("")
        if self.overlay.isHidden() or self.runner.closed:
            return
        self.form.dtStorageStatus.setText("Reading DT+ storage…")
        if self._busy:
            self._refresh_pending = True
        else:
            self.refresh()

    def _finished(self, revision, result, failure, *, deleting=False) -> None:
        if revision != self._revision:
            self._set_busy(False)
            if deleting:
                self._on_changed()  # an already admitted deletion may have completed on the previous key
            pending, self._refresh_pending = self._refresh_pending, False
            if pending and not self.overlay.isHidden() and not self.runner.closed:
                self.refresh()
            if deleting and failure is not None:
                self._error(
                    "Deletion on the previous key did not finish successfully. Check that key's storage before retrying."
                )
            return
        if deleting:
            self._deleted(result, failure)
        else:
            self._listed(result, failure)

    def refresh(self) -> None:
        if self._busy or self.runner.closed:
            return
        self._error("")
        self._files = ()
        self._local = None
        self._seen = {}
        self.select_unused.setVisible(False)
        self.select_free.setVisible(False)
        self._clear_list()
        self._show_list(False)
        self.meter.setVisible(False)
        self.form.dtStorageStatus.setText("Reading DT+ storage…")
        self._set_busy(True)
        revision = self._revision
        self.runner.submit(self._read(), lambda result, failure: self._finished(revision, result, failure))

    async def _read(self) -> tuple[str | None, StorageListing]:
        """Lookup sized for the key that is selected right now; its identity comes back with the answer."""
        identity = await self._active_credential()
        key = identity[0] if identity else None
        listing = await storage_listing(self.state_dir, self._probe_content(key), saved=self._saved())
        return key, listing

    def _probe_content(self, key: str | None) -> AssetContent:
        """A made-up file bigger than the free space, so DT+ answers with its over-limit list when the key is full.

        The hash is random per lookup and never stored; nothing is uploaded and any upload URL DT+ returns is ignored."""
        return probe_content(self._free.get(key) if key is not None else None)

    def _listed(self, result: tuple[str | None, StorageListing] | None, failure) -> None:
        self._set_busy(False)
        key, listing = result if result is not None else (None, None)
        self._listed_key = key
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
        self._local = listing.local_keys
        self._file_keys = listing.file_keys
        self._seen = dict(listing.local_seen)
        self.select_unused.setVisible(self._file_keys is not None and bool(listing.files))
        if key is not None and listing.free_space is not None:
            self._free[key] = listing.free_space
        self._show_list(bool(listing.files))
        total = sum(item.size for item in listing.files)
        count = _bold(_files(len(listing.files)))
        if listing.free_space is not None:
            # the account limit is stored + free; DT+ does not state it, so this is derived
            limit = _bold(gigabytes(total + listing.free_space))
            line = f"{_bold(gigabytes(listing.free_space))} free of {limit}, {count} stored."
            self._show_meter(total, total + listing.free_space)
        else:
            line = f"{count} stored, {_bold(gigabytes(total))}. Free space unknown."
        if self._file_keys is not None and listing.files:
            cloud_only = sum(item.key not in self._file_keys for item in listing.files)
            line += f" {_bold(str(cloud_only))} Cloud only."
        self._missing = shortfall(self._needed, listing.free_space) if self._needed is not None else None
        if self._needed is not None:
            if self._missing:
                line += (
                    f" The generation needs {_bold(storage_size(self._needed))}; "
                    f"{_bold(storage_size(self._missing))} more must be freed."
                )
            elif self._missing == 0:
                line += f" The generation needs {_bold(storage_size(self._needed))}, which now fits."
        self.select_free.setVisible(bool(self._missing) and bool(listing.files))
        skipped = f" {listing.skipped} entries could not be read and are not shown." if listing.skipped else ""
        self.form.dtStorageStatus.setText(f"{lead}{line}{skipped}")
        self._fill_rows(keep=())

    # -- delete ---------------------------------------------------------------------------------------------------
    def _delete_clicked(self) -> None:
        chosen = self._chosen()
        if self._busy or not chosen or self.runner.closed or self._listed_key is None:
            return
        total = gigabytes(sum(item.size for item in chosen))
        if not self._armed:
            self._armed = True
            self.form.dtStorageDelete.setText(f"Confirm: delete {_files(len(chosen))}, {total}")
            self._mark_danger(True)
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
        revision = self._revision
        self.runner.submit(
            delete_storage(self.state_dir, chosen, saved=self._saved(), expected_owner=self._listed_key),
            lambda result, failure: self._finished(revision, result, failure, deleting=True),
        )

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

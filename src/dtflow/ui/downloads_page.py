"""The Downloads workspace page: Links / IDs tab and the downloads list (#465, epic #327).

Presentation and wiring only. Resolving, downloading and converting live in ``dtflow.services.downloads``; all of it
runs off the GUI thread. Worker threads write progress onto ``DownloadItem`` objects and a timer, running only while
something is active, redraws the rows. Nothing downloads until the user clicks Download; cloud Prepare is manual unless
the user has enabled the persistent Auto-prepare preference.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QCheckBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.settings import (
    SettingsError, load_downloads_auto_prepare, save_downloads_auto_prepare,
)
from dtflow.services.lora_naming import LoraPrepareMetadata
from dtflow.services import civitai
from dtflow.services import downloads as engine
from dtflow.services import civitai_gallery as gallery_engine
from dtflow.ui.civitai_gallery_view import GalleryTab
from dtflow.ui.runner import AsyncRunner

log = logging.getLogger(__name__)

STATE_TEXT = {
    engine.QUEUED: "Queued",
    engine.DOWNLOADING: "Downloading",
    engine.CONVERTING: "Converting",
    engine.CONVERTED: "Converted",
    engine.NOT_CONVERTIBLE: "Downloaded, not convertible",
    engine.FAILED: "Failed",
    engine.CANCELLED: "Cancelled",
    engine.INTERRUPTED: "Not converted",
}
STATE_COLOURS = {  # the design's status colours (color.success, color.warning, color.error, color.disabled)
    engine.CONVERTED: "#78c97c",
    engine.NOT_CONVERTIBLE: "#ffbf59",
    engine.INTERRUPTED: "#ffbf59",
    engine.FAILED: "#ff8179",
    engine.CANCELLED: "#777777",
}
REFRESH_MS = 300
CHECKED_COLUMN, NAME_COLUMN = 0, 1


def format_size(size: int | None) -> str:
    if not size:
        return "–"
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    return f"{max(1, round(size / 1024**2))} MB"


class DownloadsPage(QObject):
    """Controller of ``downloads_page.ui``. ``changed`` fires when the aggregate state (pill, activity line) moves."""

    changed = Signal()

    def __init__(
        self,
        form: QWidget,
        *,
        runner: AsyncRunner,
        state_dir: Path,
        read_token: Callable[[], str | None],
        token_state: Callable[[], str],
        cloud_ready: Callable[[], bool],
        open_credentials: Callable[[], None],
        open_prepare: Callable[[list[str]], None],
        auto_prepare: Callable[[str, LoraPrepareMetadata, Callable[[str], None]], str | None] | None = None,
    ):
        super().__init__(form)
        self.form = form
        self._runner, self._state = runner, Path(state_dir)
        self._read_token, self._token_state = read_token, token_state
        self._cloud_ready, self._open_credentials, self._open_prepare = cloud_ready, open_credentials, open_prepare
        self._auto_prepare = auto_prepare
        try:
            self._auto_prepare_enabled = load_downloads_auto_prepare(self._state)
            self._auto_prepare_settings_problem = ""
        except SettingsError as exc:
            self._auto_prepare_enabled = False
            self._auto_prepare_settings_problem = str(exc)
        self.items: list[engine.DownloadItem] = []
        self._entries: list[engine.ResolvedEntry] = []
        self._manager: civitai.DownloadManager | None = None
        self._loaded = False
        self._resolving = False
        self._signature: tuple = ()
        self.finished_unseen = 0
        self.failed_unseen = 0
        self._batch: list[engine.DownloadItem] = []  # the items of the running batch, for the aggregate percent
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        runner.shutdown_started.connect(self.shutdown)

        form.resolveButton.clicked.connect(self.resolve)
        form.downloadSelectedButton.clicked.connect(self.download_selected)
        form.downloadsTokenLabel.clicked.connect(lambda: open_credentials())
        form.downloadsTable.setVisible(False)  # the list is a toggle panel, closed until asked for
        form.downloadsListToggle.toggled.connect(self._toggle_list)
        self._toggle_list(False)
        # The token chip sits at the right of the tab bar, as in the design.
        form.downloadsTabs.setCornerWidget(form.downloadsTokenBar, Qt.Corner.TopRightCorner)
        form.downloadsTokenBar.show()
        form.resolvedTable.itemChanged.connect(self._selection_changed)
        self.gallery = GalleryTab(
            form, runner=runner, download=self._download_versions,
            state_of=self._card_state, version_state=self._version_state,
        )
        form.downloadsTabs.currentChanged.connect(self._tab_changed)
        for checkbox in (form.linksAutoPrepareCheck, form.galleryAutoPrepareCheck):
            checkbox.setChecked(self._auto_prepare_enabled)
            checkbox.toggled.connect(self._toggle_auto_prepare)
        if self._auto_prepare_settings_problem:
            form.downloadStatus.setText(self._auto_prepare_settings_problem)
        self._configure(form.resolvedTable, {0: 30, 1: 200, 2: 180, 3: 70, 4: 140, 5: 150}, 6)
        self._configure(form.downloadsTable, {0: 260, 1: 200, 3: 270}, 2)
        actions = form.downloadsTable.horizontalHeaderItem(3)
        if actions is not None:
            actions.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.token_changed()

    def _toggle_auto_prepare(self, enabled: bool) -> None:
        """One durable preference, mirrored by the two standard Qt Designer checkboxes."""
        previous = self._auto_prepare_enabled
        try:
            save_downloads_auto_prepare(self._state, enabled)
        except (SettingsError, OSError) as exc:
            self.form.downloadStatus.setText(f"Auto-prepare preference not saved: {exc}")
            enabled = previous
        else:
            self._auto_prepare_enabled = enabled
        for checkbox in (self.form.linksAutoPrepareCheck, self.form.galleryAutoPrepareCheck):
            was_blocked = checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(was_blocked)

    # ------------------------------------------------------------------------------------------------- table setup
    @staticmethod
    def _configure(table: QTableWidget, widths: dict[int, int], stretch: int) -> None:
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(40)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for column, width in widths.items():
            table.setColumnWidth(column, width)

    def _toggle_list(self, shown: bool) -> None:
        images = Path(__file__).parents[1] / "resources" / "images"
        name = "controls/chevron-down.svg" if shown else "gallery/chevron-right.svg"
        toggle = self.form.downloadsListToggle
        toggle.setIcon(QIcon(str(images / name)))
        toggle.setAccessibleName("Hide downloads list" if shown else "Show downloads list")
        self.form.downloadsTable.setVisible(shown)

    def _summarise(self) -> None:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.state] = counts.get(item.state, 0) + 1
        running = counts.get(engine.DOWNLOADING, 0) + counts.get(engine.CONVERTING, 0)
        parts = [
            (running, "running"),
            (counts.get(engine.QUEUED, 0), "queued"),
            (counts.get(engine.CONVERTED, 0), "converted"),
            (counts.get(engine.NOT_CONVERTIBLE, 0) + counts.get(engine.INTERRUPTED, 0), "not converted"),
            (counts.get(engine.FAILED, 0), "failed"),
            (counts.get(engine.CANCELLED, 0), "cancelled"),
        ]
        text = ", ".join(f"{count} {label}" for count, label in parts if count) or "No downloads yet."
        if self.form.downloadsListSummary.text() != text:
            self.form.downloadsListSummary.setText(text)

    # ----------------------------------------------------------------------------------------------- page lifecycle
    def activate(self) -> None:
        """The page was opened: restore the durable rows once, clear the pill's finished/failed marks."""
        self.finished_unseen = self.failed_unseen = 0
        self.token_changed()
        if not self._loaded:
            self._loaded = True
            self._runner.submit(engine.load_items(self._state), self._restored)
        self._render()
        self._tab_changed()
        self.changed.emit()

    def _tab_changed(self, *_args) -> None:
        if self.form.downloadsTabs.currentWidget() is self.form.browseTab:
            self.gallery.ensure_loaded()

    def _restored(self, rows, failure) -> None:
        if failure is not None or not rows:
            return
        known = {item.key for item in self.items}
        for row in rows:
            if row.key not in known:
                self.items.append(row)
                known.add(row.key)
        self._render(force=True)

    def token_changed(self) -> None:
        state = self._token_state()
        text, look = {
            "present": ("Civitai token: saved \u2713", "ok"),
            "absent": ("Civitai token: missing", "warning"),
            "unavailable": ("Civitai token: secure storage unavailable", "warning"),
        }.get(state, ("Civitai token: not checked", "neutral"))
        label = self.form.downloadsTokenLabel
        label.setText(text)
        if label.property("state") != look:
            label.setProperty("state", look)
            label.style().unpolish(label)
            label.style().polish(label)

    def shutdown(self) -> None:
        for item in self.items:
            item.request_cancel()
        if self._manager is not None:
            self._manager.shutdown()
        self._timer.stop()

    # -------------------------------------------------------------------------------------------------- resolving
    def resolve(self) -> None:
        if self._resolving or self._runner.closed:
            return
        text = self.form.linksEdit.toPlainText()
        if not text.strip():
            self.form.resolveStatus.setText("Paste a Civitai link or a version id first.")
            return
        self._resolving = True
        self.form.resolveButton.setEnabled(False)
        self.form.resolveStatus.setText("Resolving…")
        self.form.downloadSelectedButton.setEnabled(False)
        read_token = self._read_token

        async def work():
            import asyncio

            def blocking():
                try:
                    token = read_token()
                except Exception:  # no secure storage: public metadata may still resolve
                    token = None
                return engine.resolve_text(text, token)

            return await asyncio.to_thread(blocking)

        self._runner.submit(work(), self._resolved)

    def _resolved(self, result, failure) -> None:
        self._resolving = False
        self.form.resolveButton.setEnabled(True)
        if failure is not None or result is None:
            self.form.resolveStatus.setText("Resolving failed. Check the connection and try again.")
            diagnostic_event(log, "downloads.resolve_failed", level=logging.WARNING)
            return
        clean, entries = result
        if clean != self.form.linksEdit.toPlainText().strip():
            self.form.linksEdit.setPlainText(clean)  # token-bearing or odd spellings are replaced, never kept
        self._entries = entries
        self._fill_resolved()
        ready = sum(1 for entry in entries if entry.downloadable)
        self.form.resolveStatus.setText(
            f"{len(entries)} found, {ready} ready to download." if entries else "Nothing found."
        )

    def _fill_resolved(self) -> None:
        table = self.form.resolvedTable
        table.blockSignals(True)
        table.setRowCount(len(self._entries))
        active = {item.key for item in self.items if item.active}
        for row, entry in enumerate(self._entries):
            version = entry.version
            check = QTableWidgetItem()
            busy = version is not None and (version.version_id, version.file.id) in active
            if entry.downloadable and not busy:
                check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                check.setCheckState(Qt.CheckState.Checked)
            else:
                check.setFlags(Qt.ItemFlag.NoItemFlags)
            table.setItem(row, CHECKED_COLUMN, check)
            if version is None:
                cells = [entry.label, "–", "–", "–", "–"]
            else:
                cells = [
                    entry.label,
                    version.file.name,
                    format_size(version.file.size_bytes),
                    version.base_model or "–",
                    (entry.family.label + (", experimental" if entry.family.experimental else ""))
                    if entry.family
                    else "None",
                ]
            if busy:
                status = "Already in the downloads list"
            elif entry.problem:
                status = entry.problem
            else:
                status = "Ready"
                if version and version.availability and version.availability.casefold() != "public":
                    status = f"Ready ({version.availability})"
            cells.append(status)
            for offset, text in enumerate(cells, 1):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if offset == 6:
                    cell.setForeground(self._warning_color(entry) if entry.problem else QColor("#78c97c"))
                elif offset == 5 and entry.family:
                    cell.setForeground(QColor("#b9dcf7"))
                elif version is None or (offset == 5 and not entry.family):
                    cell.setForeground(QColor("#777777"))
                table.setItem(row, offset, cell)
        table.blockSignals(False)
        self._selection_changed()

    @staticmethod
    def _warning_color(entry: engine.ResolvedEntry):
        return QColor("#ffbf59" if entry.version is not None else "#ff8179")  # warning / error status colours

    def _selected_entries(self) -> list[engine.ResolvedEntry]:
        table = self.form.resolvedTable
        chosen = []
        for row, entry in enumerate(self._entries):
            cell = table.item(row, CHECKED_COLUMN)
            if cell is not None and entry.downloadable and cell.checkState() == Qt.CheckState.Checked:
                chosen.append(entry)
        return chosen

    def _selection_changed(self, *_args) -> None:
        count = len(self._selected_entries())
        button = self.form.downloadSelectedButton
        button.setText(f"Download selected ({count})" if count else "Download selected")
        button.setEnabled(count > 0 and not self._runner.closed)
        if count:
            reason = "One action: download, then convert automatically."
        elif not self._entries:
            reason = "Resolve a link first."
        else:
            first = next((entry.problem for entry in self._entries if entry.problem), None)
            reason = f"Nothing to download: {first}" if first else "Tick at least one entry."
        self.form.downloadStatus.setText(reason)
        button.setToolTip(reason)

    # ------------------------------------------------------------------------------------------------ downloading
    def download_selected(self) -> None:
        self._enqueue(self._selected_entries())

    def _download_versions(self, versions: list[civitai.CivitaiVersion]) -> int:
        """Download from the gallery: the same path as Links / IDs, entered by a resolved version."""
        return self._enqueue([engine.entry_for(version) for version in versions])

    def _version_state(self, version: civitai.CivitaiVersion) -> str:
        """State belongs to an exact file/revision, not to the surrounding Civitai model."""
        for item in self.items:
            if item.version_id != version.version_id or item.key[1] not in (None, version.file.id):
                continue
            if item.active:
                return "Queued" if item.state == engine.QUEUED else (
                    "Converting" if item.state == engine.CONVERTING else "Downloading"
                )
            return {
                engine.CONVERTED: "In your LoRAs",
                engine.NOT_CONVERTIBLE: "Not convertible",
                engine.FAILED: "Failed",
                engine.INTERRUPTED: "Not converted",
                engine.CANCELLED: "Cancelled",
            }.get(item.state, "")
        return ""

    def _card_state(self, card: gallery_engine.GalleryCard) -> str:
        """A card may have several individually downloaded versions, across different families."""
        states = [self._version_state(option.version) for option in card.options]
        done = sum(state == "In your LoRAs" for state in states)
        if done:
            return f"{done} of {len(states)} in your LoRAs"
        active = sum(state in ("Queued", "Downloading", "Converting") for state in states)
        if active:
            return f"{active} downloading"
        return ""

    def _enqueue(self, entries: list[engine.ResolvedEntry]) -> int:
        if self._runner.closed:
            return 0
        started = 0
        for entry in entries:
            item = engine.item_for(entry)
            if any(other.key == item.key and other.active for other in self.items):
                continue
            self.items = [other for other in self.items if other.key != item.key]  # a finished row is replaced
            self.items.insert(0, item)
            self._start(item)
            started += 1
        if started:
            diagnostic_event(log, "downloads.started", data={"count": started})
            self._fill_resolved()
            self._render(force=True)
        return started

    def _start(self, item: engine.DownloadItem) -> None:
        if self._manager is None:
            self._manager = civitai.DownloadManager(self._state)
        manager = self._manager
        if item not in self._batch:
            self._batch.append(item)
        item.state, item.detail, item.done, item.total = engine.QUEUED, engine.QUEUED_DETAIL, 0, None
        self._timer.start()
        coroutine = engine.run_item(self._state, item, manager, self._read_token)
        try:
            self._runner.submit(coroutine, lambda _result, _failure: self._finished(item))
        except RuntimeError:
            item.state, item.detail = engine.CANCELLED, "DTFlow is closing."
        self.changed.emit()

    def _finished(self, item: engine.DownloadItem) -> None:
        if item.state == engine.CONVERTED:
            self.finished_unseen += 1
        elif item.state in (engine.FAILED, engine.NOT_CONVERTIBLE):
            self.failed_unseen += 1
        if not any(other.active for other in self.items):
            self._batch.clear()
        diagnostic_event(log, "downloads.item_finished", data={"state": item.state})
        self._render(force=True)
        if item.state == engine.CONVERTED and self._auto_prepare_enabled:
            self._auto_prepare_converted(item)
        self.changed.emit()

    def _auto_prepare_converted(self, item: engine.DownloadItem) -> None:
        """Only freshly converted files are eligible; no backend/account switch or automatic retries."""
        if not self._cloud_ready():
            item.auto_prepare_status = "Auto-prepare needs DT+ Cloud; converted file kept."
            self._render(force=True)
            return
        if self._auto_prepare is None or item.prepare_metadata is None:
            item.auto_prepare_status = "Auto-prepare unavailable; use Prepare manually."
            self._render(force=True)
            return

        # Mark the row before looking up the retained file. No duplicate manual Prepare
        # can open while this same file's automatic preparation is pending or running.
        item.auto_prepare_phase = "preparing"
        item.auto_prepare_status = "Preparing on DT+…"
        self._render(force=True)

        async def work():
            return await engine.retained_path(self._state, item)

        def resolved(path, failure) -> None:
            if not self._auto_prepare_enabled or self._runner.closed:
                item.auto_prepare_phase = ""
                item.auto_prepare_status = "Auto-prepare stopped before upload; use Prepare manually."
                self._render(force=True)
                return  # disabling the preference before admission must not upload
            if failure is not None or path is None:
                item.auto_prepare_phase = ""
                item.auto_prepare_status = "Auto-prepare could not locate the converted file; use Prepare manually."
                self._render(force=True)
                return

            def settled(message: str) -> None:
                item.auto_prepare_phase = "prepared" if message == "Prepared" else ""
                item.auto_prepare_status = "Prepared on DT+" if message == "Prepared" else message
                self._render(force=True)

            problem = self._auto_prepare(str(path), item.prepare_metadata, settled)
            if problem is not None:
                item.auto_prepare_phase = ""
                item.auto_prepare_status = f"Auto-prepare not started: {problem}"
            else:
                item.auto_prepare_status = "Preparing on DT+…"
            self._render(force=True)

        self._runner.submit(work(), resolved)

    def cancel(self, item: engine.DownloadItem) -> None:
        item.request_cancel()
        item.detail = "Cancelling…"
        self._render(force=True)

    def cancel_all(self) -> None:
        for item in self.items:
            if item.active:
                self.cancel(item)

    def retry(self, item: engine.DownloadItem) -> None:
        if item.version is not None:
            self._start(item)
            self._render(force=True)
            return
        item.state, item.detail = engine.QUEUED, "Looking the file up again"
        read_token = self._read_token

        async def work():
            import asyncio

            def blocking():
                try:
                    token = read_token()
                except Exception:
                    token = None
                return engine.reresolve(item, token)

            return await asyncio.to_thread(blocking)

        def done(entry, failure) -> None:
            if failure is not None or entry is None or not entry.downloadable:
                item.state = engine.FAILED
                item.detail = (entry.problem if entry is not None else None) or "Civitai could not be reached."
                self._render(force=True)
                return
            item.version, item.family = entry.version, engine.item_for(entry).family
            self._start(item)
            self._render(force=True)

        self._runner.submit(work(), done)
        self._render(force=True)

    def remove(self, item: engine.DownloadItem) -> None:
        async def work():
            return await engine.remove_item(self._state, item)

        def done(_result, failure) -> None:
            if failure is not None:
                item.detail = str(failure) or "The file could not be removed."
            else:
                self.items = [other for other in self.items if other is not item]
            self._render(force=True)

        self._runner.submit(work(), done)

    def prepare(self, item: engine.DownloadItem) -> None:
        if not self._cloud_ready() or item.auto_prepare_phase == "preparing":
            return

        async def work():
            return await engine.retained_path(self._state, item)

        def done(path, failure) -> None:
            if failure is not None or path is None:
                item.detail = "The converted file is no longer in DTFlow's local store."
                self._render(force=True)
                return
            self._open_prepare([str(path)])

        self._runner.submit(work(), done)

    # --------------------------------------------------------------------------------------------------- display
    @staticmethod
    def _fraction(item: engine.DownloadItem) -> float:
        """How far one file's download is: 0 while queued, bytes received while downloading, 1 once it is on disk."""
        if item.state == engine.QUEUED:
            return 0.0
        if item.state == engine.DOWNLOADING:
            total = item.total or (item.version.file.size_bytes if item.version else None)
            return min(item.done / total, 1.0) if total else 0.0
        return 1.0

    def stats(self) -> dict[str, int]:
        active = [item for item in self.items if item.active]
        batch = self._batch or active
        percent = round(100 * sum(self._fraction(item) for item in batch) / len(batch)) if batch else 0
        return {
            "active": len(active),
            "transferring": sum(1 for item in active if item.state in (engine.DOWNLOADING, engine.CONVERTING)),
            "downloading": sum(1 for item in active if item.state == engine.DOWNLOADING),
            "percent": percent,
            "files": len(batch),
            "finished": sum(1 for item in batch if not item.active),
        }

    def refresh_actions(self) -> None:
        """The backend changed: Prepare… follows the cloud-only rule."""
        self._render(force=True)

    def _tick(self) -> None:
        self._render()
        if not any(item.active for item in self.items):
            self._timer.stop()
        self.changed.emit()

    @staticmethod
    def _detail(item: engine.DownloadItem) -> str:
        if item.state == engine.DOWNLOADING:
            speed = f" · {item.speed / 1024**2:.1f} MB/s" if item.speed > 0 else ""
            if item.total:
                done, total = item.done / 1024**2, item.total / 1024**2
                return f"{item.done * 100 // item.total}% · {done:.0f} of {total:.0f} MB{speed}"
            return f"{format_size(item.done)} downloaded{speed}"
        if item.state == engine.CONVERTING and item.total:
            return f"{item.detail} · {item.done} of {item.total} modules"
        return f"{item.detail} · {item.auto_prepare_status}" if item.auto_prepare_status else item.detail

    def _detail_cell(self, item: engine.DownloadItem, cloud: bool) -> QWidget:
        """Detail as in the design: a thin bar plus text while downloading, plain text otherwise."""
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)
        bar = QProgressBar(holder)
        bar.setObjectName("downloadsRowBar")
        bar.setTextVisible(False)
        bar.setFixedWidth(120)
        bar.setRange(0, 100)
        label = QLabel(holder)
        label.setObjectName("downloadsRowDetail")
        layout.addWidget(bar)
        layout.addWidget(label, 1)
        holder.setProperty("bar", bar)
        self._fill_detail(holder, item, cloud)
        return holder

    def _fill_detail(self, holder: QWidget, item: engine.DownloadItem, cloud: bool) -> None:
        bar: QProgressBar = holder.findChild(QProgressBar, "downloadsRowBar")
        label: QLabel = holder.findChild(QLabel, "downloadsRowDetail")
        text = self._detail(item)
        look = "dim"
        if item.state == engine.FAILED or item.auto_prepare_status.startswith("Auto-prepare failed"):
            look = "error"
        elif item.state == engine.DOWNLOADING:
            look = ""
        if item.state == engine.CONVERTED and not cloud and not item.auto_prepare_status:
            text = engine.PREPARE_NEEDS_CLOUD
        bar.setVisible(item.state == engine.DOWNLOADING)
        if item.state == engine.DOWNLOADING:
            bar.setValue(item.done * 100 // item.total if item.total else 0)
        if label.text() != text:
            label.setText(text)
            label.setToolTip(text)
        if label.property("state") != look:
            label.setProperty("state", look)
            label.style().unpolish(label)
            label.style().polish(label)

    def _render(self, *, force: bool = False) -> None:
        table = self.form.downloadsTable
        self.gallery.refresh_states()
        self._summarise()
        signature = tuple(
            (id(item), item.state, item.auto_prepare_phase, item.auto_prepare_status, item.detail if force else "")
            for item in self.items
        )
        cloud = self._cloud_ready()
        signature += (cloud,)
        if force or signature != self._signature:
            self._signature = signature
            table.setRowCount(len(self.items))
            for row, item in enumerate(self.items):
                name = QTableWidgetItem(item.name)
                name.setToolTip(item.name)
                table.setItem(row, 0, name)
                label = (
                    "Preparing" if item.auto_prepare_phase == "preparing"
                    else "Prepared" if item.auto_prepare_phase == "prepared"
                    else STATE_TEXT[item.state]
                )
                state = QTableWidgetItem(label)
                colour = STATE_COLOURS.get(item.state)
                if colour:
                    state.setForeground(QColor(colour))
                table.setItem(row, 1, state)
                table.setCellWidget(row, 2, self._detail_cell(item, cloud))
                table.setCellWidget(row, 3, self._actions(item, cloud))
            return
        for row, item in enumerate(self.items):
            if item.active:  # progress only: the cells exist, the buttons stay
                holder = table.cellWidget(row, 2)
                if holder is not None:
                    self._fill_detail(holder, item, cloud)

    def _actions(self, item: engine.DownloadItem, cloud: bool) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)
        layout.addStretch(1)  # actions sit at the right edge, as in the design

        def button(text: str, action, *, enabled: bool = True, tip: str = "", role: str | None = None) -> None:
            widget = QPushButton(text, holder)
            widget.setObjectName("downloadsRowButton")
            widget.setEnabled(enabled)
            widget.setAccessibleName(f"{text} {item.name}")
            if tip:
                widget.setToolTip(tip)
            if role:
                widget.setProperty("role", role)
            widget.clicked.connect(lambda _checked=False: action(item))
            layout.addWidget(widget)

        state = item.state
        text = item.detail.casefold()
        if item.active:
            button("Cancel", self.cancel)
        elif state == engine.CONVERTED:
            button(
                "Prepare…",
                self.prepare,
                enabled=cloud and item.auto_prepare_phase != "preparing",
                tip=(
                    "Automatic preparation is running for this file."
                    if item.auto_prepare_phase == "preparing"
                    else "Open the Prepare editor with this file."
                    if cloud else engine.PREPARE_NEEDS_CLOUD
                ),
                role="primary" if cloud else None,
            )
        elif state == engine.NOT_CONVERTIBLE:
            button("Remove", self.remove, tip="Permanently delete the kept file.")
        elif state == engine.FAILED and ("removed" in text or "not found" in text or "no longer" in text):
            button("Remove", self.remove)  # gone at Civitai: a retry cannot help
        elif state == engine.FAILED:
            button("Retry", self.retry)
            if "token" in text or "login" in text:
                layout.addWidget(self._credentials_button(holder))
        else:  # cancelled, interrupted
            button("Retry", self.retry)
            button("Remove", self.remove)
        return holder

    def _credentials_button(self, parent: QWidget) -> QPushButton:
        widget = QPushButton("Open credentials", parent)
        widget.setObjectName("downloadsRowButton")
        widget.clicked.connect(lambda _checked=False: self._open_credentials())
        return widget

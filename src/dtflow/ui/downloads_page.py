"""The Downloads workspace page: Links / IDs tab and the downloads list (#465, epic #327).

Presentation and wiring only. Resolving, downloading and converting live in ``dtflow.services.downloads``; all of it
runs off the GUI thread. Worker threads write progress onto ``DownloadItem`` objects and a timer, running only while
something is active, redraws the rows. Nothing downloads until the user clicks Download, and nothing uploads to DT+
until the user clicks Prepare.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from dtflow.diagnostics import event as diagnostic_event
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
    engine.NOT_CONVERTIBLE: "Not convertible",
    engine.FAILED: "Failed",
    engine.CANCELLED: "Cancelled",
    engine.INTERRUPTED: "Not converted",
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
    ):
        super().__init__(form)
        self.form = form
        self._runner, self._state = runner, Path(state_dir)
        self._read_token, self._token_state = read_token, token_state
        self._cloud_ready, self._open_credentials, self._open_prepare = cloud_ready, open_credentials, open_prepare
        self.items: list[engine.DownloadItem] = []
        self._entries: list[engine.ResolvedEntry] = []
        self._manager: civitai.DownloadManager | None = None
        self._loaded = False
        self._resolving = False
        self._signature: tuple = ()
        self.finished_unseen = 0
        self.failed_unseen = 0
        self.batch_total = 0
        self.batch_done = 0
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        runner.shutdown_started.connect(self.shutdown)

        form.resolveButton.clicked.connect(self.resolve)
        form.downloadSelectedButton.clicked.connect(self.download_selected)
        form.downloadsCredentialsButton.clicked.connect(lambda: open_credentials())
        form.downloadsTable.setVisible(False)  # the list is a toggle panel, closed until asked for
        form.downloadsListToggle.toggled.connect(self._toggle_list)
        self._toggle_list(False)
        # The token chip sits at the right of the tab bar, as in the design.
        form.downloadsTabs.setCornerWidget(form.downloadsTokenBar, Qt.Corner.TopRightCorner)
        form.downloadsTokenBar.show()
        form.resolvedTable.itemChanged.connect(self._selection_changed)
        self.gallery = GalleryTab(form, runner=runner, download=self._download_versions, state_of=self._card_state)
        form.downloadsTabs.currentChanged.connect(self._tab_changed)
        self._configure(form.resolvedTable, {0: 30, 1: 200, 2: 180, 3: 70, 4: 140, 5: 150}, 6)
        self._configure(form.downloadsTable, {0: 220, 1: 150, 2: 130, 4: 270}, 3)
        self.token_changed()

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
        label.setToolTip("Downloads need a saved token." if look == "warning" else "")
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
                    (entry.family.label + (" (experimental)" if entry.family.experimental else ""))
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
                if offset == 6 and entry.problem:
                    cell.setForeground(self._warning_color(entry))
                table.setItem(row, offset, cell)
        table.blockSignals(False)
        self._selection_changed()

    @staticmethod
    def _warning_color(entry: engine.ResolvedEntry):
        from PySide6.QtGui import QColor

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

    def _card_state(self, card: gallery_engine.GalleryCard) -> str:
        """What the downloads list says about a card's file: empty when it can be downloaded."""
        for item in self.items:
            if item.key == card.key or (item.version_id == card.version_id and item.key[1] is None):
                if item.active:
                    return "Downloading" if item.state != engine.QUEUED else "Queued"
                return {
                    engine.CONVERTED: "In your LoRAs",
                    engine.NOT_CONVERTIBLE: "Not convertible",
                    engine.FAILED: "Failed · retry in list",
                    engine.INTERRUPTED: "Not converted",
                }.get(item.state, "")
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
        self.batch_total += 1
        item.state, item.detail, item.done, item.total = engine.QUEUED, "Waiting for a free slot", 0, None
        self._timer.start()
        coroutine = engine.run_item(self._state, item, manager, self._read_token)
        try:
            self._runner.submit(coroutine, lambda _result, _failure: self._finished(item))
        except RuntimeError:
            item.state, item.detail = engine.CANCELLED, "DTFlow is closing."
        self.changed.emit()

    def _finished(self, item: engine.DownloadItem) -> None:
        self.batch_done += 1
        if item.state == engine.CONVERTED:
            self.finished_unseen += 1
        elif item.state in (engine.FAILED, engine.NOT_CONVERTIBLE):
            self.failed_unseen += 1
        if not any(other.active for other in self.items):
            self.batch_total = self.batch_done = 0
        diagnostic_event(log, "downloads.item_finished", data={"state": item.state})
        self._render(force=True)
        self.changed.emit()

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
        if not self._cloud_ready():
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
    def stats(self) -> dict[str, int]:
        active = [item for item in self.items if item.active]
        return {
            "active": len(active),
            "transferring": sum(1 for item in active if item.state in (engine.DOWNLOADING, engine.CONVERTING)),
            "downloading": sum(1 for item in active if item.state == engine.DOWNLOADING),
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
        if item.state == engine.DOWNLOADING and item.total:
            return f"{item.done * 100 // item.total}% · {format_size(item.done)} of {format_size(item.total)}"
        if item.state == engine.DOWNLOADING:
            return f"{format_size(item.done)} downloaded"
        if item.state == engine.CONVERTING and item.total:
            return f"{item.detail}: {item.done} of {item.total} modules"
        return item.detail

    def _render(self, *, force: bool = False) -> None:
        table = self.form.downloadsTable
        self.gallery.refresh_states()
        self._summarise()
        signature = tuple((id(item), item.state, item.detail if force else "") for item in self.items)
        cloud = self._cloud_ready()
        signature += (cloud,)
        if force or signature != self._signature:
            self._signature = signature
            table.setRowCount(len(self.items))
            for row, item in enumerate(self.items):
                detail = self._detail(item)
                if item.state == engine.CONVERTED and not cloud:
                    detail = (detail + ". " if detail else "") + engine.PREPARE_NEEDS_CLOUD
                for column, text in enumerate((item.name, item.family, STATE_TEXT[item.state], detail)):
                    cell = QTableWidgetItem(text)
                    cell.setToolTip(text)
                    table.setItem(row, column, cell)
                table.setCellWidget(row, 4, self._actions(item, cloud))
            return
        for row, item in enumerate(self.items):
            if item.active:  # progress only: the cells exist, the buttons stay
                text = self._detail(item)
                cell = table.item(row, 3)
                if cell is not None and cell.text() != text:
                    cell.setText(text)
                    cell.setToolTip(text)

    def _actions(self, item: engine.DownloadItem, cloud: bool) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

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
        if item.active:
            button("Cancel", self.cancel)
        elif state == engine.CONVERTED:
            button(
                "Prepare…",
                self.prepare,
                enabled=cloud,
                tip="Open the Prepare editor with this file." if cloud else engine.PREPARE_NEEDS_CLOUD,
                role="primary",
            )
        elif state == engine.NOT_CONVERTIBLE:
            button("Remove", self.remove, tip="Permanently delete the kept file.")
        else:  # failed, cancelled, interrupted
            button("Retry", self.retry)
            text = item.detail.casefold()
            if state == engine.FAILED and ("token" in text or "login" in text):
                layout.addWidget(self._credentials_button(holder))
            button("Remove", self.remove)
        layout.addStretch(1)
        return holder

    def _credentials_button(self, parent: QWidget) -> QPushButton:
        widget = QPushButton("Open credentials", parent)
        widget.setObjectName("downloadsRowButton")
        widget.clicked.connect(lambda _checked=False: self._open_credentials())
        return widget

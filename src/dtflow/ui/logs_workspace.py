"""Native Logs workspace over the frozen Qt-free diagnostic reader.

Queries, detail hydration and exports execute outside the GUI thread.
The Qt model stores bounded projections, never complete JSONL payloads.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFileDialog, QHeaderView, QMenu, QStyleOptionViewItem

from dtflow._diagnostic_export import export_matching
from dtflow._diagnostic_reader import PAGE_SIZE, discover, query, read_detail, scan, snapshot
from dtflow.diagnostic_projection import characteristics
from dtflow.logging_setup import LOGGER
from dtflow.ui.desktop_open import open_local_path
from dtflow.ui.logs_delegate import LogCellDelegate
from dtflow.ui.theme import load_tokens

# Distinct filter choices kept per session; a pathological session cannot grow the combo lists without bound.
FACET_LIMIT = 1000

# Sort combo entries: label, reader sort key, descending.
SORTS = (
    ("Datetime · newest first", "ts", True),
    ("Datetime · oldest first", "ts", False),
    ("Event · A to Z", "event", False),
    ("Event · Z to A", "event", True),
    ("Component · A to Z", "component", False),
    ("Component · Z to A", "component", True),
    ("Severity · most severe first", "level", True),
    ("Severity · least severe first", "level", False),
    ("Characteristic · highest first", "metric", True),
    ("Characteristic · lowest first", "metric", False),
)

# Clicking a column header toggles between the two sort entries of that column.
HEADER_SORTS = {0: (6, 7), 1: (0, 1), 2: (2, 3), 3: (8, 9)}


def session_label(number: int, session_id: str, last_write: datetime | None, *, current: bool) -> str:
    """Selector text: newest-first position, local last-write time and a short session id."""
    parts = [f"#{number}"]
    if last_write is not None:
        parts.append("last write " + last_write.strftime("%d %b %H:%M"))
    if current:
        parts.append("this session")
    parts.append(session_id[:8])
    return " · ".join(parts)


def session_facets(directory: Path, session_id: str) -> tuple[list[str], list[str]]:
    """Session-wide component and event names through the shared reader, bounded per list."""
    components, events = set(), set()

    def collect(record, _ref):
        if len(components) < FACET_LIMIT:
            components.add(record["component"])
        if len(events) < FACET_LIMIT:
            events.add(record["event"])

    def reset():
        components.clear()
        events.clear()

    scan(directory, session_id, on_record=collect, on_reset=reset)
    return sorted(components), sorted(events)


class _Signals(QObject):
    ready = Signal(int, object, object)


class _Job(QRunnable):
    def __init__(self, token, func, signals):
        super().__init__()
        self.token, self.func, self.signals = token, func, signals

    def run(self):
        try:
            value, error = self.func(), None
        except Exception as exc:
            value, error = None, f"{type(exc).__name__}: {exc}"
        self.signals.ready.emit(self.token, value, error)


class _LogModel(QAbstractTableModel):
    HEADERS = ("", "Date / Time", "Event", "Characteristics", "Actions")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.refs = []
        self.palette = load_tokens()
        self.previews = []

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def replace(self, page, previews=None):
        self.beginResetModel()
        self.rows = [record for record, _ref in page][:PAGE_SIZE]
        self.refs = [ref for _record, ref in page][:PAGE_SIZE]
        self.previews = list(previews or ())[:PAGE_SIZE]
        self.endResetModel()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        record, column = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return ""
            if column == 1:
                return record["ts"].replace("T", "\n").replace("Z", " UTC")
            if column == 2:
                return record["event"]
            if column == 3:
                if index.row() >= len(self.previews):
                    return "Loading characteristics…"
                pairs, more = self.previews[index.row()]
                summary = "   ".join(f"{key}: {value}" for key, value in pairs)
                return summary + (f"   +{more} more" if more else "")
            return "Details   Copy"
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{record['level']} · {record['component']} · {record['event']} · seq {record['seq']}"
        if role == Qt.ItemDataRole.BackgroundRole and column == 0:
            key = {
                "DEBUG": "color.disabled",
                "INFO": "color.secondary",
                "WARNING": "color.warning",
                "ERROR": "color.error",
                "CRITICAL": "color.error",
            }.get(record["level"], "color.secondary")
            return QColor(self.palette[key])
        if role == Qt.ItemDataRole.TextAlignmentRole and column == 4:
            return Qt.AlignmentFlag.AlignCenter
        return None


class LogsWorkspace(QObject):
    """Window-owned UI controller; reader and writer responsibilities stay independent."""

    def __init__(self, form, directory: Path, on_close, *, runner=None, escape_owner=None):
        super().__init__(form)
        self.form, self.directory, self.on_close = form, Path(directory), on_close
        # `escape_owner()` lets a shared confirmation/editor consume Escape before Logs does.
        self.runner, self.escape_owner = runner, escape_owner
        self.pool = QThreadPool.globalInstance()
        self.model = _LogModel(form.logsTable)
        self.form.logsTable.setModel(self.model)
        self.delegate = LogCellDelegate(self.form.logsTable)
        self.form.logsTable.setItemDelegate(self.delegate)
        self.delegate.actionRequested.connect(self._row_action)
        # Keyboard equivalents of painted actions; do not open the inspector on selection alone.
        for key, action in (("Return", "details"), ("Ctrl+Return", "details"), ("Ctrl+Shift+C", "copy")):
            shortcut = QShortcut(QKeySequence(key), self.form.logsTable)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda action=action: self._selected_action(action))
        copy_selected = QShortcut(QKeySequence.StandardKey.Copy, self.form.logsTable)
        copy_selected.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_selected.activated.connect(self.copy_selected)
        # Another Escape shortcut (the shared inline overlay) may be enabled at the same time;
        # the ambiguous activation still reaches this handler, which defers to that owner first.
        escape = QShortcut(QKeySequence("Escape"), self.form)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.escape)
        escape.activatedAmbiguously.connect(self.escape)
        self.form.logsTable.verticalHeader().hide()
        self.form.logsTable.setSelectionMode(self.form.logsTable.SelectionMode.ExtendedSelection)
        header = self.form.logsTable.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._header_sort)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for col, width in enumerate((10, 150, 245, 220, 100)):
            if col != 3:
                self.form.logsTable.setColumnWidth(col, width)
        self.form.logsTable.setWordWrap(True)
        self.form.logsTable.verticalHeader().setDefaultSectionSize(56)
        self.form.logsTable.setSelectionBehavior(self.form.logsTable.SelectionBehavior.SelectRows)
        self.form.logsTable.setEditTriggers(self.form.logsTable.EditTrigger.NoEditTriggers)
        self.form.logsDetail.hide()
        self.form.logsAdvanced.hide()
        self.form.logsNotice.hide()
        self.form.logsLevelCombo.addItems(("All levels", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))
        self.form.logsComponentCombo.addItem("All components")
        self.form.logsEventCombo.addItem("All events")
        self.form.logsOperator.addItems(("=", "!=", ">", ">=", "<", "<=", "contains"))
        for label, _sort, _descending in SORTS:
            self.form.logsSortCombo.addItem(label)
        self.form.logsFollowButton.setCheckable(True)
        self.form.logsFollowButton.setChecked(True)
        self.form.logsCloseButton.clicked.connect(on_close)
        self.form.logsRefreshButton.clicked.connect(self.refresh)
        self.form.logsMoreFiltersButton.clicked.connect(
            lambda: self.form.logsAdvanced.setVisible(not self.form.logsAdvanced.isVisible())
        )
        self.form.logsFollowButton.toggled.connect(self._toggle_follow)
        chevron = Path(__file__).parents[1] / "resources" / "images" / "gallery" / "chevron-right.svg"
        self.form.logsDetailCollapse.setIcon(QIcon(str(chevron)))
        self.form.logsDetailCollapse.setText("")
        self.form.logsDetailCollapse.setToolTip("Close event details")
        self.form.logsDetailCollapse.setAccessibleName("Close event details")
        self.form.logsDetailCollapse.clicked.connect(self.form.logsDetail.hide)
        self.form.logsResetFilters.clicked.connect(self.clear_filters)
        self.form.logsCopyVisible.clicked.connect(self.copy_visible)
        self.form.logsSaveMenu.clicked.connect(self._save_menu)
        self.form.logsTable.clicked.connect(self._row_clicked)
        self.form.logsSessionCombo.currentIndexChanged.connect(self.refresh)
        for control in (
            self.form.logsSearch,
            self.form.logsFrom,
            self.form.logsTo,
            self.form.logsField,
            self.form.logsValue,
        ):
            control.editingFinished.connect(self._filtered)
        for control in (
            self.form.logsLevelCombo,
            self.form.logsComponentCombo,
            self.form.logsEventCombo,
            self.form.logsSortCombo,
            self.form.logsOperator,
        ):
            control.currentIndexChanged.connect(self._filtered)
        self.form.logsTable.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.token = 0
        self.active = False
        self._jobs = []
        self._last = ()
        self._page = []
        self._pending = 0
        self._file_snapshot = None
        self._facets = (None, [], [])
        self._programmatic_scroll = False
        self._timer = QTimer(form)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)
        self.form.destroyed.connect(self.stop)

    def _run(self, func, callback):
        self.token += 1
        token = self.token
        signals = _Signals(self.form)

        def arrived(received, value, error):
            if received == self.token and self.active:
                callback(value, error)
            if signals in self._jobs:
                self._jobs.remove(signals)

        signals.ready.connect(arrived, Qt.ConnectionType.QueuedConnection)
        self._jobs.append(signals)
        self.pool.start(_Job(token, func, signals))

    def _session(self) -> str:
        return self.form.logsSessionCombo.currentData() or ""

    def open(self):
        self.active = True
        sessions = discover(self.directory)
        chosen = self._session()
        # UUID-based session names are not chronological. Prefer the session
        # owned by this process, then the newest active JSONL file.
        active = next(
            (
                handler._dtflow_session
                for handler in logging.getLogger(LOGGER).handlers
                if getattr(handler, "_dtflow_session", None) in sessions
            ),
            None,
        )

        def modified(session):
            try:
                return (self.directory / f"dtflow-{session}.jsonl").stat().st_mtime_ns
            except OSError:
                return -1

        written = {sid: modified(sid) for sid in sessions}
        sessions.sort(key=lambda sid: (written[sid], sid), reverse=True)
        combo = self.form.logsSessionCombo
        combo.blockSignals(True)
        combo.clear()
        # Numbered newest first (#1 is the most recently written session) so the order is readable.
        for number, sid in enumerate(sessions, 1):
            last_write = datetime.fromtimestamp(written[sid] / 1e9) if written[sid] >= 0 else None
            combo.addItem(session_label(number, sid, last_write, current=sid == active), sid)
            combo.setItemData(combo.count() - 1, sid, Qt.ItemDataRole.ToolTipRole)
        index = combo.findData(chosen) if chosen in sessions else combo.findData(active) if active else 0
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)
        self.refresh()
        self._timer.start()

    def stop(self):
        self.active = False
        self.token += 1
        self._timer.stop()

    def escape(self):
        """Escape closes the innermost Logs surface first; shared overlays keep precedence."""
        if self.escape_owner is not None and self.escape_owner():
            return
        if self.form.logsDetail.isVisible():
            self.form.logsDetail.hide()
        elif self.form.logsAdvanced.isVisible():
            self.form.logsAdvanced.hide()
        else:
            self.on_close()

    def _filters(self):
        filters = {
            "search": self.form.logsSearch.text().strip(),
            "level": self.form.logsLevelCombo.currentText(),
            "component": self.form.logsComponentCombo.currentData() or "",
        }
        if filters["level"] == "All levels":
            filters["level"] = ""
        event = self.form.logsEventCombo.currentData()
        if isinstance(event, str) and event.endswith(".*"):
            filters["family"] = event[:-2]
        elif event:
            filters["event"] = event
        for field, name in (("from_ts", "logsFrom"), ("to_ts", "logsTo")):
            val = getattr(self.form, name).text().strip()
            if val:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    raise ValueError("Time range must specify a timezone")
                filters[field] = dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        characteristic = self._characteristic()
        if characteristic and self.form.logsValue.text().strip():
            filters["field"] = characteristic
            filters["op"] = self.form.logsOperator.currentText()
            raw = self.form.logsValue.text().strip()
            try:
                filters["value"] = json.loads(raw)
            except ValueError:
                filters["value"] = raw
        return filters

    def _characteristic(self) -> tuple[str, ...]:
        characteristic = self.form.logsField.text().strip()
        if not characteristic:
            return ()
        if not characteristic.startswith("data."):
            raise ValueError("Characteristic must start with data.")
        return tuple(characteristic.split("."))

    def _sort(self):
        """Reader sort key, direction and (for characteristic sorts) the typed data.* key."""
        _label, sort, descending = SORTS[max(0, self.form.logsSortCombo.currentIndex())]
        metric = self._characteristic() if sort == "metric" else ()
        if sort == "metric" and not metric:
            raise ValueError("Enter a data.* characteristic under More filters to sort by it")
        return sort, descending, metric

    def _filtered(self, *_):
        # Filters keep Follow; only a nondefault sort pauses it.
        if self.form.logsSortCombo.currentIndex() != 0:
            self.form.logsFollowButton.setChecked(False)
        self.refresh()

    def _toggle_follow(self, enabled):
        if enabled:
            self.form.logsSortCombo.blockSignals(True)
            self.form.logsSortCombo.setCurrentIndex(0)
            self.form.logsSortCombo.blockSignals(False)
            self.refresh()

    def _scrolled(self, value):
        # Scrolling away from the newest rows is inspection: pause Follow so new rows do not pull the view.
        if value > 0 and not self._programmatic_scroll and self.form.logsFollowButton.isChecked():
            self.form.logsFollowButton.setChecked(False)

    def _header_sort(self, column):
        pair = HEADER_SORTS.get(column)
        if pair is None:
            return
        combo = self.form.logsSortCombo
        combo.setCurrentIndex(pair[1] if combo.currentIndex() == pair[0] else pair[0])

    def _show_sort_indicator(self):
        header = self.form.logsTable.horizontalHeader()
        current = self.form.logsSortCombo.currentIndex()
        for column, pair in HEADER_SORTS.items():
            if current in pair:
                descending = SORTS[current][2]
                order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
                header.setSortIndicator(column, order)
                return
        header.setSortIndicator(-1, Qt.SortOrder.DescendingOrder)

    def refresh(self, *_, facets=True):
        self.form.logsRefreshButton.setText("Refresh")
        self._show_sort_indicator()
        if not self.active:
            return
        sid = self._session()
        if not sid:
            self.model.replace([])
            self.form.logsStatus.setText("No diagnostic sessions")
            return
        try:
            filters, (sort, desc, metric) = self._filters(), self._sort()
        except ValueError as exc:
            self.form.logsNotice.setText(f"Invalid filter: {exc}")
            self.form.logsNotice.show()
            return
        self.form.logsStatus.setText("Loading session…")
        # Follow polls reuse the session-wide choices and merge newly shown values;
        # an explicit refresh or session change rescans them.
        cached = self._facets if self._facets[0] == sid and not facets else None

        def load():
            status, count, page, cursor = query(
                self.directory, sid, filters=filters, sort=sort, descending=desc, metric=metric
            )
            projections = []
            # Page-only bounded previews, never complete records in the model.
            for _tiny, ref in page:
                try:
                    full = read_detail(self.directory, ref)
                    pairs, more = characteristics(full, limit=6, preview_bytes=64)
                    projections.append((pairs, more))
                except Exception:
                    projections.append(((("Details", "unavailable"),), 0))
            choices = cached if cached is not None else (sid, *session_facets(self.directory, sid))
            return status, count, page, cursor, projections, choices

        self._run(load, self._show_results)

    def _show_results(self, result, error):
        if error:
            self.form.logsNotice.setText(f"Diagnostic reader unavailable: {error}")
            self.form.logsNotice.show()
            self.form.logsStatus.setText("Read failed")
            return
        status, count, page, _next, projections, choices = result
        self._page = page
        self._programmatic_scroll = True
        try:
            self.model.replace(page, projections)
            # Size only the bounded, resident page. Never scan the session for geometry.
            for row in range(len(page)):
                index = self.model.index(row, 3)
                option = QStyleOptionViewItem()
                option.font = self.form.logsTable.font()
                option.rect = self.form.logsTable.visualRect(index)
                option.rect.setWidth(self.form.logsTable.columnWidth(3))
                height = self.delegate.sizeHint(option, index).height()
                self.form.logsTable.setRowHeight(row, min(168, max(53, height)))
            if self.form.logsFollowButton.isChecked():
                self.form.logsTable.scrollToTop()
        finally:
            self._programmatic_scroll = False
        self.form.logsStatus.setText(f"{count} matching · {status.state} · displaying {len(page)}")
        self.form.logsNotice.setVisible(status.state not in ("complete", "pending"))
        self.form.logsNotice.setText("Incomplete diagnostic evidence: " + ", ".join(status.reasons))
        self._last = tuple((r.seq for _, r in page))
        self._pending = 0
        self._file_snapshot = tuple((p.path.name, p.device, p.inode, p.size) for p in status.segments)
        self._populate_filter_values(page, choices)

    def _populate_filter_values(self, page, choices):
        sid, components, events = choices
        # Rows shown by Follow may introduce names after the last session-wide scan.
        components = sorted(set(components) | {row["component"] for row, _ in page})[:FACET_LIMIT]
        events = sorted(set(events) | {row["event"] for row, _ in page})[:FACET_LIMIT]
        self._facets = (sid, components, events)
        for combo, options, initial in (
            (self.form.logsComponentCombo, components, "All components"),
            (self.form.logsEventCombo, events, "All events"),
        ):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(initial, "")
            for choice in options:
                combo.addItem(choice, choice)
            if combo is self.form.logsEventCombo:
                for family in sorted({name.split(".")[0] for name in options}):
                    combo.addItem(f"{family}.*", f"{family}.*")
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)

    def _poll(self):
        if not self.active:
            return
        sid = self._session()
        if not sid:
            if discover(self.directory):
                self.open()
            return
        try:
            changed = tuple((p.path.name, p.device, p.inode, p.size) for p in snapshot(self.directory, sid))
        except (OSError, ValueError):
            changed = ()
        if changed == self._file_snapshot:
            return
        if self.form.logsFollowButton.isChecked():
            self.refresh(facets=False)
        else:
            self._pending = max(1, self._pending)
            self.form.logsRefreshButton.setText("Refresh (new records)")

    def _row_clicked(self, index):
        # Selection alone never opens details; actions use the painted delegate.
        if index.column() != 4:
            self.form.logsFollowButton.setChecked(False)

    def _selected_action(self, action):
        index = self.form.logsTable.currentIndex()
        if index.isValid():
            self._row_action(index.row(), action)

    def _row_action(self, row, action):
        if row >= len(self.model.refs):
            return
        self.form.logsFollowButton.setChecked(False)
        ref = self.model.refs[row]
        if action == "details":
            self._run(lambda: read_detail(self.directory, ref), self._show_detail)
        elif action == "copy":
            self._run(lambda: read_detail(self.directory, ref), self._copy_record)

    def _show_detail(self, record, error):
        if error:
            self.form.logsDetailText.setPlainText("Details unavailable: " + error)
        else:
            self.form.logsDetailTitle.setText(
                f"{record['event']}  ·  {record['level']}  ·  {record['component']}  ·  "
                f"seq {record['seq']}  ·  {record['ts']}"
            )
            self.form.logsDetailText.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))
        self.form.logsDetail.show()

    def _copy_record(self, record, error):
        if error:
            self.form.logsStatus.setText("Raw record unavailable: " + error)
        else:
            QApplication.clipboard().setText(json.dumps(record, ensure_ascii=False, separators=(",", ":")))

    def copy_visible(self):
        """Copy complete displayed-page evidence, never the small table projections."""
        self._copy_refs(tuple(ref for _tiny, ref in self._page), "visible")

    def copy_selected(self):
        """Copy the complete records of the selected rows, in displayed order."""
        rows = sorted({index.row() for index in self.form.logsTable.selectionModel().selectedRows()})
        refs = tuple(self.model.refs[row] for row in rows if row < len(self.model.refs))
        if not refs:
            self.form.logsStatus.setText("Select rows to copy")
            return
        self.form.logsFollowButton.setChecked(False)
        self._copy_refs(refs, "selected")

    def _copy_refs(self, refs, label):
        if not refs:
            return

        def collect():
            return "\n".join(
                json.dumps(read_detail(self.directory, ref), ensure_ascii=False, separators=(",", ":")) for ref in refs
            )

        def delivered(value, error):
            if error:
                self.form.logsStatus.setText("Copy unavailable: " + error)
            else:
                QApplication.clipboard().setText(value)
                self.form.logsStatus.setText(f"Copied {len(refs)} {label} records")

        self._run(collect, delivered)

    def _save_menu(self):
        menu = QMenu(self.form.logsSaveMenu)
        copy_selected = menu.addAction("Copy selected rows")
        copy_selected.setEnabled(self.form.logsTable.selectionModel().hasSelection())
        menu.addSeparator()
        exports = {
            menu.addAction(label): fmt
            for label, fmt in (("Export matching CSV", "csv"), ("Export matching JSONL", "jsonl"))
        }
        menu.addSeparator()
        open_raw = menu.addAction("Open raw log file")
        open_raw.setEnabled(bool(self._session()))
        open_folder = menu.addAction("Open log folder")
        choice = menu.exec(self.form.logsSaveMenu.mapToGlobal(self.form.logsSaveMenu.rect().bottomLeft()))
        if choice is copy_selected:
            self.copy_selected()
        elif choice is open_raw:
            # Explicit user action: the active segment opens in the system's default viewer.
            self._open(self.directory / f"dtflow-{self._session()}.jsonl", directory=False)
        elif choice is open_folder:
            self._open(self.directory, directory=True)
        elif choice in exports:
            self._export(exports[choice])

    def _open(self, path: Path, *, directory: bool):
        if self.runner is None:
            self.form.logsStatus.setText("Opening files is unavailable here")
            return
        open_local_path(
            str(path),
            runner=self.runner,
            directory=directory,
            on_error=lambda reason: self.form.logsStatus.setText(f"Could not open {path.name}: {reason}"),
        )

    def _export(self, fmt):
        sid = self._session()
        try:
            filters, (sort, desc, metric) = self._filters(), self._sort()
        except ValueError as exc:
            self.form.logsNotice.setText(f"Invalid filter: {exc}")
            self.form.logsNotice.show()
            return
        target, _ = QFileDialog.getSaveFileName(
            self.form, f"Export matching {fmt.upper()}", f"diagnostic-excerpt.{fmt}", f"*.{fmt}"
        )
        if not target:
            return
        self._run(
            lambda: export_matching(
                self.directory, sid, Path(target), fmt=fmt, filters=filters, sort=sort, descending=desc, metric=metric
            ),
            lambda value, error: self.form.logsStatus.setText(
                "Export failed: " + error if error else f"Exported {value}"
            ),
        )

    def clear_filters(self):
        self.form.logsSearch.clear()
        self.form.logsField.clear()
        self.form.logsValue.clear()
        self.form.logsFrom.clear()
        self.form.logsTo.clear()
        for combo in (
            self.form.logsLevelCombo,
            self.form.logsComponentCombo,
            self.form.logsEventCombo,
            self.form.logsSortCombo,
        ):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.form.logsFollowButton.setChecked(True)
        self.refresh()

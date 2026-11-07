"""Native Logs workspace over the frozen Qt-free diagnostic reader.

Queries, detail hydration and exports execute outside the GUI thread.
The Qt model stores bounded projections, never complete JSONL payloads.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QFileDialog,
    QHeaderView,
    QMenu,
    QScrollBar,
    QSplitterHandle,
    QStyleOptionViewItem,
    QWidget,
)

from dtflow._diagnostic_export import export_matching
from dtflow._diagnostic_reader import (
    PAGE_SIZE,
    EvidenceError,
    discover,
    match,
    query,
    read_detail,
    scan,
    segment_age,
    snapshot,
)
from dtflow.diagnostic_projection import characteristics
from dtflow.logging_setup import LOGGER
from dtflow.ui.activity_strip import activity_strip
from dtflow.ui.desktop_open import open_local_path
from dtflow.ui.logs_delegate import LogCellDelegate, LogHeaderView
from dtflow.ui.theme import load_tokens

# Distinct filter choices kept per session; a pathological session cannot grow the combo lists without bound.
FACET_LIMIT = 1000

# Sort combo entries in the workspace specification's order: label, reader sort key, descending.
SORTS = (
    ("Datetime · newest first", "ts", True),
    ("Datetime · oldest first", "ts", False),
    ("Severity · most severe first", "level", True),
    ("Severity · least severe first", "level", False),
    ("Event · A → Z", "event", False),
    ("Event · Z → A", "event", True),
    ("Component · A → Z", "component", False),
    ("Component · Z → A", "component", True),
    ("Selected metric · highest first", "metric", True),
    ("Selected metric · lowest first", "metric", False),
)
METRIC_SORTS = (8, 9)

# Clicking a sortable header target toggles between its two sort entries; the severity strip is not sortable.
HEADER_SORTS = {"ts": (0, 1), "component": (6, 7), "event": (4, 5)}
# Header sections and their sort targets; the event column header holds two independent targets.
SECTION_SORTS = {1: ("ts",), 2: ("component", "event")}

# Operator labels shown to people, with the shared query operator as item data.
OPERATORS = (("=", "="), ("≠", "!="), (">", ">"), ("≥", ">="), ("<", "<"), ("≤", "<="), ("contains", "contains"))

# Text after an icon starts with an en space; the native tool-button gap reads as cramped.
ICON_GAP = "\u2002"
# Refresh spinner frame interval, and its minimum visible time so quick Follow reads do not flicker.
SPIN_INTERVAL_MS = 50
SPIN_MINIMUM = 0.6

EMPTY_SESSION = "No records yet in this session."
NO_MATCHES = "No matching records. Adjust filters or reset."

# Minimum seconds between pending-arrival counts while Follow is off and the session keeps growing.
PENDING_COUNT_INTERVAL = 3.0

INCOMPLETE_NOTICE = (
    "Incomplete session evidence: available records remain inspectable, "
    "but benchmark completeness cannot be certified ({reasons})."
)


def session_label(number: int, session_id: str, last_write: datetime | None, *, current: bool) -> str:
    """Selector text: newest-first position, local last-write time and a short session id.

    Kept short so the closed combo fits the filter row; the item tooltip carries the full id.
    """
    parts = [f"#{number}"]
    if last_write is not None:
        parts.append(last_write.strftime("%d %b %H:%M"))
    if current:
        parts.append("this session")
    parts.append(session_id[:8])
    return " · ".join(parts)


def session_facets(directory: Path, session_id: str, *, cancelled=None):
    """Session-wide components, events, data keys and numeric data keys through the shared reader, bounded per list."""
    components, events, keys, numeric = set(), set(), set(), set()

    def collect(record, _ref):
        if len(components) < FACET_LIMIT:
            components.add(record["component"])
        if len(events) < FACET_LIMIT:
            events.add(record["event"])
        data = record.get("data")
        if type(data) is dict:
            for key, value in data.items():
                if len(keys) < FACET_LIMIT:
                    keys.add(key)
                if type(value) in (int, float) and len(numeric) < FACET_LIMIT:
                    numeric.add(key)

    def reset():
        for collected in (components, events, keys, numeric):
            collected.clear()

    scan(directory, session_id, on_record=collect, on_reset=reset, cancelled=cancelled)
    return sorted(components), sorted(events), sorted(keys), sorted(numeric)


def row_preview(record: dict):
    """Table projection: up to six characteristics, or the sanitized message when a record has no data."""
    pairs, more = characteristics(record, limit=6, preview_bytes=64)
    message = record.get("msg")
    if not pairs and type(message) is str and message:
        encoded = message.encode("utf-8")
        return pairs, more, encoded[:160].decode("utf-8", "ignore") + ("…" if len(encoded) > 160 else "")
    return pairs, more


def characteristic_label(key: str) -> str:
    return key.replace("_", " ")


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
        try:
            self.signals.ready.emit(self.token, value, error)
        except RuntimeError:
            # The owning form may have been destroyed while the worker finished.
            pass


def count_arrivals(directory: Path, session_id: str, boundary, filters, *, cancelled=None):
    """Matching records not wholly inside the shown snapshot, through the shared reader.

    `boundary` maps each (device, inode) of the shown snapshot to its (size, rotation age).
    Rotation renames keep the inode and only raise the age, so a record is new when it lies
    beyond the shown size, sits in an unknown file, or sits in a file whose age is lower than
    recorded (a new file reusing a deleted segment's inode). Only a count is retained, never
    the arriving records.
    """
    total = 0

    def collect(record, ref):
        nonlocal total
        shown = boundary.get((ref.device, ref.inode))
        new = shown is None or segment_age(ref.basename) < shown[1] or ref.offset + ref.length > shown[0]
        if new and match(record, **filters):
            total += 1

    def reset():
        nonlocal total
        total = 0

    status = scan(directory, session_id, on_record=collect, on_reset=reset, cancelled=cancelled)
    return total, status


def refresh_label(count, *, incomplete=False):
    """Refresh text for held-back matching arrivals; None means a change whose count is unknown."""
    if count is None:
        return "Refresh (new)"
    if not count:
        return "Refresh (0 new · incomplete)" if incomplete else "Refresh"
    return f"Refresh ({count} new{' · incomplete' if incomplete else ''})"


# Plain-language export failures for reader reasons a person can act on.
EXPORT_FAILURES = {
    "snapshot_changed": "the session files changed while reading; try again",
    "session_rotated": "the oldest log file was rotated away during the export; try again",
    "consumer_record_limit": "too many matching records; narrow the filters",
    "consumer_memory_limit": "matching records exceed the export memory budget; narrow the filters",
    "session_unavailable": "the session files are no longer available",
}


def export_outcome(manifest) -> tuple[str, str]:
    """Overlay state and one-line summary of a published export manifest."""
    artifact, selection = manifest["artifact"], manifest["selection"]
    evidence = manifest["source_evidence"]
    text = f"{artifact['basename']} · {selection['exported_records']} records · source {evidence['state']}"
    if evidence["state"] in ("complete", "pending"):
        return "success", text
    state, text = "warning", text + " (" + ", ".join(evidence["reasons"]) + ")"
    return state, text


class _ExportSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(object, object)


class _ExportJob(QRunnable):
    def __init__(self, func, signals):
        super().__init__()
        self.func, self.signals = func, signals

    def run(self):
        try:
            value, error = self.func(), None
        except Exception as exc:
            value, error = None, exc
        try:
            self.signals.finished.emit(value, error)
        except RuntimeError:
            pass  # the window closed while the export finished


class _LogModel(QAbstractTableModel):
    HEADERS = ("", "DATE / TIME", "COMPONENT . EVENT", "CHARACTERISTICS", "")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.refs = []
        self.palette = load_tokens()
        self.previews = []
        # (sort target, descending, characteristic label) of the active sort; drives the header arrows.
        self.sort_state = ("ts", True, "")

    def set_sort_state(self, target, descending, metric=""):
        if self.sort_state != (target, descending, metric):
            self.sort_state = (target, descending, metric)
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 1, 3)

    def active_header_part(self):
        """Which part of the split COMPONENT . EVENT header is sorted, or None."""
        targets = SECTION_SORTS[2]
        return targets.index(self.sort_state[0]) if self.sort_state[0] in targets else None

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            text = self.HEADERS[section]
            if section not in (1, 2, 3):
                return text
            target, descending, metric = self.sort_state
            if section == 3 and target == "metric" and metric:
                text += f" · {metric}"
            targets = SECTION_SORTS.get(section, ("metric",))
            labels = text.split(LogHeaderView.SPLIT_SEPARATOR) if len(targets) > 1 else [text]
            # Inactive sort targets show ↕; the active one shows its direction.
            return LogHeaderView.SPLIT_SEPARATOR.join(
                f"{label} {('↓' if descending else '↑') if name == target else '↕'}"
                for label, name in zip(labels, targets, strict=True)
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return {
                0: "Recorded severity",
                1: "Sort by source time",
                2: "Sort by component (left) or by event name (right)",
                3: "Sort by a numeric characteristic",
                4: "Row actions: Details and Copy raw",
            }[section]
        return None

    def replace(self, page, previews=None):
        self.beginResetModel()
        self.rows = [record for record, _ref in page][:PAGE_SIZE]
        self.refs = [ref for _record, ref in page][:PAGE_SIZE]
        self.previews = list(previews or ())[:PAGE_SIZE]
        self.endResetModel()

    def reconcile(self, page, previews):
        """Apply a bounded page with batched removals/inserts and stable Qt indexes."""
        page = page[:PAGE_SIZE]
        wanted = [(ref.session_id, ref.seq) for _, ref in page]
        keep = set(wanted)
        # Remove obsolete ranges backwards so each notification uses current indexes.
        end = len(self.refs) - 1
        while end >= 0:
            if (self.refs[end].session_id, self.refs[end].seq) in keep:
                end -= 1
                continue
            start = end
            while start > 0 and (self.refs[start - 1].session_id, self.refs[start - 1].seq) not in keep:
                start -= 1
            self.beginRemoveRows(QModelIndex(), start, end)
            del self.rows[start : end + 1]
            del self.refs[start : end + 1]
            del self.previews[start : end + 1]
            self.endRemoveRows()
            end = start - 1
        changed = []
        row = 0
        while row < len(page):
            if row < len(self.refs) and (self.refs[row].session_id, self.refs[row].seq) == wanted[row]:
                if self.rows[row] != page[row][0] or self.previews[row] != previews[row]:
                    changed.append(row)
                self.rows[row], self.refs[row], self.previews[row] = page[row][0], page[row][1], previews[row]
                row += 1
                continue
            start = row
            next_key = (self.refs[row].session_id, self.refs[row].seq) if row < len(self.refs) else None
            while row < len(page) and wanted[row] != next_key:
                row += 1
            self.beginInsertRows(QModelIndex(), start, row - 1)
            self.rows[start:start] = [tiny for tiny, _ in page[start:row]]
            self.refs[start:start] = [ref for _, ref in page[start:row]]
            self.previews[start:start] = previews[start:row]
            self.endInsertRows()
            changed.extend(range(start, row))
        if changed:
            self.dataChanged.emit(self.index(min(changed), 0), self.index(max(changed), 4))
        return changed

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
        # Painted split header: COMPONENT and EVENT are two sort targets in one section, not two columns.
        self.form.logsTable.setHorizontalHeader(LogHeaderView(2, self.model.active_header_part, self.form.logsTable))
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
        # Header text carries ↕/↑/↓ as in the workspace specification; no second native indicator.
        header.setSortIndicatorShown(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.sectionClicked.connect(self._header_sort)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for col, width in enumerate((12, 122, 250, 220, 80)):
            if col != 3:
                self.form.logsTable.setColumnWidth(col, width)
        self.form.logsTable.setWordWrap(True)
        self.form.logsTable.verticalHeader().setDefaultSectionSize(56)
        self.form.logsTable.setSelectionBehavior(self.form.logsTable.SelectionBehavior.SelectRows)
        self.form.logsTable.setEditTriggers(self.form.logsTable.EditTrigger.NoEditTriggers)
        self.form.logsDetail.hide()
        self.form.logsAdvanced.hide()
        self.form.logsNotice.hide()
        self.form.logsLevelCombo.addItem("ALL", "")
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.form.logsLevelCombo.addItem(f">={level}", level)
        self.form.logsLevelCombo.setCurrentIndex(self.form.logsLevelCombo.findData("WARNING"))
        self.form.logsComponentCombo.addItem("All", "")
        self.form.logsEventCombo.addItem("All events", "")
        for label, operator in OPERATORS:
            self.form.logsOperator.addItem(label, operator)
        self._key_completer = QCompleter([], self.form.logsField)
        self._key_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._key_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.form.logsField.setCompleter(self._key_completer)
        for label, _sort, _descending in SORTS:
            self.form.logsSortCombo.addItem(label)
        self.form.logsFollowButton.setCheckable(True)
        self.form.logsFollowButton.setChecked(True)
        self.form.logsCloseButton.clicked.connect(on_close)
        self.form.logsRefreshButton.clicked.connect(lambda: self.refresh(facets=True))
        self.form.logsPreviousButton.clicked.connect(lambda: self._navigate_page(before=True))
        self.form.logsNextButton.clicked.connect(lambda: self._navigate_page(before=False))
        self.form.logsMoreFiltersButton.clicked.connect(
            lambda: self._show_advanced(not self.form.logsAdvanced.isVisible())
        )
        self.form.logsFollowButton.toggled.connect(self._toggle_follow)
        icons = Path(__file__).parents[1] / "resources" / "images" / "gallery"
        for button, icon in (
            (self.form.logsRefreshButton, "refresh.svg"),
            (self.form.logsFollowButton, "arrow-up-to-line.svg"),
        ):
            button.setIcon(QIcon(str(icons / icon)))
            button.setIconSize(QSize(16, 16))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setText(ICON_GAP + button.text().strip())
        # The Refresh icon turns while a page read is running (Follow refreshes included).
        self._refresh_icon = QIcon(str(icons / "refresh.svg"))
        self._spin_angle = 0
        self._spin_started = 0.0
        self._spin_timer = QTimer(form)
        self._spin_timer.setInterval(SPIN_INTERVAL_MS)
        self._spin_timer.timeout.connect(self._spin)
        # Session labels grow with their id and time; keep the filter row stable and show the full label on hover.
        session = self.form.logsSessionCombo
        session.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        session.setMinimumContentsLength(28)
        session.setMaximumWidth(320)
        session.currentIndexChanged.connect(lambda _index: self._session_tooltip())
        for combo in (self.form.logsComponentCombo, self.form.logsEventCombo):
            combo.currentIndexChanged.connect(
                lambda _index, combo=combo: combo.setToolTip(combo.currentData() or combo.currentText())
            )
        # The pane collapses downward into the table split.
        chevron = Path(__file__).parents[1] / "resources" / "images" / "controls" / "chevron-down.svg"
        self.form.logsDetailCollapse.setIcon(QIcon(str(chevron)))
        self.form.logsDetailCollapse.setText("")
        self.form.logsDetailCollapse.setToolTip("Collapse details")
        self.form.logsDetailCollapse.setAccessibleName("Collapse details")
        self.form.logsDetailCollapse.clicked.connect(self.form.logsDetail.hide)
        self.form.logsDetailCopyRaw.clicked.connect(self._copy_detail)
        self._detail_record = None
        self.form.logsResetFilters.clicked.connect(self.clear_filters)
        self.form.logsCopyVisible.clicked.connect(self.copy_visible)
        self.form.logsSaveMenu.clicked.connect(self._save_menu)
        self.form.logsTable.clicked.connect(self._row_clicked)
        self.form.logsSessionCombo.currentIndexChanged.connect(self._session_changed)
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
        self.form.logsTable.selectionModel().selectionChanged.connect(self._selection_changed)
        self.token = 0
        self._tokens = {}
        self._cancellations = {}
        self._query_busy = False
        self._query_key = None
        self._first_cursor = None
        self._next_cursor = None
        self._page_cursor = None
        self._page_before = False
        self._page_number = 0
        self.active = False
        self._jobs = []
        self._last = ()
        self._page = []
        self._pending = 0
        self._file_snapshot = None
        # Snapshot last counted for the Refresh label, and when; counts never retain arriving records.
        self._counted_snapshot = None
        self._counted_at = 0.0
        self._outside_watch = False
        self._outside_armed = False
        self._outside_widgets = []
        self._facets = (None, [], [], [], [])
        self._programmatic = False
        self._timer = QTimer(form)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)
        self.form.destroyed.connect(self.stop)
        # One export at a time; it outlives navigation and reports through the shared overlay.
        self._export_cancel = None
        self.form.destroyed.connect(self._cancel_export)
        # Outside-click dismissal is watched only while the details pane is shown.
        self.form.logsDetail.installEventFilter(self)

    def _run(self, func, callback, *, kind, cancel=None):
        old = self._cancellations.get(kind)
        if old is not None:
            old.set()
        self.token += 1
        token = self.token
        self._tokens[kind] = token
        if cancel is not None:
            self._cancellations[kind] = cancel
        signals = _Signals(self.form)

        def arrived(received, value, error):
            try:
                if received == self._tokens.get(kind) and self.active:
                    callback(value, error)
            finally:
                if received == self._tokens.get(kind):
                    self._tokens.pop(kind, None)
                    self._cancellations.pop(kind, None)
                if signals in self._jobs:
                    self._jobs.remove(signals)
                signals.deleteLater()

        signals.ready.connect(arrived, Qt.ConnectionType.QueuedConnection)
        self._jobs.append(signals)
        self.pool.start(_Job(token, func, signals))

    def _cancel(self, kind):
        old = self._cancellations.pop(kind, None)
        if old is not None:
            old.set()
        self._tokens.pop(kind, None)

    def _session_tooltip(self):
        combo = self.form.logsSessionCombo
        sid = combo.currentData() or ""
        combo.setToolTip(f"{combo.currentText()} (last write, local time)\nSession {sid}" if sid else "")

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
        self._session_tooltip()
        # The popup may be wider than the closed combo, so complete labels stay readable there.
        combo.view().setMinimumWidth(combo.view().sizeHintForColumn(0) + 24)
        combo.blockSignals(False)
        self.refresh(facets=True)
        self._timer.start()

    def stop(self):
        self.active = False
        self._watch_outside(False)
        self._tokens.clear()
        for cancel in self._cancellations.values():
            cancel.set()
        self._cancellations.clear()
        self._query_busy = False
        self._timer.stop()

    def escape(self):
        """Escape closes the innermost Logs surface first; shared overlays keep precedence."""
        if self.escape_owner is not None and self.escape_owner():
            return
        if self.form.logsDetail.isVisible():
            self.form.logsDetail.hide()
        elif self.form.logsAdvanced.isVisible():
            self._show_advanced(False)
        else:
            self.on_close()

    def _show_advanced(self, visible):
        self.form.logsAdvanced.setVisible(visible)
        self.form.logsMoreFiltersButton.setText("More filters ▴" if visible else "More filters ▾")

    def _query_arguments(self):
        try:
            filters = self._filters()
        except ValueError as exc:
            raise ValueError(f"Invalid filter: {exc}") from exc
        try:
            return filters, self._sort()
        except ValueError as exc:
            raise ValueError(f"Invalid sort: {exc}") from exc

    def _filters(self):
        filters = {
            "search": self.form.logsSearch.text().strip(),
            "min_level": self.form.logsLevelCombo.currentData() or "",
            "component": self.form.logsComponentCombo.currentData() or "",
        }
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
            filters["op"] = self.form.logsOperator.currentData() or "="
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
            raise ValueError("Choose a characteristic to sort by")
        return sort, descending, metric

    def _session_changed(self, *_):
        # Component/event names belong to the previous session; never carry them over as a hidden filter.
        for combo in (self.form.logsComponentCombo, self.form.logsEventCombo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.refresh(facets=True)

    def _filtered(self, *_):
        # Filters keep Follow; only a nondefault sort pauses it.
        if self.form.logsSortCombo.currentIndex() != 0:
            self.form.logsFollowButton.setChecked(False)
        self.refresh()

    def _toggle_follow(self, enabled):
        self.form.logsFollowButton.setText(ICON_GAP + ("Follow: On" if enabled else "Follow: Off"))
        if enabled:
            self.form.logsSortCombo.blockSignals(True)
            self.form.logsSortCombo.setCurrentIndex(0)
            self.form.logsSortCombo.blockSignals(False)
            self.refresh()

    def _scrolled(self, value):
        # Scrolling away from the newest rows is inspection: pause Follow so new rows do not pull the view.
        if value > 0 and not self._programmatic and self.form.logsFollowButton.isChecked():
            self.form.logsFollowButton.setChecked(False)

    def _selection_changed(self, *_):
        # Keyboard selection is inspection too; a Follow refresh would otherwise reset it.
        if not self._programmatic and self.form.logsTable.selectionModel().hasSelection():
            self.form.logsFollowButton.setChecked(False)

    def _header_sort(self, column):
        if column == 3:
            self._characteristic_menu()
            return
        targets = SECTION_SORTS.get(column)
        if targets is None:
            return
        header = self.form.logsTable.horizontalHeader()
        part = getattr(header, "clicked_part", 0) if len(targets) > 1 else 0
        pair = HEADER_SORTS[targets[part]]
        combo = self.form.logsSortCombo
        combo.setCurrentIndex(pair[1] if combo.currentIndex() == pair[0] else pair[0])

    def _characteristic_menu(self):
        """CHARACTERISTICS opens the numeric keys present in the session; picking the active one reverses it."""
        numeric = self._facets[4] if self._facets[0] == self._session() else []
        if not numeric:
            self.form.logsStatus.setText("No numeric characteristics in this session")
            return
        header = self.form.logsTable.horizontalHeader()
        menu = QMenu(header)
        actions = {menu.addAction(characteristic_label(key)): key for key in numeric}
        point = header.mapToGlobal(header.rect().bottomLeft())
        point.setX(point.x() + header.sectionViewportPosition(3))
        choice = menu.exec(point)
        if choice not in actions:
            return
        field = "data." + actions[choice]
        combo = self.form.logsSortCombo
        same = self.form.logsField.text().strip() == field
        target = METRIC_SORTS[1] if same and combo.currentIndex() == METRIC_SORTS[0] else METRIC_SORTS[0]
        self.form.logsField.setText(field)
        if combo.currentIndex() == target:
            self._filtered()
        else:
            combo.setCurrentIndex(target)

    def _show_sort_indicator(self):
        current = max(0, self.form.logsSortCombo.currentIndex())
        label, sort, descending = SORTS[current]
        self.form.logsSortNote.setText(label)
        if sort == "metric":
            key = self.form.logsField.text().strip().removeprefix("data.")
            self.model.set_sort_state("metric", descending, characteristic_label(key))
            self.form.logsSortNote.setText(f"{characteristic_label(key)} · {label.split(' · ')[1]}")
            return
        for target, pair in HEADER_SORTS.items():
            if current in pair:
                self.model.set_sort_state(target, descending)
                return
        self.model.set_sort_state("", descending)

    def refresh(self, *_, facets=False):
        self._request_page(facets=facets)

    def _navigate_page(self, *, before):
        if self._query_busy:
            return
        cursor = self._first_cursor if before else self._next_cursor
        if cursor is None or (before and self._page_number == 0):
            return
        self.form.logsFollowButton.setChecked(False)
        self._request_page(facets=False, navigation=(cursor, before, self._page_number + (-1 if before else 1)))

    def _page_controls(self):
        self.form.logsPreviousButton.setEnabled(not self._query_busy and self._page_number > 0)
        self.form.logsNextButton.setEnabled(not self._query_busy and self._next_cursor is not None)

    def _start_spin(self):
        self._spin_started = time.monotonic()
        if not self._spin_timer.isActive():
            self._spin_timer.start()

    def _spin(self):
        """Rotate the Refresh icon while a page read runs; restore the still icon afterwards."""
        button = self.form.logsRefreshButton
        if not self.active or (not self._query_busy and time.monotonic() - self._spin_started >= SPIN_MINIMUM):
            self._spin_timer.stop()
            self._spin_angle = 0
            button.setIcon(self._refresh_icon)
            return
        self._spin_angle = (self._spin_angle + 30) % 360
        size = button.iconSize()
        ratio = button.devicePixelRatioF()
        source = self._refresh_icon.pixmap(size, ratio)
        frame = QPixmap(source.size())
        frame.setDevicePixelRatio(ratio)
        frame.fill(Qt.GlobalColor.transparent)
        painter = QPainter(frame)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(size.width() / 2, size.height() / 2)
        painter.rotate(self._spin_angle)
        painter.translate(-size.width() / 2, -size.height() / 2)
        painter.drawPixmap(0, 0, source)
        painter.end()
        button.setIcon(QIcon(frame))

    def _set_pending(self, pending, count=None, *, incomplete=False, detail=""):
        """Refresh label and accent border for held-back matching records (count None: unknown)."""
        button = self.form.logsRefreshButton
        pending = bool(pending and (count is None or count > 0 or incomplete))
        button.setText(ICON_GAP + (refresh_label(count, incomplete=incomplete) if pending else "Refresh"))
        button.setToolTip(detail or "Reload the selected session")
        if button.property("pending") != pending:
            button.setProperty("pending", pending)
            button.style().unpolish(button)
            button.style().polish(button)

    def _request_page(self, *, facets=False, navigation=None):
        self._set_pending(False)
        self._cancel("pending")
        if not self.active:
            return
        sid = self._session()
        if not sid:
            old = self._cancellations.get("query")
            if old is not None:
                old.set()
            self._tokens.pop("query", None)
            self._query_busy = False
            self._query_key = None
            self._page_number = 0
            self._first_cursor = self._next_cursor = None
            self.model.replace([])
            self._page = []
            self._page_controls()
            self._set_empty("No diagnostic sessions yet.")
            self.form.logsCounter.setText("No diagnostic sessions")
            return
        try:
            filters, (sort, desc, metric) = self._query_arguments()
        except ValueError as exc:
            old = self._cancellations.get("query")
            if old is not None:
                old.set()
            self._tokens.pop("query", None)
            self._query_busy = False
            self.form.logsPreviousButton.setEnabled(False)
            self.form.logsNextButton.setEnabled(False)
            self.form.logsNotice.setText(str(exc))
            self.form.logsNotice.show()
            return
        key = (sid, filters, sort, desc, metric)
        same_query = key == self._query_key
        if not same_query or self.form.logsFollowButton.isChecked():
            cursor, before, number = None, False, 0
        elif navigation is not None:
            cursor, before, number = navigation
        else:
            cursor, before, number = self._page_cursor, self._page_before, self._page_number
        if self._query_key is not None and sid != self._query_key[0]:
            for kind in ("detail", "copy"):
                self._tokens.pop(kind, None)
            self.form.logsDetail.hide()
        self._show_sort_indicator()
        self._query_busy = True
        self._start_spin()
        self._page_controls()
        self.form.logsCounter.setText("Loading session…")
        # Filter, sort and Follow refreshes reuse the session-wide choices and merge newly shown
        # values; only an explicit Refresh, session change or reopen rescans them.
        cached = self._facets if self._facets[0] == sid and not facets else None
        cancel = Event()
        fallback = self._facets if self._facets[0] == sid else (sid, [], [], [], [])

        def load():
            status, count, page, cursor = query(
                self.directory,
                sid,
                filters=filters,
                sort=sort,
                descending=desc,
                metric=metric,
                cursor=request_cursor,
                before=before,
                cancelled=cancel.is_set,
                project=row_preview,
            )
            choices = cached
            if choices is None:
                try:
                    choices = (sid, *session_facets(self.directory, sid, cancelled=cancel.is_set))
                except Exception:
                    # A failed choice rescan never discards an otherwise readable page.
                    choices = fallback
            return status, count, page, cursor, [tiny["preview"] for tiny, _ in page], choices

        request_cursor = cursor

        def delivered(result, error):
            self._query_busy = False
            if not error:
                self._query_key = key
                self._page_cursor, self._page_before, self._page_number = request_cursor, before, number
            self._show_results(result, error, incremental=same_query, navigating=navigation is not None)
            self._page_controls()

        self._run(load, delivered, kind="query", cancel=cancel)

    def _show_results(self, result, error, *, incremental=False, navigating=False):
        if error:
            self.form.logsNotice.setText(f"Diagnostic reader unavailable: {error}")
            self.form.logsNotice.show()
            self.form.logsCounter.setText("Read failed")
            # Keep readable earlier rows; never leave an earlier empty-state message describing this failure.
            self._set_empty("" if self.model.rows else "Records could not be read.")
            return
        status, count, page, _next, projections, choices = result
        self._page = page
        self._first_cursor = page[0][0]["cursor"] if page else None
        # A preceding page always has a forward neighbor (the page just left).
        self._next_cursor = page[-1][0]["cursor"] if page and self._page_before else _next
        table = self.form.logsTable
        selected = {
            (self.model.refs[index.row()].session_id, self.model.refs[index.row()].seq)
            for index in table.selectionModel().selectedRows()
        }
        current = table.currentIndex()
        current_key = (
            (self.model.refs[current.row()].session_id, self.model.refs[current.row()].seq)
            if current.isValid()
            else None
        )
        top = table.rowAt(0)
        anchor = self.model.refs[top] if top >= 0 else None
        self._programmatic = True
        try:
            if incremental:
                changed = self.model.reconcile(page, projections)
            else:
                self.model.replace(page, projections)
                changed = range(len(page))
            # Size only the bounded, resident page. Never scan the session for geometry.
            for row in changed:
                index = self.model.index(row, 3)
                option = QStyleOptionViewItem()
                option.font = self.form.logsTable.font()
                option.rect = self.form.logsTable.visualRect(index)
                option.rect.setWidth(self.form.logsTable.columnWidth(3))
                height = self.delegate.sizeHint(option, index).height()
                self.form.logsTable.setRowHeight(row, min(168, max(53, height)))
            selection = table.selectionModel()
            for row, ref in enumerate(self.model.refs):
                identity = (ref.session_id, ref.seq)
                if identity in selected:
                    selection.select(
                        self.model.index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
                if identity == current_key:
                    selection.setCurrentIndex(self.model.index(row, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
                if not navigating and anchor is not None and identity == (anchor.session_id, anchor.seq):
                    table.scrollTo(self.model.index(row, 0), table.ScrollHint.PositionAtTop)
            if navigating or self.form.logsFollowButton.isChecked():
                self.form.logsTable.scrollToTop()
        finally:
            self._programmatic = False
        if status.records == 0:
            self._set_empty(EMPTY_SESSION)
            self.form.logsCounter.setText(f"0 records · {status.state}")
        elif count == 0:
            self._set_empty(NO_MATCHES)
            self.form.logsCounter.setText(f"0 of {status.records} matching records · {status.state}")
        else:
            self._set_empty("")
            self.form.logsCounter.setText(
                f"{count} of {status.records} matching records · {status.state} · "
                f"page {self._page_number + 1} · showing {len(page)}"
            )
        self.form.logsNotice.setVisible(status.state not in ("complete", "pending"))
        self.form.logsNotice.setText(INCOMPLETE_NOTICE.format(reasons=", ".join(status.reasons)))
        self._last = tuple((r.seq for _, r in page))
        self._pending = 0
        self._file_snapshot = tuple((p.path.name, p.device, p.inode, p.size) for p in status.segments)
        self._counted_snapshot = self._file_snapshot
        self._populate_filter_values(page, choices)

    def _set_empty(self, message):
        """Replace the table with the empty-state panel while `message` is set."""
        self.form.logsEmpty.setText(message)
        self.form.logsTableStack.setCurrentWidget(self.form.logsEmpty if message else self.form.logsTable)

    def _populate_filter_values(self, page, choices):
        sid, components, events, keys, numeric = choices
        # Rows shown by Follow may introduce names after the last session-wide scan.
        components = sorted(set(components) | {row["component"] for row, _ in page})[:FACET_LIMIT]
        events = sorted(set(events) | {row["event"] for row, _ in page})[:FACET_LIMIT]
        self._facets = (sid, components, events, keys, numeric)
        self._key_completer.model().setStringList(["data." + key for key in keys])
        for combo, options, initial in (
            (self.form.logsComponentCombo, components, "All"),
            (self.form.logsEventCombo, events, "All events"),
        ):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(initial, "")
            if combo is self.form.logsEventCombo:
                # One dropdown: event families first, then exact events, each under a disabled group heading.
                families = sorted({name.split(".")[0] for name in options})
                for heading, entries in (
                    ("Event families", [(f"{family}.* (all)", f"{family}.*") for family in families]),
                    ("Exact events", [(name, name) for name in options]),
                ):
                    if not entries:
                        continue
                    combo.addItem(heading, None)
                    combo.model().item(combo.count() - 1).setEnabled(False)
                    for text, value in entries:
                        label = text.removeprefix("dtflow.") if heading == "Exact events" else text
                        combo.addItem("    " + label, value)
            else:
                for choice in options:
                    combo.addItem(choice.removeprefix("dtflow."), choice)
            if current and combo.findData(current) < 0:
                # Keep an active filter visible even when its name is not (or no longer) listed.
                combo.addItem(f"{current.removeprefix('dtflow.')} (not listed)", current)
            for index in range(combo.count()):
                combo.setItemData(index, combo.itemData(index) or combo.itemText(index), Qt.ItemDataRole.ToolTipRole)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.setToolTip(combo.currentData() or combo.currentText())
            self._fit_filter_popup(combo)
            combo.blockSignals(False)

    @staticmethod
    def _fit_filter_popup(combo):
        """Expand only the popup, not the filter row; keep exceptionally long labels inspectable."""
        view = combo.view()
        view.setTextElideMode(Qt.TextElideMode.ElideNone)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        padding = view.verticalScrollBar().sizeHint().width() + 2 * view.frameWidth() + 16
        width = max(combo.width(), view.sizeHintForColumn(0) + padding)
        screen = combo.screen()
        if screen is not None:
            width = min(width, max(1, screen.availableGeometry().width() - 16))
        view.setMinimumWidth(width)

    def _poll(self):
        if not self.active or self._query_busy:
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
            self._count_pending(changed)

    def _count_pending(self, changed):
        """Count matching arrivals since the shown snapshot in the background, one count at a time."""
        if (
            "pending" in self._tokens
            or changed == self._counted_snapshot
            or time.monotonic() - self._counted_at < PENDING_COUNT_INTERVAL
        ):
            return
        if self._query_key is None or self._file_snapshot is None:
            self._set_pending(True, None, detail="The session changed since this page was read")
            return
        sid, filters = self._query_key[0], self._query_key[1]
        boundary = {(device, inode): (size, segment_age(name)) for name, device, inode, size in self._file_snapshot}
        cancel = Event()
        self._counted_at = time.monotonic()

        def delivered(result, error):
            self._counted_at = time.monotonic()
            if error:
                # The change is real even when the count is not available.
                self._counted_snapshot = None
                self._set_pending(True, None, detail=f"Matching new records could not be counted: {error}")
                return
            count, status = result
            self._counted_snapshot = tuple((p.path.name, p.device, p.inode, p.size) for p in status.segments)
            self._pending = count
            incomplete = status.state not in ("complete", "pending")
            detail = f"{count} new records match the current filters"
            if incomplete:
                detail += "; session evidence is incomplete (" + ", ".join(status.reasons) + "), so it may be inexact"
            self._set_pending(True, count, incomplete=incomplete, detail=detail)

        self._run(
            lambda: count_arrivals(self.directory, sid, boundary, filters, cancelled=cancel.is_set),
            delivered,
            kind="pending",
            cancel=cancel,
        )

    def _watch_outside(self, enabled):
        """Filter only the Logs widgets outside the details pane, and only while it is shown."""
        if enabled == self._outside_watch:
            return
        self._outside_watch = enabled
        self._outside_armed = False
        if enabled:
            detail = self.form.logsDetail
            self._outside_widgets = [
                widget
                for widget in (self.form, *self.form.findChildren(QWidget))
                if widget is not detail and not detail.isAncestorOf(widget)
            ]
            for widget in self._outside_widgets:
                widget.installEventFilter(self)
        else:
            for widget in self._outside_widgets:
                try:
                    widget.removeEventFilter(self)
                except RuntimeError:
                    pass  # already destroyed with the form
            self._outside_widgets = []

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.form.logsDetail:
            if kind in (QEvent.Type.Show, QEvent.Type.Hide):
                self._watch_outside(kind == QEvent.Type.Show and self.active)
            return False
        if not self._outside_watch:
            return False
        if kind == QEvent.Type.MouseButtonPress:
            self._outside_armed = self._outside_armed or self._outside_press(watched, event)
        elif kind == QEvent.Type.MouseButtonRelease and self._outside_armed:
            # Collapse after the release is handled, so the table does not grow under the pressed row
            # before its click completes.
            self._outside_armed = False
            QTimer.singleShot(0, self.form.logsDetail, self.form.logsDetail.hide)
        # Never consume the event: selection, copy and row gestures continue normally.
        return False

    def _outside_press(self, watched, event):
        """A primary press inside Logs but outside details that is not a splitter, scroll or row-action gesture."""
        if event.button() != Qt.MouseButton.LeftButton or not isinstance(watched, QWidget):
            return False
        if not self.form.logsDetail.isVisible() or not (watched is self.form or self.form.isAncestorOf(watched)):
            return False
        if isinstance(watched, (QSplitterHandle, QScrollBar)):
            return False
        point = event.globalPosition().toPoint()
        detail = self.form.logsDetail
        if detail.rect().contains(detail.mapFromGlobal(point)):
            return False
        viewport = self.form.logsTable.viewport()
        if watched is viewport and self.form.logsTable.indexAt(viewport.mapFromGlobal(point)).column() == 4:
            # Details / Copy raw on another row switch or copy without a collapse in between.
            return False
        return True

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
            self._run(lambda: read_detail(self.directory, ref), self._show_detail, kind="detail")
        elif action == "copy":
            self._run(lambda: read_detail(self.directory, ref), self._copy_record, kind="copy")

    def _show_detail(self, record, error):
        self._detail_record = None if error else record
        self.form.logsDetailCopyRaw.setEnabled(not error)
        if error:
            self.form.logsDetailTitle.setText("Selected record")
            self.form.logsDetailMeta.setText("")
            self.form.logsDetailMetrics.setText("")
            self.form.logsDetailMessage.setText("")
            self.form.logsDetailText.setPlainText("Details unavailable: " + error)
        else:
            self.form.logsDetailTitle.setText(record["event"])
            self.form.logsDetailMeta.setText(
                f"{record['level']} · {record['component']} · seq {record['seq']} · UTC {record['ts']}"
            )
            pairs, _more = characteristics(record, limit=None, preview_bytes=256)
            self.form.logsDetailMetrics.setText("   ".join(f"{characteristic_label(k)}: {v}" for k, v in pairs))
            self.form.logsDetailMetrics.setVisible(bool(pairs))
            message = record.get("msg") if type(record.get("msg")) is str else ""
            self.form.logsDetailMessage.setText(message)
            self.form.logsDetailMessage.setVisible(bool(message))
            self.form.logsDetailText.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))
        self.form.logsDetail.show()

    def _copy_detail(self):
        if self._detail_record is not None:
            self._copy_record(self._detail_record, None)

    def _copy_record(self, record, error):
        if error:
            self.form.logsStatus.setText("Raw record unavailable: " + error)
        else:
            QApplication.clipboard().setText(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            self.form.logsStatus.setText(f"Copied raw record seq {record['seq']}")

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

        self._run(collect, delivered, kind="copy")

    def _save_menu(self):
        menu = QMenu(self.form.logsSaveMenu)
        copy_selected = menu.addAction("Copy selected rows")
        copy_selected.setEnabled(self.form.logsTable.selectionModel().hasSelection())
        copy_raw = menu.addAction("Copy raw JSONL record")
        copy_raw.setEnabled(self.form.logsTable.currentIndex().isValid())
        menu.addSeparator()
        exports = {
            menu.addAction(label): fmt
            for label, fmt in (("Export matching CSV", "csv"), ("Export matching JSONL", "jsonl"))
        }
        for action in exports:
            action.setEnabled(bool(self._session()) and self._export_cancel is None)
        menu.addSeparator()
        current_segment = self.directory / f"dtflow-{self._session()}.jsonl"
        open_raw = menu.addAction("Open raw file")
        open_raw.setEnabled(bool(self._session()) and current_segment.is_file())
        open_folder = menu.addAction("Open folder")
        # The menu runs a nested event loop; a Follow refresh meanwhile would clear the selection it acts on.
        self._timer.stop()
        try:
            choice = menu.exec(self.form.logsSaveMenu.mapToGlobal(self.form.logsSaveMenu.rect().bottomLeft()))
        finally:
            if self.active:
                self._timer.start()
        if choice is copy_selected:
            self.copy_selected()
        elif choice is copy_raw:
            self._selected_action("copy")
        elif choice is open_raw:
            # Explicit user action: the active segment opens in the system's default viewer.
            self._open(current_segment, directory=False)
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
        if not sid:
            return
        try:
            filters, (sort, desc, metric) = self._query_arguments()
        except ValueError as exc:
            self.form.logsNotice.setText(str(exc))
            self.form.logsNotice.show()
            return
        # Exports never overwrite, so the native picker must not ask to replace a file.
        chosen, _ = QFileDialog.getSaveFileName(
            self.form,
            f"Export matching {fmt.upper()}",
            f"diagnostic-excerpt.{fmt}",
            f"*.{fmt}",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() != f".{fmt}":
            target = target.with_name(target.name + f".{fmt}")
        if target.exists():
            self.form.logsStatus.setText(f"Export not started: {target.name} already exists; choose a new name")
            return
        self._start_export(sid, target, fmt, filters, sort, desc, metric)

    def _start_export(self, sid, target, fmt, filters, sort, desc, metric):
        """Run one export on the pool; progress, cancel and outcome use the shared overlay."""
        cancel = Event()
        self._export_cancel = cancel
        signals = _ExportSignals(self.form)
        strip = activity_strip(self.form)
        label = f"Exporting {target.name}"
        if strip is not None:
            strip.start(
                "logs-export", label, cancel.set, stop_text="Cancel export", detail="Selecting matching records…"
            )
        self.form.logsOperationStatus.setText(label + "…")

        def progressed(done, total):
            if strip is not None and self._export_cancel is cancel and not cancel.is_set():
                strip.update("logs-export", done, total, detail=f"{done} of {total} records written")

        def finished(manifest, error):
            self._export_cancel = None
            signals.deleteLater()
            if error is None:
                state, text = export_outcome(manifest)
                title = "Logs exported"
            elif isinstance(error, EvidenceError) and error.reason == "cancelled":
                state, title, text = "stopped", "Logs export cancelled", f"{target.name} was not written."
            else:
                reason = error.reason if isinstance(error, EvidenceError) else ""
                state, title = "error", "Logs export failed"
                text = f"{target.name}: " + EXPORT_FAILURES.get(reason, f"{type(error).__name__}: {error}")
            self.form.logsOperationStatus.setText(f"{title}: {text}")
            if strip is not None:
                strip.complete("logs-export", title, text, state)

        signals.progress.connect(progressed, Qt.ConnectionType.QueuedConnection)
        signals.finished.connect(finished, Qt.ConnectionType.QueuedConnection)
        self.pool.start(
            _ExportJob(
                lambda: export_matching(
                    self.directory,
                    sid,
                    target,
                    fmt=fmt,
                    filters=filters,
                    sort=sort,
                    descending=desc,
                    metric=metric,
                    cancelled=cancel.is_set,
                    progress=signals.progress.emit,
                ),
                signals,
            )
        )

    def _cancel_export(self, *_):
        if self._export_cancel is not None:
            self._export_cancel.set()

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
            combo.setCurrentIndex(combo.findData("WARNING") if combo is self.form.logsLevelCombo else 0)
            combo.blockSignals(False)
        if self.form.logsFollowButton.isChecked():
            self.refresh()
        else:
            self.form.logsFollowButton.setChecked(True)  # refreshes through _toggle_follow

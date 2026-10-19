"""Native Logs workspace over the frozen Qt-free diagnostic reader.

Queries, detail hydration and exports execute outside the GUI thread.
The Qt model stores bounded projections, never complete JSONL payloads.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QFileDialog, QHeaderView, QMenu, QStyleOptionViewItem

from dtflow._diagnostic_reader import PAGE_SIZE, discover, query, read_detail, snapshot
from dtflow._diagnostic_export import export_matching
from dtflow.diagnostic_projection import characteristics
from dtflow.ui.theme import load_tokens
from dtflow.ui.logs_delegate import LogCellDelegate


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

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
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
                return ("   ".join(f"{key}: {value}" for key, value in self.previews[index.row()][0])
                        + (f"   +{self.previews[index.row()][1]} more" if self.previews[index.row()][1] else ""))
                        if index.row() < len(self.previews) else "Loading characteristics…"
            return "Details   Copy"
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{record['level']} · {record['component']} · {record['event']} · seq {record['seq']}"
        if role == Qt.ItemDataRole.BackgroundRole and column == 0:
            key = {"DEBUG": "color.disabled", "INFO": "color.secondary",
                   "WARNING": "color.warning", "ERROR": "color.error",
                   "CRITICAL": "color.error"}.get(record["level"], "color.secondary")
            return QColor(self.palette[key])
        if role == Qt.ItemDataRole.TextAlignmentRole and column == 4:
            return Qt.AlignmentFlag.AlignCenter
        return None


class LogsWorkspace(QObject):
    """Window-owned UI controller; reader and writer responsibilities stay independent."""

    def __init__(self, form, directory: Path, on_close):
        super().__init__(form)
        self.form, self.directory, self.on_close = form, Path(directory), on_close
        self.pool = QThreadPool.globalInstance()
        self.model = _LogModel(form.logsTable)
        self.form.logsTable.setModel(self.model)
        self.delegate = LogCellDelegate(self.form.logsTable)
        self.form.logsTable.setItemDelegate(self.delegate)
        self.delegate.actionRequested.connect(self._row_action)
        self.form.logsTable.verticalHeader().hide()
        header = self.form.logsTable.horizontalHeader()
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
        for label in ("Datetime · newest first", "Datetime · oldest first", "Event · A to Z",
                      "Event · Z to A", "Component · A to Z", "Component · Z to A",
                      "Severity · most severe first", "Severity · least severe first"):
            self.form.logsSortCombo.addItem(label)
        self.form.logsFollowButton.setCheckable(True)
        self.form.logsFollowButton.setChecked(True)
        self.form.logsCloseButton.clicked.connect(on_close)
        self.form.logsRefreshButton.clicked.connect(self.refresh)
        self.form.logsMoreFiltersButton.clicked.connect(
            lambda: self.form.logsAdvanced.setVisible(not self.form.logsAdvanced.isVisible()))
        self.form.logsFollowButton.toggled.connect(self._toggle_follow)
        self.form.logsDetailCollapse.clicked.connect(self.form.logsDetail.hide)
        self.form.logsResetFilters.clicked.connect(self.clear_filters)
        self.form.logsCopyVisible.clicked.connect(self.copy_visible)
        self.form.logsSaveMenu.clicked.connect(self._save_menu)
        self.form.logsTable.clicked.connect(self._row_clicked)
        self.form.logsSessionCombo.currentIndexChanged.connect(self.refresh)
        for control in (self.form.logsSearch, self.form.logsFrom, self.form.logsTo,
                        self.form.logsField, self.form.logsValue):
            control.editingFinished.connect(self._filtered)
        for control in (self.form.logsLevelCombo, self.form.logsComponentCombo,
                        self.form.logsEventCombo, self.form.logsSortCombo, self.form.logsOperator):
            control.currentIndexChanged.connect(self._filtered)
        self.token = 0
        self.active = False
        self._jobs = []
        self._last = ()
        self._page = []
        self._pending = 0
        self._file_snapshot = None
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

    def open(self):
        self.active = True
        sessions = discover(self.directory)
        chosen = self.form.logsSessionCombo.currentText()
        self.form.logsSessionCombo.blockSignals(True)
        self.form.logsSessionCombo.clear()
        self.form.logsSessionCombo.addItems(reversed(sessions))
        if chosen in sessions:
            self.form.logsSessionCombo.setCurrentText(chosen)
        self.form.logsSessionCombo.blockSignals(False)
        self.refresh()
        self._timer.start()

    def stop(self):
        self.active = False
        self.token += 1
        self._timer.stop()

    def _filters(self):
        filters = {"search": self.form.logsSearch.text().strip(),
                   "level": self.form.logsLevelCombo.currentText(),
                   "component": self.form.logsComponentCombo.currentText()}
        if filters["level"] == "All levels":
            filters["level"] = ""
        if filters["component"] == "All components":
            filters["component"] = ""
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
                filters[field] = dt.astimezone(timezone.utc).isoformat(
                    timespec="milliseconds").replace("+00:00", "Z")
        characteristic = self.form.logsField.text().strip()
        if characteristic:
            if not characteristic.startswith("data."):
                raise ValueError("Characteristic must start with data.")
            filters["field"] = tuple(characteristic.split("."))
            filters["op"] = self.form.logsOperator.currentText()
            raw = self.form.logsValue.text().strip()
            try:
                filters["value"] = json.loads(raw)
            except ValueError:
                filters["value"] = raw
        return filters

    def _sort(self):
        values = (("ts", True), ("ts", False), ("event", False), ("event", True),
                  ("component", False), ("component", True), ("level", True), ("level", False))
        return values[self.form.logsSortCombo.currentIndex()]

    def _filtered(self, *_):
        self.form.logsFollowButton.setChecked(False)
        self.refresh()

    def _toggle_follow(self, enabled):
        if enabled:
            self.form.logsSortCombo.setCurrentIndex(0)
            self.refresh()

    def refresh(self, *_):
        self.form.logsRefreshButton.setText("Refresh")
        if not self.active:
            return
        sid = self.form.logsSessionCombo.currentText()
        if not sid:
            self.model.replace([])
            self.form.logsStatus.setText("No diagnostic sessions")
            return
        try:
            filters, (sort, desc) = self._filters(), self._sort()
        except ValueError as exc:
            self.form.logsNotice.setText(f"Invalid filter: {exc}")
            self.form.logsNotice.show()
            return
        self.form.logsStatus.setText("Loading session…")
        def load():
            status, count, page, cursor = query(
                self.directory, sid, filters=filters, sort=sort, descending=desc)
            projections = []
            # Page-only bounded previews, never complete records in the model.
            for _tiny, ref in page:
                try:
                    full = read_detail(self.directory, ref)
                    pairs, more = characteristics(full, limit=6, preview_bytes=64)
                    projections.append((pairs, more))
                except Exception:
                    projections.append(((("Details", "unavailable"),), 0))
            return status, count, page, cursor, projections

        self._run(load, self._show_results)

    def _show_results(self, result, error):
        if error:
            self.form.logsNotice.setText(f"Diagnostic reader unavailable: {error}")
            self.form.logsNotice.show()
            self.form.logsStatus.setText("Read failed")
            return
        status, count, page, _next, projections = result
        self._page = page
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
        self.form.logsTable.resizeRowsToContents()  # only the bounded visible page; no full-session scan
        self.form.logsStatus.setText(f"{count} matching · {status.state} · displaying {len(page)}")
        self.form.logsNotice.setVisible(status.state not in ("complete", "pending"))
        self.form.logsNotice.setText("Incomplete diagnostic evidence: " + ", ".join(status.reasons))
        self._last = tuple((r.seq for _, r in page))
        self._pending = 0
        self._file_snapshot = tuple((p.path.name, p.device, p.inode, p.size)
                                    for p in status.segments)
        self._populate_filter_values(page)
        if self.form.logsFollowButton.isChecked():
            self.form.logsTable.scrollToTop()

    def _populate_filter_values(self, page):
        for combo, current, options, initial in (
            (self.form.logsComponentCombo, self.form.logsComponentCombo.currentText(),
             sorted({row["component"] for row, _ in page}), "All components"),
            (self.form.logsEventCombo, self.form.logsEventCombo.currentData(),
             sorted({row["event"] for row, _ in page}), "All events"),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(initial, "")
            for choice in options:
                combo.addItem(choice, choice)
            if combo is self.form.logsEventCombo:
                families = sorted({name.split(".")[0] for name in options})
                for family in families:
                    combo.addItem(f"{family}.*", f"{family}.*")
                index = combo.findData(current)
            else:
                index = combo.findText(current)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    def _poll(self):
        if not self.active:
            return
        sid = self.form.logsSessionCombo.currentText()
        if not sid:
            if discover(self.directory):
                self.open()
            return
        try:
            changed = tuple((p.path.name, p.device, p.inode, p.size)
                            for p in snapshot(self.directory, sid))
        except (OSError, ValueError):
            changed = ()
        if changed == self._file_snapshot:
            return
        if self.form.logsFollowButton.isChecked():
            self.refresh()
        else:
            self._pending = max(1, self._pending)
            self.form.logsRefreshButton.setText("Refresh (new records)")

    def _row_clicked(self, index):
        # Selection alone never opens details; actions use the painted delegate.
        if index.column() != 4:
            self.form.logsFollowButton.setChecked(False)

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
            self.form.logsDetailTitle.setText(record["event"])
            self.form.logsDetailText.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))
        self.form.logsDetail.show()

    def _copy_record(self, record, error):
        if error:
            self.form.logsStatus.setText("Raw record unavailable: " + error)
        else:
            QApplication.clipboard().setText(json.dumps(record, ensure_ascii=False,
                                                       separators=(",", ":")))

    def copy_visible(self):
        """Copy complete selected-page evidence, never the small table projections."""
        refs = tuple(ref for _tiny, ref in self._page)
        if not refs:
            return

        def collect():
            return "\\n".join(
                json.dumps(read_detail(self.directory, ref), ensure_ascii=False, separators=(",", ":"))
                for ref in refs
            )

        def delivered(value, error):
            if error:
                self.form.logsStatus.setText("Copy unavailable: " + error)
            else:
                QApplication.clipboard().setText(value)
                self.form.logsStatus.setText(f"Copied {len(refs)} visible records")

        self._run(collect, delivered)

    def _save_menu(self):
        menu = QMenu(self.form.logsSaveMenu)
        actions = {menu.addAction(label): fmt for label, fmt in (
            ("Export matching CSV", "csv"), ("Export matching JSONL", "jsonl"))}
        choice = menu.exec(self.form.logsSaveMenu.mapToGlobal(self.form.logsSaveMenu.rect().bottomLeft()))
        if choice not in actions:
            return
        fmt = actions[choice]
        target, _ = QFileDialog.getSaveFileName(self.form, f"Export matching {fmt.upper()}",
                                                 f"diagnostic-excerpt.{fmt}", f"*.{fmt}")
        if not target:
            return
        sid = self.form.logsSessionCombo.currentText()
        filters, (sort, desc) = self._filters(), self._sort()
        self._run(lambda: export_matching(self.directory, sid, Path(target),
                                          fmt=fmt, filters=filters, sort=sort, descending=desc),
                  lambda value, error: self.form.logsStatus.setText(
                      "Export failed: " + error if error else f"Exported {value}"))

    def clear_filters(self):
        self.form.logsSearch.clear()
        self.form.logsField.clear()
        self.form.logsValue.clear()
        self.form.logsFrom.clear()
        self.form.logsTo.clear()
        for combo in (self.form.logsLevelCombo, self.form.logsComponentCombo,
                      self.form.logsEventCombo, self.form.logsSortCombo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.form.logsFollowButton.setChecked(True)
        self.refresh()

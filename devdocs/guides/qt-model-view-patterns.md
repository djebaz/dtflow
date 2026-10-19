# Efficient Qt model/view patterns for large diagnostics

**Status:** reusable engineering/research guide (2026-10-19), not proof of an implemented DTFlow Logs workspace. The [PR #105 plan](../plans/2026-10-19-structured-log-viewer.md) owns its precise limits, phases and acceptance rules; [structured diagnostics architecture](../architecture/structured-diagnostics.md) owns data-source boundaries. These patterns may also apply to other large DTFlow lists. Performance claims require native Windows measurements.

## Recommended widgets

Use **`QTableView` + a custom `QAbstractTableModel`**, consistent with DTFlow's PySide6 Widgets stack. Do not switch to QML solely for this feature. A `QTableWidget` materializes item objects and is convenient for small static lists, but is a weaker fit for a continually updated, bounded dataset whose backing source is a rotating log. `QStandardItemModel` is convenient for medium-sized generic lists but adds an unnecessary item layer for this specialized read-only projection. `QTreeView` is best reserved for an explicitly hierarchical UI, not the planned compact event/characteristic table.

The table is a **presentation cache**. The full-session source of truth is the shared Qt-free reader. A details pane requests selected record content on demand, rather than holding complete JSON and exception tracebacks in each row.

```text
Rotated JSONL -> shared validated reader -> cancellable worker search/sort/page
                                              |
                             bounded row projections + identities
                                              |
                          GUI-thread QAbstractTableModel -> QTableView
                                              |
                                selected-row details (lazy)
```

## Qt threading and update contracts

- `QAbstractItemModel` is **not thread-safe**: file scans/filtering/sorting run in background workers; only the owning GUI thread calls `beginInsertRows`, `endInsertRows`, `beginRemoveRows`, `endRemoveRows`, `dataChanged` or model reset. Deliver worker results using queued Qt signals or equivalent GUI-thread callbacks. Never mutate the underlying exposed row list from a worker.
- Accumulate live updates and apply them in **bounded batches**. Prefer precise inserts/removals over a full `beginResetModel` for each Follow tick; use a reset only when the entire query/session changes. For newest-first mode, inserts can occur at row 0; explicitly preserve selection by stable `(session_id, seq)`, rather than a row index.
- `canFetchMore` / `fetchMore` are supported by Qt for incremental display, but **must not synchronously parse the file or scan the full session**. They may request a bounded asynchronous page, with in-flight deduplication, cancellation and GUI-thread insertion when ready. Explicit pagination is equally valid.
- An ordinary `QSortFilterProxyModel` may be useful on a small loaded projection, but it does **not** implement whole-session sorting/searching and must not be used to imply that only the first 500/10,000 rows match. Global search/filter/sort belong to the reader on a cancellable worker, before selecting the displayed page.
- Follow polling occurs only while the Logs workspace is visible. Pause automatic viewport jumps when a user inspects an older record, changes sorting or selects an item. Resume only on explicit Follow; do not let a stale worker result restore a superseded selection or page.
- A request generation token and file-snapshot boundary must accompany every background page/detail/export request. Ignore stale callbacks after switching session/filter/sort, navigating Back or shutting down. Stop timers/cancel owned worker tasks before the window is destroyed.

## Large-table geometry and memory

- Prefer **fixed-height rows**, no content-driven expansion or per-row widget editors. Logs permits up to two painted characteristic lines within that fixed height, while its event pill stays single-line. Put full context/tracebacks in a separate selectable details pane.
- For the [PR #105 Logs presentation](../design-system/logs-workspace.md), use a narrow severity-indicator column, fixed Date/Time, fixed **Event** and flexible **Characteristics** column. Draw one single-line muted pill for the complete dotted event (no prefix/suffix split or underline); paint up to two visual lines of multiple semantically ordered key/value chips, with stable deterministic colors for metric-name keys and neutral values, and a `+N more` overflow marker. **No full Level, Source, Summary or Value column**. Keep severity accessible via row semantics, tooltips, filters and inspector. Do not use per-cell widgets, dynamic-height rows, `ResizeToContents`/full-model scans or global QSS changes; preserve selected-row blue focus. INFO is neutral and recorded DEBUG remains DEBUG.
- Budget **bytes, not only row count**. The PR plan proposes 500-row pages, <=10,000 resident projections, a shared 1 MiB physical record limit, a 32 MiB display-projection budget and a 16 MiB worker budget (G3/G4); these are **design constraints, not measured memory usage**. Don't cache full 1 MiB payloads for every row.
- Sort order must remain deterministic under equal values, e.g. tie-break on `(session_id, seq)`. Entire-session query results need bounded working memory and explicit interrupted/incomplete states when rotated segments disappear during a scan.
- Do not infer that no rows returned means a clean/empty session when the reader reported an unsupported schema, incomplete tail, missing segment or rejected oversized record.

## Upstream and open-source references

These are **patterns to study**, not new runtime dependencies or a blanket license to copy code. Versions/licenses and suitability must be rechecked before importing third-party source; no upstream code is vendored by this guide.

| Reference | Relevant pattern | Limitation for PR #105 |
| --- | --- | --- |
| [Qt for Python CAN-bus ReceivedFramesModel](https://doc.qt.io/qtforpython-6/examples/example_serialbus_can.html) | `QAbstractTableModel`, queued incoming frames, batch insert/remove, optional bounded ring buffer | In-memory incoming frames, not disk-backed rotating JSONL with full-session search |
| [Qt QAbstractItemModel](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QAbstractItemModel.html) and [model/view programming](https://doc.qt.io/qtforpython-6/overviews/qtwidgets-model-view-programming.html) | `canFetchMore`, `fetchMore`, model notifications and incremental population | Fetch mechanics do not solve worker scans, retained-file rotation, or completeness |
| [Qt model/view thread safety](https://doc.qt.io/qt-6/qabstractitemmodel.html#thread-safety) | GUI-thread model updates, queued background work | Requires a project-specific cancellation/shutdown contract |
| [Qt QHeaderView](https://doc.qt.io/qt-6/qheaderview.html) | Controlled sizing and avoiding expensive content-based resize scans | Real Windows responsiveness must still be measured |
| [qt-logging](https://github.com/beatreichenbach/qt-logging) | Python logging-connected LogViewer/LogCache composition and interaction ideas | Own handler/cache; **do not install a competing logging path** |
| [Qt Log Analyzer](https://github.com/BaseMax/qt-log-analyzer) | JSON-lines parsing, severity/search/export UX; inspected source uses PyQt5 `QTableWidget` and accumulates entries | Not a direct large-file bounded-memory model |
| [cutelog](https://github.com/busimus/cutelog) | Live log filtering and inspector UX | Primarily socket-stream-oriented; different source and lifetime model |
| [klogg](https://github.com/variar/klogg) | Large-file search/indexing architecture | Canonical upstream C++/Qt application, not a drop-in PySide6 component |

**CAN example caution:** study its model-notification pattern, not its implementation as copy-ready code. Bound the pending accumulator and incoming batch as well as retained rows, using row and byte budgets. After evicting rows, recompute insertion ranges from the current model row count and the actual retained incoming count; never reuse a pre-eviction row count or emit an insertion range for an empty batch. Keep all exposed model mutations and notifications on the GUI thread.

**Recommendation:** reuse the official Qt model-update pattern and learn from other viewers' UX. Keep DTFlow's single diagnostics pipeline and design its own small Qt model over the shared reader. Import no new viewer package without a separate compatibility/licensing/maintenance evaluation.

## PR #105 acceptance implications

Documented patterns do not establish performance. Confirm on the owner's Windows setup that large sessions, full-session sorted searches, follow/pause, selection/detail hydration, session rotation and corruption never block ordinary page switches or generation, and that shutdown never invokes callbacks on deleted Qt objects. Keep GUI stalls and test results as *measured* evidence, not assumed benefits of choosing `QTableView`.

No tests, UI renders or native benchmark runs were performed as part of this reference.

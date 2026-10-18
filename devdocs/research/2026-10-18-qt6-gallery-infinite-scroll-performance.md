# Qt 6 gallery batch-boundary freezes — research and offline measurements

Date: 2026-10-18  
Status: **Research only. No production implementation or Qt performance result.**  
Baseline: [squash-merged PR #89](https://github.com/djebaz/dtflow/pull/89), merge commit [`7cf49c4`](https://github.com/djebaz/dtflow/commit/7cf49c4ca942e84556c3db3fad4b35adbfdb564a).  
Canonical source inspected before merge: PR #89 head `b66ef424dbf43248edc30f133e1dd5a022c67e24`. Follow-up documentation is branched from the squash-merge result.

## Scope and observed problem

The owner reports that Option 2a now scrolls **substantially more smoothly** than the original pagination. The remaining symptom is a **brief freeze when the previous or next 100 records become mounted** in Grid/List. The current browsing contract is 100 records per numbered chip/SQLite batch, a bounded **300-card** residency window (three adjacent batches), and two 200+100 bounded queries for an auto-refresh of a full window. SQLite/snapshot reads, long-lived checked identities, direction-aware upward/downward navigation, 100/101 viewport continuity and six concurrent thumbnail decoders must not regress.

The owner symptom is real feedback; a reproducible **current 100/300 Qt GUI frame-time profile has not been collected**. Do not confuse it with a CPU Python microbenchmark or historic owner logs from the earlier 80/160 version.

## Source inspection — what still happens on the GUI thread

On PR #89's inspected code, `src/dtflow/ui/gallery_page.py::_adjacent_done` calls `_render_page` as one synchronous Qt controller operation after the asynchronous database read. `_render_page` updates all **three** presentations on each batch even when only one is visible:

1. Grid: individual `QFrame` cards with image `QPushButton`, `QCheckBox`, and two `QLabel` widgets; `CoverPainter` tracks each button.
2. List: `QTableWidget` uses per-cell `QTableWidgetItem` objects across nine columns, with `_reconcile_rows` performing row insertion, removal and moves.
3. Detail filmstrip: `_reconcile_filmstrip` creates/reorders `QPushButton` thumbnails independently of whether Detail is visible.

`_render_page` calls `setUpdatesEnabled(False)` while making these changes. That suppresses painting updates; it does **not** move widget construction or the Python/C++ Qt method calls off the GUI thread. `_size_new_entries`, selection syncing, layout invalidation, and scheduling thumbnail decodes follow the row/widget work.

**Source-derived structural count:** A fresh record creates approximately **six Qt Widgets** (five in Grid and one filmstrip) and, if all table cells are newly populated, up to **nine QTableWidgetItem instances**. Adding 100 records therefore potentially means about **600 widgets and 900 table cell items**, even when the user's current mode is only Grid or only List. These are architecture counts from method inspection, **not memory-allocation measurements, benchmark timings or per-frame measurements**. Item reuse, hidden-view behavior and other allocations require instrumented observation.

Likely contributors include creating/removing widgets, repopulating hidden presentations, table row mutation, style/polish, selection handling, and event-loop-delayed layout/paint. Their relative contributions are **not isolated yet**. Faster SQLite fetching alone cannot remove a synchronous GUI-thread freeze.

## Research — Qt-supported alternatives

All references below are official Qt documentation pages, checked on 2026-10-18; the suggestions are hypotheses pending a DTFlow prototype.

| Topic | What Qt documents | Implication for DTFlow |
| --- | --- | --- |
| [`QAbstractItemModel`](https://doc.qt.io/qt-6/qabstractitemmodel.html) | `canFetchMore()/fetchMore()`, `beginInsertRows()/endInsertRows()`, corresponding remove notifications; model methods must be invoked in the model's thread, typically the GUI thread for widgets. | Keep data fetching asynchronous, but apply short model changes on GUI thread with correct insert/remove signals and ID-based anchor preservation. |
| [Official PySide6 “Fetch More” example](https://doc.qt.io/qtforpython-6/examples/example_widgets_itemviews_fetchmore.html) | On-demand model population and `beginInsertRows/endInsertRows` in an item-view example. | Reference pattern for incremental arrival. Its illustrative batch count is not an endorsed DTFlow page size. |
| [`QListView`](https://doc.qt.io/qt-6/qlistview.html) | `IconMode` and wrapping for Grid; `Batched` layout mode processes layout in batches during event processing, with default `batchSize=100`; `uniformItemSizes` optimizes truly uniform cell sizes. | Promising Qt Widgets Grid prototype. **Layout batchSize is not the database pagination size**; tune separately, and keep icon-cell geometry honest. |
| [`QStyledItemDelegate`](https://doc.qt.io/qt-6/qstyleditemdelegate.html) | Delegate `paint()` draws items; `editorEvent()` can handle item interaction. | One delegate can paint cards and checks rather than mounting multiple QWidget objects per image. Needs careful hit testing, focus, accessibility, selection and native style parity. |
| [Qt Model/View Programming](https://doc.qt.io/qt-6/model-view-programming.html) | Model/view separation and incremental fetch interfaces are intended for larger datasets. | A shared model could feed Grid (`QListView`) and List (`QTableView`), avoiding independent hidden-view reconciliation. |
| [`QWidget::setUpdatesEnabled`](https://doc.qt.io/qt-6/qwidget.html) | Temporarily disables updates. | Does not make GUI-thread creation/reconciliation asynchronous. Retain only for bounded repaint suppression. |
| [Qt Quick `GridView`](https://doc.qt.io/qt-6/qml-qtquick-gridview.html) | Buffering/caching and reusable delegates support efficient view presentation. | A possible virtualization option, but introducing QML into the existing Qt Widgets app is a larger, riskier rewrite; not the first experiment. |

Separate concepts rigorously: **database page size (100)**, **resident metadata/window size (300)**, **Qt item-view layout batch size**, **visible delegate count**, **thumbnail decoder concurrency (6)**, and **memory footprint** are different quantities.

## Test 1 — isolated Python row-reconciliation proxy

A standalone Python 3 script transcribed the identity-ordering algorithm of `GalleryPage._reconcile_rows`, replacing Qt with `FakeTable`/`FakeCell` and nine fake columns. It enumerates seven deterministic synthetic scenarios, **300 repetitions each**, and checks the final row identity order and repeatable fake-method call counts. This is a **Python algorithm proxy, not a real Qt test**, and its absolute timings are **not comparable** to the production GUI or to a future `QAbstractItemModel` implementation.

Environment for a fresh 2026-10-18 rerun: Linux x86_64, kernel 6.18.55, CPython 3.13.5. Timing is monotonic Python wall-clock. No SQLite, provider network, widget creation, graphics, Qt event loop, offscreen renderer, or model inference was involved.

| Scenario | Starting → desired rows | Fake inserts | Fake removals | Median Python ms | p95 Python ms |
|---|---|---:|---:|---:|---:|
| First 100 | 0 → 100 | 100 | 0 | 0.0852 | 0.1046 |
| Append 100 → 200 | 100 → 200 | 100 | 0 | 0.2805 | 0.3875 |
| Append 200 → 300 | 200 → 300 | 100 | 0 | 0.4963 | 0.6946 |
| Slide 300 forward by 100 | 300 → 300 | 100 | 100 | 0.7887 | 1.0067 |
| Slide 300 backward by 100 | 300 → 300 | 100 | 100 | 0.9517 | 1.1877 |
| Prepend 100 → 200 | 100 → 200 | 100 | 0 | 0.3166 | 0.5091 |
| No-op 300 | 300 → 300 | 0 | 0 | 0.4839 | 0.6692 |

All case outputs matched the expected order. A previous same-size saved sample (also 300 repeats) gave medians around 0.09, 0.30, 0.50, 0.79, 0.99, 0.32, 0.46 ms respectively; operation counts matched. Times vary with CPU load. The proxy measures only Python list/identity bookkeeping and fake calls: low values are **consistent with**, but do **not establish**, a hypothesis that actual QWidget/QTableWidget calls dominate.

### Reproduction notes

The complete standalone Python probe source and output CSV were kept as conversation artifacts (`gallery_reconcile_probe.py` and `gallery_reconcile_probe_results.csv`); the repository receives **documentation only**, not an executable benchmark or new dependency. For an independent implementation, emulate the exact current `_reconcile_rows` row-order loops with a nine-column fake table, repeat each case 300 times, use `time.perf_counter_ns` around the reconciliation method only, assert the expected identity order after populating new fake cells, and report fake insert/remove counts. A future **code PR** should add a reviewed reproducible benchmark harness before claiming Qt throughput.

For auditing, the exact deterministic case boundaries in the probe are:
- `first-100`: empty → IDs 0–99.
- `append-100-to-200`: IDs 0–99 → 0–199.
- `append-200-to-300`: IDs 0–199 → 0–299.
- `slide-forward-300`: IDs 0–299 → 100–399.
- `slide-backward-300`: IDs 100–399 → 0–299.
- `prepend-100-to-200`: IDs 100–199 → 0–199.
- `reconcile-no-op-300`: IDs 0–299 → identical IDs.

**Warning:** the metrics named fake Qt-call counts do not indicate actual C++ Qt calls, nor measure paint or QWidget construction.

## Test 2 — historical application `-x --dev` logs

Four local logs from previous owner smoke sessions were re-parsed using the literal `gallery_perf phase=adjacent_reconcile elapsed_ms=...` and `phase=grid_layout elapsed_ms=...` records. **They belong to an earlier 80-image / 160-card configuration**; source-commit provenance was frequently unavailable. These do **not** measure the current 100/300 trial.

| Log name | Adjacent completions | Median synchronous reconcile (ms) | Median Grid placement (ms) |
|---|---:|---:|---:|
| `dtflow-29e61bdd9d35(1).log` | 6 | 730.0 | 3.2 |
| `dtflow-29e61bdd9d35.log` | 3 | 565.8 | 3.1 |
| `dtflow-6680cf2eb8d9.log` | 13 | 592.6 | 3.0 |
| `dtflow-746394ac5d9c.log` | 3 | 761.7 | 3.4 |

These recordings distinguish full gallery reconciliation (hundreds of milliseconds) from the isolated Grid placement calculation (around three milliseconds). They do **not** pinpoint how much is spent in widget allocation, table row changes, hidden views, repaint/style propagation, decoding, or Windows compositor scheduling. Do not infer frame rate or a validated improvement factor from either table.

## Environment limitation — no native Qt microbenchmark

The available container has Python 3.13.5, and import discovery found **no PySide6 or PyQt6**. An earlier isolated `uv` install/venv attempt could not obtain Qt wheels because outbound package-index DNS was unavailable. No Qt binary was installed or tested. The GitHub Actions **230-state offscreen render** is correctness/layout evidence from CI, not a local main-thread benchmark or a frame-time measurement. No Windows build ran here.

## Recommendations and next-phase acceptance

### Phase A — instrument before changing the gallery

Keep current 100/300 navigation and immutable read-only snapshot authority. Under existing `-x --dev`, add segmented monotonic durations within `_render_page` for:
- Grid card create/remove, sizing/style and data assignment;
- `_reconcile_rows` (List), `_reconcile_filmstrip` (Detail);
- selection/check state changes, `_update_entry` and thumbnail scheduling;
- post-insertion Qt layout and actual GUI event-loop lag across the operation.

Record only sanitized event type, mode, direction, counts, elapsed milliseconds, and 100/200/300 residency; **never IDs, paths, full prompts, credentials or raw image payloads**. Separate SQLite fetch timing, main-thread synchronous time and asynchronous thumbnail decode completion. Capture p50/p95/max over comparable repeat trials.

### Phase B — small isolated Qt Widgets prototype

Build a **nonproduction** PySide6 QAbstractItemModel with `QListView` `IconMode` for Grid, `QTableView` for List, and a lightweight `QStyledItemDelegate` (including checkbox and click/focus handling). Compare against the existing widget-per-card architecture on the **same Qt version, OS, host, 100-image page boundaries, 300-record window, synthetic images, thumbnail sizes, filter sort, and repeated scroll sequence**. Time event-loop stalls, prepend/append/swap, scroll-anchor preservation, selection and memory. Preserve the model object on GUI thread; perform slow reads/decoding in workers with queued GUI updates.

Important caveat: model/view does not guarantee zero GUI work or automatic row virtualization under all modes. `QListView.Batched` batches **layout**, not database loading, and `uniformItemSizes` is safe only with true uniform cell metrics. Do not claim better FPS until this prototype is measured.

### Phase C — minimal improvement alternative

If a full rewrite is not justified, first profile **skipping hidden List/filmstrip synchronization** and scheduling widget construction/update in **small, bounded chunks across event-loop turns**. This approach may retain the existing visuals but must protect stable-identity selection, rapid direction reversals, stale fetch cancellation, scroll anchors, viewport counts and read-only snapshot fallback. Choose chunk sizes from measured event-loop latency, not arbitrary hard-coded "16 items per frame" folklore.

### Completion criteria

- Reproduce transitions: initial 100, append to 200, append to 300, evict-forward/backward at full 300, jump to page 7/9, zoom Grid/List, 100/101 upward boundary, auto-refresh (including 200+100), snapshot fallback and stop/restart.
- Define measured **p50/p95/worst** main-thread blocks and GUI event-loop stalls before setting numerical latency targets.
- Compare in native Windows with mouse wheel and precision touchpad, normal/fullscreen and representative large thumbnail densities.
- Preserve same visible identity after every prepend/eviction/resize; no backward movement from downward scroll, no orphan selections, no cross-source snapshot splicing.
- Keep exact 100/300 buffering semantics unless a separately reviewed change demonstrates benefit.
- Produce a separate implementation PR only after one experiment shows a reproducible improvement without regressions.

## Verification / nonverification declaration

**Changed here:** one Markdown research report only. No Python source, UI code, tests, dependency metadata, project setup, dataset, models, checkpoints or binary assets were changed.  
**Verified:** PR #89 source methods reviewed; official Qt docs checked; seven fake-Python scenarios rerun 300 times with identity assertions; four historical log summaries parsed; latest PR #89 CI and offscreen renders verified separately.  
**Not verified:** any PySide6/Qt microbenchmark, real GUI frame times, RAM/VRAM difference, Windows native or packaged behavior, root cause of the *current* 100-card boundary freeze, or superiority of the recommended alternatives.  
**Training/inference/billable:** none. The investigation used no GPU, provider generation, billable model API or live remote service other than reading official documentation/GitHub source.

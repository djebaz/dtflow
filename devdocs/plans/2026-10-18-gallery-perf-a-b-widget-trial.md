# Gallery performance: instrumentation and existing-widget scalability trial

Status: implementation experiment; no performance outcome established.
Date: 2026-10-18.
Base: merged PR #92. Research: PR #91.

## Product objective
Keep the current Grid/List/Detail UX and Qt Widgets implementation. Test a larger persistent Grid/List window before undertaking a QListView/QTableView migration. A typical library may contain 800–2,000 saved outputs; common browsing is about 400 images. These figures are workload targets, not hard limits.

## Non-negotiables
- Preserve current source authority, read-only snapshot fallback, original-file leases, durable checked identities, filters, sorting, Trash/Delete safety, Detail focus and export behavior.
- Keep existing 100-image page chips as navigational shortcuts; preserve the 200-record maximum per individual gallery query.
- Do not remove proactive **bidirectional** neighboring-zone buffering without a measured and separately approved change.
- Do not introduce new dependencies, train/generate images or call providers.
- No local tests or added tests in this PR per owner request; verification is limited to non-test inspection/validation reported explicitly.
- One commit per subtask, pushed immediately. No further documentation until pre-merge closeout.

## PR A — Measure the actual sources of stalls
A1. Extend development `gallery_perf` timing to separate CPU time and elapsed time in worker tasks where possible: query/read/build, thumbnail original read/lease, decode and GUI delivery. CPU time is thread CPU time when supported, not proof of GIL ownership; correlate timing rather than infer causality.
A2. Separate Qt main-thread preparation, hidden List/filmstrip work, Grid layout placement, zoom/resize and event-loop gaps. Track time to first usable page, time to full mount, number of resident cards, layout work and motion during preparation. Keep logs sanitized and avoid excessive per-item logging.
A3. Provide a small deterministic, disposable library benchmark generator under `tools/ui/`, writing only under `tmp/` and using local fixtures without provider calls. The script must not mutate application state or claim measured Windows performance.

## PR B — Existing-widget scalability
B1. Avoid constructing or reconciling hidden List/filmstrip controls until each view is activated, while keeping one authoritative record/selection identity state. Changes to hidden views must appear immediately when opened.
B2. Make the resident window configurable internally for experimental comparisons (300/500/1000/2000), without changing persisted preferences or CLI; keep a conservative production default until Windows evidence justifies changing it. Continue bounded asynchronous reads and preparation; never create 2,000 widgets in a single GUI task.
B3. Avoid unnecessary relocation/rebuilding of unchanged cards. Explicitly measure zoom-slider movement and continuous window resize, including reflow work and event-loop gaps; abort enlargement if these become unresponsive. Do not promise that Qt widgets reflow only visible cards.
B4. Preserve immediate bidirectional neighbor readiness, direct jumps, forward/back navigation, anchor identity, multi-selection, empty→same-results recovery, and clean shutdown.

## Windows A/B gate
Same 2,000-record synthetic library and exact build identity for 300/500/1000/2000 settings. Record cold load, first usable frame, full mount, browsing 400 records forward/back, page jumps, zoom drag and continuous resize, one-minute idle, RAM footprint, p50/p95/max GUI gap, worker wall/CPU time. Report filter/sort, Detail and Trash/Delete read-only UI paths without performing destructive operations.

Adopt the largest **measured responsive** resident window, not automatically 2,000. If no configuration eliminates stalls without unacceptable reflow cost or memory usage, stop and separately propose a Model/View prototype. Disk/decoded thumbnail caching and auto-refresh gating are deliberately separate follow-ups.

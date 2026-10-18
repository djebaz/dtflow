# Images gallery responsiveness — A+B implementation plan

2026-10-18 · Draft PR · instrumentation and existing-widget experiment

## Scope and constraints

Optimize the current PySide6 Grid/List/Detail implementation without introducing QListView/QTableView, disk thumbnail cache, database schema changes, or auto-refresh behavior changes in this PR. Preserve the current 100-item page chips, adjacent bidirectional preload, selection identities, filter/sort, Detail, snapshot/live authority and safe Trash/Delete semantics by default. No new tests or test runs in this PR at the owner's request; documentation is frozen after this initial plan commit until pre-merge reconciliation.

## A — Opt-in performance instrumentation

- Extend existing `-x` logs with per-stage wall-clock and thread CPU times (when available) for gallery query worker, item conversion, thumbnail work and Qt preparation/mount/layout. Report elapsed time separately from CPU time; neither alone proves GIL causation.
- Correlate worker completion and GUI gaps using monotonic timestamps and session/operation identifiers, with no paths, prompts or credentials.
- Record active view, mounted/visible/retained counts, zoom, grid columns, image query count, time to first usable viewport and time to full mount, scroll and page-jump latency, resize and zoom costs, plus memory usage when reliably available.
- Avoid heavy synchronous logging or per-paint log floods on the GUI thread. Existing log mode and behavior stay unchanged without `-x`.

## B — Reversible existing-widget comparison

- Benchmark the existing 300-card resident window against experimental 500 and 1,000 card caps; optionally 2,000 if responsive. Keep the default unchanged until owner Windows acceptance.
- Preserve previous and next zone preloading, retain stable widget identities, and do not build hidden Grid/List/filmstrip representations unnecessarily.
- Measure time to full mount and scroll responsiveness *during* construction. Do not synchronously create thousands of Qt widgets.
- Explicitly benchmark zoom-slider dragging, repeated window resizing (including grid-column changes), filter/sort, direct page-chip jumps, 400-image forward/back browse in the same 2,000-record library, Grid/List/Detail switches and memory.
- Stop increasing resident card counts if it introduces GUI stalls or memory growth. No Model/View rewrite in this PR.

## Commit order

1. This plan (first push; open draft PR immediately).
2. A1: Worker/query and thumbnail timing instrumentation.
3. A2: GUI lifecycle, layout, zoom, resize and navigation timing instrumentation.
4. B1: Hidden-view work avoidance with existing widget behavior.
5. B2: Reversible resident-window size trial, bounded scheduling and measured navigation.

Push after every subtask. Keep a final documentation/status reconciliation and any new automated tests for a separate owner-authorized pre-merge step. Do not claim Windows benchmarks without an actual Windows run.

## Owner Windows acceptance

Compare all trials with the same 2,000-output library, sort, zoom, display scale, Qt build, cache state and navigation script. Log GUI-gap distribution and worst stall, query wall/CPU spans, card construction and layout totals, time to usable and fully mounted, zoom/resize latency, peak RSS and thumbnail work. A successful experiment must remain responsive while browsing roughly 400 items and jumping elsewhere in the 2,000-item library. A source-level change or offscreen render is not Windows acceptance.

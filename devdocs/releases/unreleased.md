# Unreleased

Changes through 5.2.0 are consolidated in [DTFlow 5.2.0](v5.2.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Add an isolated QLineEdit style diagnostic for native, DTFlow and exact-font variants, with per-operation timings and no application-state or provider access. Production styling is unchanged.

- Offline startup diagnostic report (`python3 tools/startup_report.py <logs...>`) for comparing recorded `-x` phase timings without starting providers; measured first-paint callbacks are not asserted to be first usable window (#169). Repeated matching Windows source/packaged acceptance remains outstanding.
- Focused offline event-stage and visible animation lifecycle tests, plus queue preparation/wait/sampling render fixtures, to strengthen progress presentation review (#170). Native visual acceptance remains outstanding.

- Check the restored connection once in the background at desktop startup; retain its last-check indicator across navigation without generating or resuming work.

- Save and reload named text-to-image queue recipes, with searchable selection, explicit update/rename/delete, dirty-draft replacement and backend-rebinding review. Original prompts, variation settings and seeds stay independent of Generate and queue history; loading never starts work.

## Changed

- Faster Windows desktop startup (#169): theme resolution no longer requests the generic font fallback on Windows; glyph-only controls use artwork (status dot, queue and removal arrows, activity placeholder icon); one shared form loader with forms created under their final parents; lazy NumPy and Logs-module imports; the brand artwork is decoded once; application-wide wheel and Escape filters install on the first event-loop turn. Owner Windows source runs moved from 11.1–15.7 s to 3.5–4.1 s; these are single-machine observations, not packaged or controlled benchmarks, and #169 stays open.

- Schedule the automatic connection check and the Draw Things provider preload one second after the queued `first paint` milestone (not an observed paint event). Two owner runs recorded largest post-load GUI gaps of 70 and 138 ms, versus earlier 0.8–2.7 s observations; this does not establish that GIL contention or freezes are eliminated. The check result arrives about a second later.

- Startup diagnostics isolate shared activity-strip construction, and startup reports expose per-phase sample counts with a warning when instrumentation schemas differ; these measurements do not claim a startup speedup.

- Distinguish workspace mounting and root-layout insertion from activity-strip constructor costs, avoiding attribution of preceding startup work to the overlay itself.

- The development-only in-app QLineEdit/font warm-up phases were removed after the isolated diagnostic located the cost; `tools/ui/probe_line_edit.py` remains for isolated comparisons.

- Load optional credential and library summaries on workers, discover saved credentials only when needed, and construct Logs widgets on first use. Repeatable native Windows startup speedup remains to be established.

## Fixed

- Changing connection settings or saving/removing a key permits an immediate new connection check; late results from the retired check cannot alter the new status or unlock its controls.

- Queue concurrency coverage checks FIFO at durable admission, without assuming asynchronous preparation reaches the backend in the same order.

- Queue-history thumbnails and hover previews stay visible during background availability refresh instead of blinking empty. Failed checks still clear previews, and Open in Images continues to require fresh availability.

## Release audit

- PRs: #164, #166, #167, #171, #172, #173.
- Scope: Version bump 5.2.0 and release-note consolidation; queue thumbnail refresh continuity correction; local queue authoring templates and durable-admission FIFO coverage; startup connection check and deferred optional startup work (#168/#169), with scoped owner Windows autoconnect smoke and merge authorization. Repeatable startup measurements (#169), exact Windows executable acceptance and publication remain separate.

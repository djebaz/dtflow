# Unreleased

Changes through 5.5.2 are consolidated in [DTFlow 5.5.2](v5.5.2.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Development-only `ui.callback_slow` timings identify Script Run and queue UI callbacks above 50 ms (wall and current-thread CPU time). A passive, bounded `ui.gui_stall_sample` now captures sanitized GUI/runner frame names after a >1-second event-loop scheduling stall, without accessing Qt widgets or logging arguments from the watcher thread. This supports #261 investigation; it does not establish the cause of the previously observed 7–8 second gaps.

## Changed

- Scripts history reuses existing Qt cards when a new run is prepended or its latest status changes, instead of rebuilding every card in two inspector panels. This targets the remaining Script Run GUI stall sampled in `_sync_drawer_data` (#261); rare reorders still perform a complete refresh.

- Queues defers expensive hidden list, table and event-history rendering while Scripts or another workspace is visible, but continues publishing shared run state. Opening Queues renders the latest state immediately. This removes observed unnecessary offscreen UI work during queue dispatch (#261); broader multi-second scheduling stalls still require Windows verification.

- Development GUI heartbeat monitoring is application-wide and activity-scoped: visible Images browsing or existing in-window execution/progress work, not idle Logs. Restart resets the baseline; `ui.gui_gap` reports the active workspace/operation instead of stale gallery context. Existing benchmark readers retain historical log support without a duplicate producer. This reduces observation scope/noise, not measured stall duration; #263's actual-stall investigation and #261 remain open.

- Logs initially opens on >=WARNING (WARNING, ERROR and CRITICAL). All severity choices are consistent thresholds (ALL, >=DEBUG, >=INFO, >=WARNING, >=ERROR, >=CRITICAL); selection survives navigation and Follow, and Reset restores >=WARNING. Source/static changes and GitHub CI do not establish native Windows or repeated-stall acceptance (#263).

## Fixed

- Workflow Generate width/height editors commit typed sizes to the nearest 64-pixel multiple (ties upward) on focus loss or before Run; arrow steps remain 64 pixels. Imported invalid values retain strict validation.

## Release audit

- PRs: #264
- Scope: Issue #263 scoped heartbeat lifecycle/context and Logs severity thresholds. Actual-stall investigation remains open; no local tests or renders.

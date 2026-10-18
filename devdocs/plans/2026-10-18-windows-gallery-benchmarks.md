# Native Windows gallery performance automation

Date: 2026-10-18  
Status: implementation on dedicated PR, based on the merged gallery configuration in `main`.

## Goal

Drive the actual DTFlow PySide6 window through Windows accessibility automation, not an HTML mockup, and compare the configurable Images batch size (`-ml`), resident-card cap (`-mc`), and thumbnail format (`-format`). Reuse the existing `-x` `gallery_perf` per-session CSV and text log instead of creating a duplicate Qt timing engine.

## Boundaries

- Source checkout and native interactive Windows desktop only. Do not use `QT_QPA_PLATFORM=offscreen` or treat CI/Linux fixtures as native acceptance.
- Explicit opt-in is required because DTFlow opens the user's saved-output catalogue, uses its thumbnail cache, and saves gallery/viewer preferences on normal exit. Do not trigger Generate, queues, Export, Delete, cache cleanup or cloud actions.
- The automation selects only Images, Grid, List, Detail, pager page chips, and scrolls the gallery. Fail closed when an expected accessible control is missing, ambiguous, disabled or unresponsive.
- Launch DTFlow as its own child process, attach to that exact PID, give each trial fresh development logs, then close only that child. Never attach to an arbitrary already-running DTFlow window.
- No generations, uploads, provider calls, downloads, destructive actions or new runtime dependencies. Optional Windows-only pywinauto is installed as a pinned ephemeral uv overlay by the documented command.
- Compare the same fixed saved-output data, display resolution/DPI, input sequence, cache state, and sort/filter across runs. Measurements of different cache states or concurrent catalogue changes must not be ranked together.
- Give the application a bounded preparation window, fail if a gallery did not load, and preserve partial failure evidence. Log-event correlation is diagnostic, not causal attribution.

## Reports

Save one trial directory with original DTFlow logs/CSV and a sanitized driver JSON (configuration, action timestamps/durations, outcomes). Produce a compact, sortable summary CSV and JSON across trials, with per-phase evidence and worst observed Qt heartbeat gaps. Missing heartbeat samples mean no threshold-crossing event was recorded, not proof of zero latency. Distinguish GUI freezes from query/decode and total content-readiness. Use no unverified performance claims.

## Validation

- Pure parsing/reporting unit tests on Linux.
- Syntax, import and CLI help checks in the locked environment.
- Explicit native Windows source smoke with real saved-output gallery and stable 100–200+ images, including at least three configurations; separately record Windows DPI, cache state and manual perceived smoothness.
- Never run billable inference for UI benchmarking.

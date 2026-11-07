# Issue #263 — scoped heartbeat and Logs defaults

## Owner-aligned scope

Implement the diagnostic lifecycle/context cleanup and Warning-and-worse Logs opening default first. Keep the underlying repeated GUI stalls open: fewer heartbeat records or a quieter Logs filter do not prove reduced event-loop latency. The measured 1–2 second gaps need a separately selected before/after investigation; #261's Workflow Run startup interval is not automatically the same cause.

The owner reports that transient gallery pauses during fast scrolling/page changes are expected. Do not classify every threshold crossing as a defect or expand this slice into broad gallery optimization; unexplained idle/off-page observations remain distinct.

## Implementation plan

1. Add a minimum-severity predicate at the shared Qt-free reader seam. Owner-aligned combo choices are ALL, >=DEBUG, >=INFO, >=WARNING, >=ERROR and >=CRITICAL, not exact-level matches. First opening and explicit Reset select >=WARNING (WARNING, ERROR, CRITICAL); preserve deliberate severity changes across refresh/follow/navigation/reopening. Existing shared exact predicates remain available to independent reader callers, not the combo UX.
2. Replace the always-on gallery timer with one window-parented, development-only GUI heartbeat. Reuse Images activity, existing execution activity and shared progress lifecycle; stop while idle outside Images, reset the monotonic baseline on restart/context changes, and stop at shutdown. Observe the active workspace/operation rather than stale gallery state.
3. Preserve the useful 20 ms cadence, >=50 ms source population and >=100 ms individual outliers without a second legacy producer. Reuse existing bounded timing summaries and shared logging writer/schema. Update the live gallery benchmark consumers narrowly to read new events and cheap historical compatibility, without conflating aggregates with individual stalls.
4. Extend offline render definitions for Warning-and-worse presentation, update guidance/pending release notes, and open a reviewable PR. Do not automatically close #263.

## Evidence and validation boundaries

- Baseline: clean main `b3f2ce455a9e458b509fb58c37bbafb94f839c44`; branch `fix/issue263-heartbeat-logs-default`.
- Explicit owner restriction: no new/extended tests and no local test execution. No local renders, application/provider workloads, model downloads or inference are requested. Existing GitHub CI may run existing checks; report that exact-head evidence separately from native Windows/package acceptance.
- Static checks and source review can establish contracts, not an actual stall root cause or measured speedup. Native owner verification remains required for idle Logs, Images browsing, active execution while on Logs, restarting the monitor and threshold filtering.
- Live consumers, not just old pytest fixtures, use heartbeat evidence: `tools/ui/grid_benchmark_report.py` scores individual wheel-window gaps; `tools/ui/windows_gallery_benchmark.py` reads source maxima. Retain those useful metrics with one application producer and narrowly update readers. No old gallery heartbeat is emitted for test compatibility, and no tests are added, extended or run locally.
- Keep PR #264 draft while evolving. Only after source review and owner alignment may it become ready and run existing GitHub CI; do not dispatch CI manually during development.
- The monitor follows existing execution/progress signals; it does not add a second scheduler or instrument synchronous work before those signals (notably Workflow Run preflight). That earlier click-to-dispatch interval stays with #261 and the open investigation, not a claimed performance fix here.
- Checkpoint static checks passed: environment readiness (managed Python 3.13.13/SQLite 3.53.1), repository Ruff lint, Ruff format (528 files) and `uv lock --check`. No runtime scenario, tests or renders ran; final-head CI/owner smoke remain pending.

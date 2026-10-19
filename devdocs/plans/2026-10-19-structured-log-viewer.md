# Plan: Structured JSONL diagnostics and native Logs workspace

## Goal and scope

Implement issue #96 on top of merged PR #104 (`2945157`): purpose-designed structured diagnostic evidence for agents and benchmark tooling, presented to humans as a clean, newest-first native Qt table. One standard-library logging pipeline owns application, backend and gallery diagnostics. Logs remain diagnostic evidence, never generation state or proof of success.

- In: JSONL schema/writer, typed DTFlow emitters, existing gallery aggregation semantics, new-session readers, CSV export, benchmark reader migration, and an in-window Logs workspace.
- Out: legacy text/CSV adapters or conversion, gallery optimization, provider changes, inference, model downloads, new runtime dependencies, release/version bump, packaged build, and changes to durable job/output schemas.
- Existing `.log`/gallery CSV evidence remains untouched. New sessions write only `dtflow-<session>.jsonl` and its rotated siblings; benchmark result CSV/JSON artifacts remain reports, not parallel logging sinks.

## User decisions

- Ship the foundation and viewer together in one new PR, not separate PRs.
- Use one JSONL stream for application events and gallery measurements. CSV is an explicit derived export/report only.
- Support new JSONL sessions only. Do not parse, migrate or silently delete historical text/CSV sessions.
- Open Logs beside Help in a dedicated main-window workspace with Back, not a dialog or floating window.
- Use PySide6 `QTableView` with `QAbstractTableModel`, not `QTableWidget`, per-cell widgets or a raw JSON text dump.
- Initial plan is its own commit. Each implementation phase below is exactly one subsequent coherent commit. Keep the draft PR updated after each phase; never merge it.
- Follow the requested fast implementation workflow: do not create or modify any tests/fixtures, or run tests, linters, syntax/import checks, CLI validation, builds, screenshots/renders, recording replays, benchmarks or live/provider work. All test reconciliation is deferred until the implementation phases are finished; list affected contracts without editing them. Owner/CI execute acceptance after that deferred work. Existing CI can fail old-format expectations during implementation; keep that explicit and never disable checks to conceal it.

## Event contract (schema version 1)

Each UTF-8, BOM-free physical line is one JSON object with a terminating LF. Escape multiline messages/tracebacks inside JSON strings; never pretty-print records across physical lines. Disallow non-finite numbers and arbitrary object reprs in structured fields.

- Required envelope: `schema_version` (integer 1), `session_id`, `seq` (unique increasing persisted record order), `timestamp` (source UTC ISO-8601 with milliseconds), `monotonic_ns` (source monotonic clock), `level`, `component`, `event` (stable dotted identifier), `kind` (`event`, `measurement` or `aggregate`), `message` (concise human summary), and `data` (typed JSON object).
- Optional `context`: backend, job/attempt/operation IDs and benchmark run/trial/action IDs only when actually available. No placeholders for absent context and no invented causal links.
- Optional `error`: sanitized exception type, message and complete traceback without locals. Preserve exception structure before queue preparation can flatten it into the message.
- Optional `aggregation`: sample count and source window bounds, with explicit per-metric count/mean/p95/max/total where measured. Keep units explicit in field names (`*_ms`, `*_bytes`); do not fabricate percentiles or average window percentiles into a global p95.
- `session.started` records full available build identity, dirty/unknown state, runtime/platform and diagnostic mode. All records identify the session; the viewer can join session metadata without repeating the full environment in every row.
- Introduce a small typed logging helper around standard logging. Migrate DTFlow-owned call sites to explicit event names and typed arguments, including gallery timings, rather than parsing formatted messages in the live writer. Plain standard-library messages have a clearly generic diagnostic fallback, not guessed fields.
- Sanitize text recursively before persistence and again at viewer/export boundaries. Preserve the existing secret-environment and credential/payload redaction protections; never capture full prompts, raw image data, prepared cloud references, private paths or exception locals.

## Phase 1 — Structured logging foundation

Status: pending.

- Keep the existing queue/listener architecture and nonblocking caller behavior. Serialize, rotate and aggregate on the listener, preserving source timestamp/context across that boundary. Preserve normal versus explicit development verbosity and existing measurement locations/cadence.
- Replace automatic text/CSV fanout with one structured JSONL sink. Retain the existing 1 MB / 3 backups / 20-session limits for new JSONL files and the documented source/packaged log locations and explicit log-location override. Pruning touches only recognized JSONL session files, never old text/CSV or outputs/state.
- Audit and migrate all DTFlow-owned logging producers to stable semantic events and typed `data`; record the event families and units in the architecture documentation. Preserve large GUI-gap records individually, existing listener batching and source-side thumbnail aggregates. Add source window bounds where aggregation currently loses them.
- Preserve fail-soft logging: sanitized one-time reporting of setup/write/serialization errors must not retry generation or raise into UI/provider workflows. Flush pending aggregates on clean close.
- Update repository logging contracts/docs to describe JSONL; record existing logging assertions/fixtures needing deferred reconciliation without editing them. No validation execution.

## Phase 2 — Readers, exports and benchmark migration

Status: pending.

- Add Qt-free session discovery and structured-record reading services shared by the viewer and benchmarks. Use only the configured log directory; enumerate recognized JSONL names/rotation siblings, reject redirected/nonregular inputs, and leave files unchanged.
- Read incrementally by file identity and offset. Handle rotation, removed sessions, concurrent appends, incomplete final lines, malformed records, unknown schema versions and canceled/stale requests explicitly. Do not silently score damaged/incompatible evidence as clean.
- Use bounded viewer pages of 500 rows, a maximum 10,000 resident rows, and a 1 MiB per-record read limit. Search/filter/sort the selected session on a worker with cancellation, returning bounded pages and match counts rather than loading the entire session into Qt. Reader limits do not truncate stored tracebacks; oversized records are reported as unavailable rather than presented as complete.
- Adapt PR #104 query-readiness and wheel-intent observation to session/sequence-based record identity, and adapt gallery summaries/Grid scoring to structured fields. Preserve clean-trial gates, warning/failure exclusion, measured sample populations, >=100 ms Grid stall threshold and startup-versus-scrolling separation. Use exact source timestamps rather than the former whole-second widening; disclose that boundary-precision change in report metadata. Do not change ranking policy or claim historical and new scores are identical.
- Keep benchmark report CSV/JSON and trial manifests. Remove reliance on an automatically generated gallery CSV or text-log regexes. New readers explicitly refuse old formats; no compatibility auto-detection.
- Provide sanitized readable/JSONL/CSV export operations over selected or matching records, using workers for large exports and native destination pickers. Exports never alter source diagnostics. No persistent export index or second logger.
- List existing reader/report assertions and fixtures needing deferred reconciliation; leave all test files unchanged. No tests, benchmarks or validation executed.

## Phase 3 — Native Logs workspace and delivery

Status: pending.

- Add a small Logs control beside Help and lazy-create the canonical Designer Logs form when first opened. Integrate a dedicated main-window workspace safely: the current navigation handler assumes exactly four indexed pages and must explicitly support Logs without indexing a nonexistent navigation button. Back restores the prior page, shell/rail arrangement and ongoing work; navigation never cancels a generation/export/queue task.
- Session selector defaults to the current/newest JSONL session. Filters: text, level, component/event and application/gallery category. Table columns: Time, Level, Component, Event, Summary, Metrics. Default order is newest first; header sorting and filters apply to the selected session, not only the currently loaded page.
- Use `QTableView`/`QAbstractTableModel`, fixed-height single-line rows, elided summaries, shared token colors and a read-only expandable details pane. Tint severity cells only: subdued DEBUG, ordinary INFO, amber WARNING, red ERROR/CRITICAL. Preserve full timestamp, data, context and traceback in selectable details. Do not introduce global palette changes or large per-row widgets.
- Parse/filter on background workers via existing AsyncRunner facilities; only mutate the model on the GUI thread. Batch insert/remove updates for follow, avoid per-record resets and resize-to-contents scans, and preserve selected records by `(session_id, seq)`.
- Default Follow is on for the current session, polling once per second only while Logs is visible. Pause follow when the user selects/inspects/scrolls older content or applies a nondefault sort; explicit Follow resumes at newest. Never pull the viewport away during inspection. No recursive log events for reads/refreshes.
- Provide Refresh, Back, Follow, Copy selected, Copy visible, Copy raw JSONL, Export matching CSV/JSONL, Open raw file and Open folder. Clipboard/export content is sanitized; opening a raw local file is an explicit user action. No delete/clear-log control.
- Keep loading, empty, no matches, malformed/unsupported data and log-write/read failure states clear and nonmodal. Stop owned timers/cancel reads at shutdown and ignore stale completions; do not let the viewer outlive its window or prevent durable settlement.
- Extend the offline render-state definitions in the same phase for empty, populated/mixed-severity, selected exception/details and reader-error states using synthetic records and the real Logs presentation. Define but do not execute previews; no test files added or modified.
- Synchronize README (GUI-first feature/navigation), AGENTS logging/UI contract, architecture, developer/desktop guides, runtime layout, current status and unreleased notes. Revise issue #96's obsolete preserve-text/CSV constraint through the PR description, not by silently claiming the old acceptance contract is met. Keep the PR draft through implementation and pending deferred test reconciliation/owner gates; source completion is not acceptance.

## Acceptance checklist — owner/CI only

- [ ] New normal/dev sessions produce valid structured events, typed measurements, preserved exception details, safe rotation/retention and no automatic text/gallery CSV sinks.
- [ ] Credential/prompt/payload/path privacy survives writer, viewer, clipboard and exports; logging failures do not affect generation semantics.
- [ ] Current/closed/missing/malformed/rotating JSONL sessions render without GUI stalls, unbounded memory or duplicated/partial records.
- [ ] Newest-first sorting/filtering, pagination, follow pause/resume, keyboard selection, full details, copying and exports work on native Windows.
- [ ] Opening/back/navigation/shutdown preserve the prior workspace and ongoing operations, including the shared bottom overlay and Images export-card placement.
- [ ] PR #104 readiness, clean/warning/failure behavior, Grid stall intervals and reports still work on newly recorded JSONL evidence; startup and scrolling remain separate.
- [ ] After all implementation phases, reconcile tests in separately scoped follow-up work, then owner/CI run applicable checks and native smoke. During these phases the agent neither modifies nor runs tests or validation; no provider submission, inference or potentially billable actions are needed.

## Progress and evidence

- Baseline inspected read-only: clean main at merged #104, current asynchronous logger/text+CSV aggregation, benchmark regex/CSV consumers, existing Qt table-model example and four-page navigation assumptions.
- No files other than this plan have been changed for this PR. No tests, checks, renders, benchmarks or provider actions have been run.

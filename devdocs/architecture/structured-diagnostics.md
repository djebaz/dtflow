# Structured diagnostics architecture

**Status (2026-10-19):** PR #105 implements the writer/typed producers, shared Qt-free reader/export layer, benchmark migration and native Logs workspace. [Delivery audit](../plans/2026-10-19-structured-log-viewer.md) records code and CI evidence separately. [#106](https://github.com/djebaz/dtflow/issues/106) owns remaining end-to-end byte budgets and lazy-form evaluation; PR #111 adds export cancellation and the Logs presentation follow-up; [#107](https://github.com/djebaz/dtflow/issues/107) owns fault/native/schema/benchmark/storage/policy acceptance. The owner explicitly moved those outstanding gates to follow-ups; no exhaustive runtime or Windows acceptance is claimed.

The [proposed compact, searchable event contract and legacy migration inventory](diagnostic-event-schema.md) and [record JSON Schema v1](../schemas/diagnostic-record-v1.schema.json) complement this ownership guide. The current design uses a strict small searchable envelope and flexible safe typed payloads: **new ordinary event/sub-event names do not require schema registration**, while benchmark/integrity fields remain selectively enforced. They document the implemented v1 format and its intended invariants; the plan's delivery disposition identifies what remains unverified. The inventory is sourced from code rather than retained user log samples, and exhaustive producer/schema agreement remains follow-up acceptance in #107.

## Pre-Phase-1 foundation: retained ownership

Before this PR, `src/dtflow/logging_setup.py` owned `configure_logging`, a standard-library `QueueHandler`/`QueueListener`, sanitization, rotated per-session text files, session pruning, and listener-side gallery performance aggregation/CSV fanout. `AGENTS.md` already requires a shared logger. This is meaningful existing centralization; a competing logging engine is unnecessary.

The pre-migration fragmentation concerned the **machine-readable meaning** of diagnostics. For example, `src/dtflow/ui/gallery_page.py` emits `gallery_perf phase=gui_gap elapsed_ms=... mode=... mounted=...` using formatting; `logging_setup.py` interprets such messages for aggregation/CSV, while `tools/ui/windows_gallery_benchmark.py` and `tools/ui/grid_benchmark_report.py` interpret text files through their own regex/readers. That string contract is fragile even though all producers share the primary file-writing pipeline.

Legacy baseline (inspected before Phase 1):

```text
CLI / Qt / services / backends
       | standard logging, some "gallery_perf phase=..." messages
       v
logging_setup.py: QueueHandler -> QueueListener -> sanitization/aggregation
                                                 |-> rotated .log
                                                 +-> gallery .csv
Separate benchmark tools -> text/CSV regex parsing
```

Implemented source topology (not a native performance claim):

```text
Ordinary logging.getLogger(...)    DTFlow typed event/measurement helper
             \                         /
              -> shared bounded capture/admission
                      -> central queue/listener
                         schema + privacy + aggregation
                         source-time preservation / persisted seq
                                  |
                           rotated JSONL session
                                  |
               shared Qt-free reader / schema validator
                        /           |             \
                 Logs viewer    benchmark      explicit exports
                (Qt model)      scoring       (CSV/JSONL/text)
```

## Ownership and dependency boundaries

- The BYOM checksum-contract follow-up records `backend.byom_upload_evidence` INFO before consuming an upload offer. Only fixed type/format labels, lengths, usability and comparison booleans are emitted; no checksum bytes, server-returned values or URLs. Local content identity stays separate from server-returned storage values; comparison is diagnostic, not an admission condition. No new sink or live acceptance is introduced.
The BYOM tolerance follow-up adds `backend.byom_response` INFO before known-branch interpretation, using the existing bounded public privacy projection plus fixed-name branch types/counts. HTTP 200 interpretation failures use `backend.response_unusable` WARNING rather than implying API rejection. No new sink or raw response bypass is introduced; signed URLs and private identities remain excluded.
PR #156 adds the public `diagnostics.api_error_data` projection, delegated to the existing `_diagnostic_schema.py` privacy authority. HTTP/gRPC producers emit `backend.api_failed` at WARNING with safe server message/body/header evidence and stable operation/status/code/timing/submission fields. Error-body capture is 8 KiB, text display is 2,048 characters, projected structures are capped at depth 6 / 128 nodes / 32 mapping members / 16 list members, and oversized/incomplete or binary content is omitted explicitly. Known request-specific prompts/credentials/cloud references supplement generic redaction before truncation; successful token bodies, signed URLs and tensors are never observations. Unknown structured gRPC details carry only type/count/omission evidence. This does not add a sink, retained-log parser, reader, retry, provider policy or verified peak-memory bound. `backend.api_completed`, `backend.asset_lookup_summary` and `backend.asset_negotiated` are DEBUG summaries. Existing UI/lifecycle events may coexist but do not duplicate response bodies. Source presence is not privacy/fault/native acceptance; the [endpoint plan](../plans/2026-10-22-api-response-diagnostics.md) records the no-validation delivery boundary.

PR #151 adds warning-level `ui.prepare_folder_unavailable` (`read_failed`/`write_failed`), `ui.asset_prepare_incomplete` (fixed outcome/reason, item/total counts, backend error code and remote-outcome enum when known) and `ui.asset_registry_unavailable` (`read_failed`) through the same producer API. These are important failure observations in normal and dev mode, not a separate sink or success-per-file trace. No filename, path, content, credential, signed URL or raw exception text is included. Unknown remote preparation outcomes remain unresolved; logging never triggers a retry.

PR #150 adds dev-only `queue.thumbnail_summary` and `gallery.open_output` measurements through the existing producer API. Thumbnail summaries accumulate fixed outcome counters (`available`, `unavailable`, `busy`, `read_decode_failed`, `worker_failed`, `obsolete`) with total/max submission-to-GUI-completion elapsed milliseconds and current active/waiting counts. The owned expiry timer flushes nonempty summaries at roughly 30-second intervals; clearing/navigation flushes the current summary, and late completions while hidden flush bounded remaining work. These timings include scheduling, live lookup, image access/decode and GUI delivery, not pure decoder CPU time. No per-paint/hover/poll event is emitted. Handoff events record one terminal outcome (`opened`, `unavailable`, `query_failed`, `query_invalid`, `busy_exhausted`, `cancelled`, `navigation`, `shutdown`), elapsed milliseconds and busy-wait count. Both projections exclude prompts, identities, paths and raw error text; they do not change normal-mode verbosity, retries or generation behavior.

Phase 1 keeps configuration/session lifecycle and JSONL-only retention in `logging_setup.py`; `diagnostics.py` is the producer API (`event`, `measurement`, `source_summary`, `ATTEMPT`). `_diagnostic_schema.py` owns bounded capture, recursive sanitization, exception extraction and record/critical-field policy; `_diagnostic_writer.py` owns `QueueHandler`/`QueueListener` subclasses, admission accounting, listener aggregation, UTF-8/LF rotation and close seals. UI/services/backends import producer APIs only. `_diagnostic_reader` reuses record validation and owns discovery, status, whole-session queries and lazy details; `_diagnostic_export` and the Qt/benchmark consumers use that boundary rather than alternate parsers.

The queue budget includes conservative reservations for concurrent capture, not just queued records (1,024 pending records / 8 MiB; depth 16 / 4,096 nodes). A source observation is never retained as a mutable `LogRecord`, traceback or provider graph on the queue. The listener keeps at most 64 aggregate groups, each with at most 64 samples and a fixed small metric set; reaching group capacity flushes the oldest batch early without discarding observations. Known dimensions/attempt IDs are length-bounded for batching; extensions outside this policy remain individual records. All individual wheel receipts and >=100 ms GUI outliers remain observable. Original numeric metric keys such as `mounted`/`offset` are preserved rather than receiving invented suffixes; the documentary schema permits those source keys.

`session.started` declares instrumentation, capacities, `source-64-or-10s-bounded64-legacy1dp-v1` and numeric policy. Typed individual observations/source-summary data retain native finite values; listener batch inputs preserve the old one-decimal population before computing statistics. The migrated legacy report adapter is required to apply the former one-decimal rendering precision to individual/source-summary/statistic values where the old text/CSV did so, explicitly in report metadata, without changing raw JSONL or silently correcting report conventions. Source timestamp attribution is the separately versioned intentional precision change under G5.

`session.closed` counts distinguish `observed`, `admitted`, `capture_rejected`, `dropped`, `processed_records`, serialization rejections/failures and persisted `written_records` (including controls/seal). On a drained close, observed = admitted + capture_rejected + dropped, and processed_records = admitted; timeout is unsealed. `aggregate_source_samples` counts listener inputs, while `source_summary_samples` counts upstream summarized populations; neither is a persisted-record count and overlapping populations must not be added as unique measurements. A seal with any nonzero loss/failure is not clean evidence. Capture rejections coalesce in fixed reason/severity/measurement buckets: markers explicitly identify their last representative event and count, not a complete list of lost identities. The close deadline is two seconds; a timed-out or failed sink cannot certify closure and no fallback file is written.

Accepted exception traces retain all captured frames and causal/context chains without locals or source-code snippets, with recursive sanitization. Unsupported groups, deep/oversized traces or unsupported generic objects are rejected explicitly, never formatted through arbitrary repr or silently truncated. Read/copy/export privacy reuse and evidence-status enforcement are implemented consumer paths; exhaustive privacy/evidence fidelity remains #107.

### Phase 1 corrective source checkpoint

The [corrective subplan](../plans/2026-10-19-phase-1-logging-corrections.md) distinguishes rotation collisions from terminal failed/partial writes. Rotation retries use monotonic exponential backoff capped at two seconds, dropping the current output instead of unbounded appends; no producer sleeps for rotation. Each failed rename attempt increments `writer_failures`/`rotation_failures` (writes skipped during backoff do not), while `unwritten_records` counts unavailable ordinary/aggregate output records (not unique source observations). `diagnostics.loss` uses `reason=writer_error`; its counters are not admission drops and must not be added to the observed/admitted equation. Loss markers advance their reported cursors only after successful persistence. A recovered rotation may permit a close record with nonzero failure counters, never clean evidence; a terminal sink failure or expired close remains unsealed.

Capture now reserves a 1 KiB base and charges incremental conservative allocations under the shared admission lock before copying/formatting; reserved bytes transfer atomically into queued bytes, and release on rejection. It does not prepare an unbounded snapshot and check afterward. Home/package context is cached; credential values are sampled per observation, not cached across auth changes. Generic formatting uses bounded native snapshots, reserves expansion before formatting and sanitizes the final result; full paths are redacted, while safe MIME/ratio/model identifiers remain readable. Tracebacks expose package/site-packages-relative locations and redact unknown external locations, without locals or source snippets. No speedup or measured memory usage is claimed.

Each new session holds a nonblocking OS lock in `dtflow-<session>.owner` until the listener closes its sink, including a timed-out listener still draining. Best-effort pruning must acquire that session's ownership lock before removing regular JSONL segments. Busy, missing, redirected or unknown ownership is skipped, so twenty sessions is a retention target rather than a hard bound on active/unknown evidence. Stable ownership files are never unlinked automatically, preventing split-lock races; tiny coordination files can accumulate and are not diagnostic records. Pre-policy JSONL sessions without ownership files and historical text/CSV are left untouched. They require explicit owner cleanup while all DTFlow processes/readers are stopped.

The packaged desktop routes logging warnings into a one-message Qt-free mailbox; a window-owned QObject polls and presents the shared inline warning, stopping with runner shutdown. No listener-thread `MessageBoxW` is invoked. Pre-window warnings wait for window creation; post-shutdown warnings cannot reopen a window/dialog. CLI logging retains its console reporter. Native inline notification rendering, Windows lock/rotation behavior, fault handling and shutdown remain owner/CI acceptance, not established by source inspection.

Only `scroll_intent` and `gui_gap` measurement phases enter listener aggregation; thumbnail/preparation source summaries are not reaggregated. Idle ticks expire batches ten seconds after their first source monotonic timestamp, while persisted first/last source timestamps and sample populations remain unchanged. Sink writes/rotation share the handler close lock. The two-second explicit session-close wait remains bounded; interpreter-level logging shutdown is not a guarantee against arbitrary blocked OS I/O.

| Responsibility | Only owner / allowed caller | Not allowed |
| --- | --- | --- |
| Logging configuration and lifecycle | `logging_setup.py` is the entry point; small owned internal modules are fine | Independent persistent logger setup in a UI page, backend or tool |
| Ordinary application/third-party log messages | Standard-library `logging.getLogger(__name__)` | Forcing unrelated libraries into a new facade or modifying their propagation globally |
| Structured DTFlow measurements | Small typed emission API at existing measurement sites | Hand-built JSON, key-value formatting as an implicit machine schema, or direct file writes from widgets |
| Envelope, validation, source clocks, persisted sequence, privacy, aggregation | One diagnostics subsystem, with writer-side state on the queue listener | Separate event definitions, sanitizers, sequence counters or retention policies scattered across consumers |
| Session discovery, rotated-file reading, record decoding, status and sanitization | One Qt-free reader/schema implementation | Log-specific regexes or JSONL parsers duplicated in the GUI, export or benchmark |
| Table presentation and interaction | Qt Logs workspace, driven by validated row projections | UI ownership of diagnostic file I/O or report interpretation |
| Benchmark meaning and ranking | Benchmark-specific scoring using shared validated events | A second ad hoc parser or conflating absent/lost measurements with zero stalls |
| Explicit exports and benchmark artifacts | Derived operations with shared reader and sanitization | An automatic second logging sink or rewriting original evidence |

This is **logical centralization**, not a demand for one giant source file. Possible internal separations include schema, emitter, writer, aggregation and reader; actual names should follow the repository's conventions and remain minimal. Avoid dependencies from the core diagnostics reader onto Qt or generation/provider modules.

## Events, evidence and privacy

The format aims for reasonable measured storage overhead, **not** byte parity or an arbitrary percentage target at the expense of filtering or benchmark trust. Store session build/runtime metadata once in `session.started`; use only a handful of stable top-level search characteristics per record, keep typed descriptive names inside `data`, and rely on the shared Qt-free reader for full-retained-session field comparisons and hierarchical event-prefix filters. Rotation gaps/startup loss make evidence incomplete rather than triggering invented metadata. Storage overhead, retained evidence and native UI impact remain unmeasured until owner replay.

The catalogue owns the [core typed-query rules](diagnostic-event-schema.md#shared-query-semantics--phase-2-contract) and [derived JSONL excerpt/manifest boundary](diagnostic-event-schema.md#canonical-sessions-versus-derived-jsonl-excerpts). Export manifests carry bounded provenance without changing ordinary v1 records or manufacturing session seals; excerpts never enter automatic canonical session discovery. The workspace specification owns versioned display ordering/palette rules, reused through one projection path rather than reinterpreted by each consumer.


The [plan's schema and G1–G5](../plans/2026-10-19-structured-log-viewer.md#accepted-owner-review-guards) are authoritative. The guiding invariants are:

- Source `ts` and relevant monotonic `mono_ns` observation time are captured at the event origin; persisted `seq` identifies listener-written order and **must not** be used as a substitute for source time. Aggregates retain their source window and sample population.
- Top-level searchable `ts`, `level`, `component`, and dotted `event`, with optional `kind`, flexible typed `data`, `ctx`, `error` and `agg`, carry diagnostics independently of human `msg` formatting. Generic Python log records use a clearly defined fallback; do not infer field types from a message's words.
- The same recursive privacy/sanitization rules apply before persistence and at read/export/copy boundaries. No prompts, authorization headers, secret environment values, raw images, cloud payloads or exception locals. Sanitization must not be delegated independently to each Qt view or report.
- The legacy unbounded `queue.SimpleQueue` has been replaced in Phase 1 source by bounded nonblocking admission, explicit loss/rejection accounting and close sealing. Those sizes remain *proposed safety defaults*, not measured throughput or owner acceptance.
- A missing seal, missing required segment/sequence, loss marker, unsupported schema, malformed record, failed write or truncated evidence must be visible as incomplete/incompatible—not silently accepted as clean. A live active session is pending, not certified complete.
- The shared physical record/traceback policy is specified in G3 (1 MiB maximum accepted line, explicit rejection rather than silent truncation). Rotation, incomplete final lines, deduplication, cancellation and whole-session bounded queries are specified in G4. Avoid inventing alternate limits here.

## PR #104 benchmark preservation

The existing interactive Windows gallery benchmark is a **consumer of evidence**, not a logger. The migrated reader must preserve the plan's G5 event-by-event mapping: fresh positive `query_done`; real individual `scroll_intent` with source/direction; visible-first image #1 and target movement; GUI gaps **>=100 ms** with Grid mode and `mounted > 0` during *actual* wheel intervals; and clean/warning/failure exclusions. Exclude startup stalls from the browsing score, retain sample/aggregate distinctions, and disclose any change from old whole-second text timestamps to source-millisecond JSONL timestamps. Failed/incomplete recording cannot win a fastest/smoothest ranking.

## Lifecycle and acceptance boundaries

- Keep caller-side log emission nonblocking; listener-side I/O, rotation and aggregation must never stall Qt. Setup, disk-full and serialization failures must be fail-soft for generation, **without** claiming diagnostic completeness.
- New sessions use the single JSONL stream. Historical `.log` and gallery CSV files remain intact and are **not** silently migrated, removed or parsed as new-format sessions.
- The Logs workspace is a read-only consumer of local diagnostic evidence; it must not create recursive logging traffic for polling/filtering or change durable generation/output state.
- The owner's 2026-10-19 delivery disposition moves unfinished implementation to #106 and comprehensive G6 acceptance to #107. Ordinary final-head CI is still required for this delivery; strict lint/format gating and repeated native/fault/large-log evidence remain explicitly deferred. Code presence and green tests are not benchmark proof.

## References and provenance

- DTFlow source inspected: [current logging setup](../../src/dtflow/logging_setup.py), [gallery page](../../src/dtflow/ui/gallery_page.py), [Windows driver](../../tools/ui/windows_gallery_benchmark.py), [Grid report](../../tools/ui/grid_benchmark_report.py).
- [PR #105 plan](../plans/2026-10-19-structured-log-viewer.md) — single authoritative acceptance contract; [PR #104 benchmark protocol](../plans/2026-10-18-windows-gallery-benchmarks.md).
- [Qt model/view patterns](../guides/qt-model-view-patterns.md) — presentation and background-reader integration.

No third-party implementation code is copied here.

## Native reader and display boundary

`LogsWorkspace` uses the configured logging destination, one resident 500-row page and forward/back keyset queries on workers. Same-query Follow reconciles rows by `(session_id, seq)` instead of resetting the model; selected raw details and clipboard/export content are read on demand. The form is currently created at startup. Query/detail/copy have independent delivery tokens; hiding Logs cancels reads/delivery. One export runs at a time outside those tokens: it reports progress, Cancel and its outcome through the shared bottom overlay, so leaving Logs neither cancels it nor drops its result. A reader pass treats appends to the same files as stable (it read only up to its captured sizes); renames, replacement or shrinking still retry or report `snapshot_changed`.

The shared display projection v2 rounds finite numeric `*_ms` characteristic values to whole milliseconds in table/inspector chips. Timestamp precision, raw JSON/clipboard/exports and numeric filters/sort keys remain unchanged. `select` now rejects an oversized export candidate set with `consumer_memory_limit` using a conservative half-worker-budget admission estimate; it never returns a truncated success. This does not certify actual peak allocations: hydration, projections, pending work, and the combined 16 MiB worker / 32 MiB display limits still require the end-to-end measurement and enforcement in #106.

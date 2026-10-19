# Structured diagnostics architecture

**Status:** Phase 1 JSONL writer/typed-producer source is implemented on draft [PR #105](../plans/2026-10-19-structured-log-viewer.md) (2026-10-19); readers, exports, benchmark migration and native Logs UI remain pending. No syntax/import checks, tests or runtime/Windows acceptance were executed. The pre-Phase-1 facts below explain the retained foundation, not the current file format. The [implementation plan](../plans/2026-10-19-structured-log-viewer.md) owns G1–G6; this reference explains boundaries, not a second specification.

The [proposed compact, searchable event contract and legacy migration inventory](diagnostic-event-schema.md) and [record JSON Schema v1](../schemas/diagnostic-record-v1.schema.json) complement this ownership guide. The current design uses a strict small searchable envelope and flexible safe typed payloads: **new ordinary event/sub-event names do not require schema registration**, while benchmark/integrity fields remain selectively enforced. They are preparatory design proposals, not shipped behavior; the implementation plan's G1–G6 rules take precedence. The inventory is sourced from code rather than retained user log samples, and its unfinalized families must be reconciled in Phase 1.

## Pre-Phase-1 foundation: retained ownership

`src/dtflow/logging_setup.py` already owns `configure_logging`, a standard-library `QueueHandler`/`QueueListener`, sanitization, rotated per-session text files, session pruning, and listener-side gallery performance aggregation/CSV fanout. `AGENTS.md` already requires a shared logger. This is meaningful existing centralization; a competing logging engine is unnecessary.

What is *not* centralized yet is the **machine-readable meaning** of diagnostics. For example, `src/dtflow/ui/gallery_page.py` emits `gallery_perf phase=gui_gap elapsed_ms=... mode=... mounted=...` using formatting; `logging_setup.py` interprets such messages for aggregation/CSV, while `tools/ui/windows_gallery_benchmark.py` and `tools/ui/grid_benchmark_report.py` interpret text files through their own regex/readers. That string contract is fragile even though all producers share the primary file-writing pipeline.

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

Target design (writer/producers present in Phase 1 source; reader and consumers still pending, not shipped):

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

Phase 1 keeps configuration/session lifecycle and JSONL-only retention in `logging_setup.py`; `diagnostics.py` is the producer API (`event`, `measurement`, `source_summary`, `ATTEMPT`). `_diagnostic_schema.py` owns bounded capture, recursive sanitization, exception extraction and record/critical-field policy; `_diagnostic_writer.py` owns `QueueHandler`/`QueueListener` subclasses, admission accounting, listener aggregation, UTF-8/LF rotation and close seals. UI/services/backends import producer APIs only. Phase 2 must reuse schema/privacy/validation rather than restoring regexes or adding a second parser; no reader/export implementation is claimed here.

The queue budget includes conservative reservations for concurrent capture, not just queued records (1,024 pending records / 8 MiB; depth 16 / 4,096 nodes). A source observation is never retained as a mutable `LogRecord`, traceback or provider graph on the queue. The listener keeps at most 64 aggregate groups, each with at most 64 samples and a fixed small metric set; reaching group capacity flushes the oldest batch early without discarding observations. Known dimensions/attempt IDs are length-bounded for batching; extensions outside this policy remain individual records. All individual wheel receipts and >=100 ms GUI outliers remain observable. Original numeric metric keys such as `mounted`/`offset` are preserved rather than receiving invented suffixes; the documentary schema permits those source keys.

`session.started` declares instrumentation, capacities, `source-64-or-10s-bounded64-legacy1dp-v1` and numeric policy. Typed individual observations/source-summary data retain native finite values; listener batch inputs preserve the old one-decimal population before computing statistics. Phase 2's legacy report adapter must apply the former one-decimal rendering precision to individual/source-summary/statistic values where the old text/CSV did so, explicitly in report metadata, without changing raw JSONL or silently correcting report conventions. Source timestamp attribution is the separately versioned intentional precision change under G5.

`session.closed` counts distinguish `observed`, `admitted`, `capture_rejected`, `dropped`, `processed_records`, serialization rejections/failures and persisted `written_records` (including controls/seal). On a drained close, observed = admitted + capture_rejected + dropped, and processed_records = admitted; timeout is unsealed. `aggregate_source_samples` counts listener inputs, while `source_summary_samples` counts upstream summarized populations; neither is a persisted-record count and overlapping populations must not be added as unique measurements. A seal with any nonzero loss/failure is not clean evidence. Capture rejections coalesce in fixed reason/severity/measurement buckets: markers explicitly identify their last representative event and count, not a complete list of lost identities. The close deadline is two seconds; a timed-out or failed sink cannot certify closure and no fallback file is written.

Accepted exception traces retain all captured frames and causal/context chains without locals or source-code snippets, with recursive sanitization. Unsupported groups, deep/oversized traces or unsupported generic objects are rejected explicitly, never formatted through arbitrary repr or silently truncated. Read/copy/export privacy reuse and evidence-status enforcement are Phase 2 work; their runtime presence is not implied by this writer checkpoint.

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
- New sessions use the planned single JSONL stream. Historical `.log` and gallery CSV files remain intact and are **not** silently migrated, removed or parsed as new-format sessions.
- The Logs workspace is a read-only consumer of local diagnostic evidence; it must not create recursive logging traffic for polling/filtering or change durable generation/output state.
- The owner's plan specifies no test modifications or execution during implementation phases. The plan's **G6 gate** requires deferred reconciliation, strict exact-head CI, repeated native Windows real-log benchmarks and owner large-log/UX acceptance before Ready/Merge. Code presence is not benchmark proof.

## References and provenance

- DTFlow source inspected: [current logging setup](../../src/dtflow/logging_setup.py), [gallery page](../../src/dtflow/ui/gallery_page.py), [Windows driver](../../tools/ui/windows_gallery_benchmark.py), [Grid report](../../tools/ui/grid_benchmark_report.py).
- [PR #105 plan](../plans/2026-10-19-structured-log-viewer.md) — single authoritative acceptance contract; [PR #104 benchmark protocol](../plans/2026-10-18-windows-gallery-benchmarks.md).
- [Qt model/view patterns](../guides/qt-model-view-patterns.md) — presentation and background-reader integration.

No third-party implementation code is copied here.

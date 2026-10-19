# Diagnostic events v1 — schema proposal and legacy migration inventory

**Status: proposed, not implemented.** Prepared for [draft PR #105](https://github.com/djebaz/dtflow/pull/105) from repository source at `36202c6` (2026-10-19). This is a source-based migration reference, **not an analysis of retained user log files**: none were available in this review. The [PR plan and G1–G6](../plans/2026-10-19-structured-log-viewer.md) remain the binding project decisions; this document and the [machine-readable schema](../schemas/diagnostic-record-v1.schema.json) propose the concrete v1 record shapes. Writer/reader/event integration and all acceptance are pending.

## Contract layers

1. **Physical JSONL:** one BOM-free UTF-8 JSON object per complete LF-terminated line; no pretty-printed embedded newlines. Shared maximum physical line **1 MiB including LF**, and rollover target **1,000,000 bytes**; the writer must never truncate a line to fit the rollover target.
2. **Common envelope:** `schema_version=1`, `session_id`, persisted increasing `seq`, source UTC `timestamp` with milliseconds, source `monotonic_ns`, `level`, `component`, stable dotted `event`, `kind`, sanitized `message`, and typed object `data`. Optional `context`, `error`, `aggregation` have centrally defined shapes. The machine schema uses JSON Schema draft 2020-12 and rejects unrecognized *envelope* fields.
3. **Event contracts:** known event IDs have required typed `data` fields and a fixed `kind` in the machine schema. New event IDs can match the generic dotted-name envelope, but **must not become stable typed events without catalogue review**. `logging.message` is the ordinary-Python-logging fallback, with human details in sanitized `message` rather than guessed key-value fields.
4. **Semantic invariants, beyond JSON Schema:** the central writer/privacy policy must explicitly allowlist values; enforce finite JSON numbers, capture depth/size/node budgets and the 1 MiB *encoded line* limit; own `seq` assignment and the close seal; apply redaction at write/read/export boundaries. The shared reader enforces rotation identity, deduplication, sequence gaps, seal/counter reconciliation, source-time correctness and incomplete/corrupt status. A valid single JSON object is not proof that its session is complete. Do not add a JSON-Schema validator runtime dependency just because this reference exists: stdlib enforcement is acceptable.
5. **Privacy:** context IDs are opaque correlations, not raw paths or provider payloads. Backend development logs containing cloud metadata, LoRA configuration, server model specification, raw identities and arbitrary exception text require *explicit safe-key projection* before admission, not wholesale copying to `data`. The schema checks structure; it cannot determine whether a string includes a credential, prompt or private path.

### Illustrative JSONL records (not captured evidence)

```jsonl
{"schema_version":1,"session_id":"demo1","seq":1,"timestamp":"2026-10-19T10:00:00.000Z","monotonic_ns":10000000,"level":"INFO","component":"dtflow","event":"session.started","kind":"event","message":"Diagnostic session started","data":{"version":"unknown","source_commit":"unknown","build_state":"unknown","runtime":"Python","platform":"win32","dev":true,"instrumentation_version":"proposed-v1","schema_version":1,"queue_max_records":1024,"queue_max_bytes":8388608,"max_record_bytes":1048576,"aggregation_policy":"proposed-v1"}}
{"schema_version":1,"session_id":"demo1","seq":2,"timestamp":"2026-10-19T10:00:01.125Z","monotonic_ns":1125000000,"level":"DEBUG","component":"dtflow.ui.gallery","event":"gallery.gui_gap","kind":"measurement","message":"Gallery event loop delayed","data":{"elapsed_ms":117.4,"mode":"grid","mounted":100,"query_busy":false,"preparation_busy":false}}
{"schema_version":1,"session_id":"demo1","seq":3,"timestamp":"2026-10-19T10:00:02.000Z","monotonic_ns":2000000000,"level":"INFO","component":"dtflow.services.generation","event":"generation.attempt_succeeded","kind":"event","message":"Attempt completed","data":{"outputs":1},"context":{"attempt_id":"example-attempt"}}
```

These three lines illustrate syntax only. They do **not** form a sealed, certified complete session; no `session.closed` is present. They do not demonstrate real Windows performance or source clock behavior.

## Source review: existing ownership and consumers

The repository's `src/dtflow/logging_setup.py` configures `dtflow` logging with a `QueueHandler` backed by `queue.SimpleQueue`, one `QueueListener`, a rotating text handler and (development mode) a gallery CSV handler. It uses `_GalleryPerfAggregationHandler` to parse certain `gallery_perf phase=...` strings; the CSV handler parses formatted string tokens again. The separate benchmark consumers (`tools/ui/windows_gallery_benchmark.py`, `tools/ui/grid_benchmark_report.py`) use text regexes/CSV. **The writer is already centralized; producer and consumer field interpretation is not.**

Source inspection included `logging_setup.py`, `ui/gallery_page.py`, `services/gallery.py`, `services/generation.py`, `services/queue.py`, `services/session.py`, `services/assets.py`, `services/catalogue.py`, `services/image_recycle.py`, `services/queue_session.py`, `ui/generation_page.py`, `ui/main_window.py`, `ui/desktop.py`, `ui/gallery_preparation.py`, `ui/image_export_panel.py`, `persistence/gallery_snapshot.py`, `persistence/original_io.py`, `backends/drawthings/backend.py`, and the benchmark reader, with additional code-search discovery of `startup.py`, other persistence/backend/UI modules and standalone tooling. These are **reviewed families and representative sites**, not a mechanically certified exhaustive AST inventory. Phase 1 must reconcile all DTFlow-owned call sites and record any new families.

## Migration matrix: old observations to typed events

Event IDs in the right column are **proposed**; currently no code emits this schema. Fields shown are data field names, not claims that all observations exist in every current message. Entries marked "catalogue follow-up" should receive their own approved contract during producer migration instead of silently becoming generic free-form dictionaries.

| Current producer/source evidence | Proposed event or family | Required preservation / receiver |
| --- | --- | --- |
| `logging_setup.py`: `session start version=... commit=... backend=... dev=...` | `session.started` | Build/runtime identity and diagnostic mode; declare instrumentation, limits, aggregation policy, not guessed build cleanliness |
| `logging_setup.py` queue/handler error paths | `diagnostics.loss`, `diagnostics.record_rejected`, `session.closed` | New G2/G3 control-event proposals; out-of-band accounting, closure after drain, fail-soft errors |
| `gallery_page.py`: `phase=gui_gap elapsed_ms mode mounted query_busy preparation_busy` | `gallery.gui_gap` | Individual >=100 ms gaps, source timestamp; #104 counts Grid, mounted >0, during real wheel intervals only |
| `gallery_page.py`: `phase=scroll_intent direction source offset mounted` | `gallery.scroll_intent` | Preserve actual individual wheel receipt, source/direction, no double-counting summary |
| `gallery_page.py`: `phase=query_done elapsed_ms returned source error` | `gallery.query_done` | Positive returned with no error for readiness; source-time/session freshness |
| `services/gallery.py`: `phase=query_worker sql_wall_ms sql_cpu_ms build_wall_ms build_cpu_ms rows` | `gallery.query_worker` | Distinct SQL and projection wall/CPU populations, no fabricated sample timings |
| `gallery_page.py`: query_start/locate/read/facets/delivery/extension, page_jump, adjacent_*, render_page, grid layout/mount, preparation and resize timings | `gallery.<phase>` (catalogue follow-up) | Preserve original field names/units where appropriate; stable mapping per event, not dynamic string interpolation in final writer |
| `gallery_page.py`: `_PerfBatchStats` source summaries `samples items wall_total_ms wall_avg_ms wall_max_ms cpu_total_ms failures` | `gallery.source_summary` | Already aggregated observations; retain source sample population and window; **no invented p95** from mean/max |
| `logging_setup.py`: listener batches `thumbnail_worker`, `thumbnail_end_to_end`, `scroll_intent`, `prepare_slice`, `gui_gap` | `gallery.performance_aggregate` | 64 samples / 10 source seconds, grouped by direction/source/mode/success, nearest-rank p95 where actual samples exist; no duplicate individual outliers |
| `services/generation.py`: attempt prepared/succeeded/interrupted/failed | `generation.attempt_prepared`, `generation.attempt_succeeded`, `generation.attempt_interrupted`, `generation.attempt_failed` | Safe backend/request/model IDs, outputs, stage, failure category/outcome; durable attempt state remains authority |
| `services/queue.py`, `queue_session.py`: queue event evidence failure, queue item settled, command start | `queue.item_settled`; other queue lifecycle IDs (catalogue follow-up) | Preserve item/queue correlation, evidence failure, non-idempotent settlement distinctions; no inference of settlement from logs |
| `services/session.py`, `ui/generation_page.py`: inventory refresh/cache, provider forms, request validation | `inventory.*`, `generation.*` (catalogue follow-up) | Cache hit/miss and safe counts; no prompts or raw request objects |
| `persistence/gallery_snapshot.py`: publication, close, cache misses/failures | `gallery.snapshot.*` (catalogue follow-up) | Updated/unchanged/failure, item counts, elapsed/source context; never confuse snapshot with live mutation authority |
| `services/image_recycle.py`, `ui/image_export_panel.py`: cleanup preflight/outcome, export completion/failure | `images.recycle.*`, `images.export.*` (catalogue follow-up) | Aggregate counts and safe outcome stage; exclude private full paths and raw image identity |
| `ui/main_window.py`, `ui/desktop.py`, `startup.py`: startup, connection, credentials, shutdown | `app.*`, `connection.*`, `credentials.*` (catalogue follow-up) | Distinguish lifecycle from durable state; no secrets or credential value |
| `backends/drawthings/backend.py`: grpc metadata, LoRA configuration, model-spec dumps | `backend.*` (catalogue follow-up; privacy review required) | Safe, small allowlisted metadata only; do **not** copy arbitrary server metadata, LoRA payloads or full model JSON |
| Generic third-party/standard-library messages reaching the configured `dtflow` logger | `logging.message` | Retain level/component/sanitized message and optional exception without guessing event data |

### Additional discovered source families

The repository search also locates logging in `persistence/last_session.py`, `persistence/image_recycle.py`, `persistence/windows_original.py`, `backends/drawthings/codec.py`, `backends/drawthings/cloud_assets.py`, `ui/desktop_open.py`, `ui/image_recycle_panel.py`, `ui/gallery_preparation.py`, `services/person_selection.py` and `ui/app.py`. These require a full call-site check when migrating; their presence in a code-search result does **not** imply that every module emits a runtime record. Standalone `tools/images/*` logging is separately scoped and must not be silently forced into desktop session semantics.

## Semantic decisions before Phase 1

- **Ordinary vs typed:** first-class typed events are stable only after caller and consumer contracts are reviewed; ordinary unstructured messages go to `logging.message`, with no extracted machine data. Keep existing levels and -x verbosity boundaries unless the PR explicitly changes them.
- **Aggregation ownership:** `gallery.source_summary` represents summaries already computed in `_PerfBatchStats`. `gallery.performance_aggregate` represents listener aggregation over retained *source measurements* and requires a real sample-population/metrics object. Both have explicit source window bounds. Do not merge, reaggregate or compute a global p95 from summaries.
- **Named event completeness:** the current file schema narrows a small critical subset of events; other dotted IDs are structurally accepted but are **not** implicitly schema-reviewed. Expansion requires a new typed contract and documented migration mapping before a producer starts relying on it.
- **Field types:** numeric milliseconds and bytes are numbers, counts/offsets are nonnegative integers, flags are booleans, absent optional context is omitted. Current string literals such as `error=none` map to an explicit no-error value in v1; never mistake text `"False"` for JSON `false`.
- **Event count vs sample count:** `seq` identifies persisted records, not input observations. `session.closed` counts reconcile writer losses separately from intentional aggregation; a successful close record exists only after drain and flush. Missing/unsupported/lost events invalidate clean benchmark evidence even if the data file is well-formed.
- **Timestamps:** source `timestamp` and `monotonic_ns` must be captured before queueing; aggregates give first/last source windows. Do not attribute scroll stalls to listener write time or derive source chronology from rotated filenames.
- **Privacy and volume:** avoid unbounded arbitrary `repr`, protobuf, backend dict, exception locals, prompt, cloud reference, image bytes or filesystem paths. Separate allowed generic log message text from safe typed payload; subject both to existing and enhanced redaction.
- **Schema versioning:** increment `schema_version` for breaking envelope/type/required-field changes; additive named event families still need a reviewed catalogue contract. Readers reject unsupported versions explicitly. Retained pre-JSONL text/CSV remains untouched and unsupported by new readers.

## Contract versus validation limits

[JSON Schema v1](../schemas/diagnostic-record-v1.schema.json) validates the shape of a **single decoded record** and typed data for the initial critical event IDs; it is **not** a validator for bytes-on-disk, secret safety, session completeness or PR #104 clean-trial semantics. The JSON Schema file is design documentation, not a new runtime dependency. The implementation must enforce G1–G6 and the event-specific privacy allowlists independently.

## Implementation handoff

1. Phase 1: reconcile all logging sites against this inventory, finalize the first-class event catalogue (including remaining gallery phases), and keep typed key definitions in one module/schema authority; preserve current measurement cadence before replacing string producers.
2. Phase 2: use the shared validator/reader for all viewers, exports and #104 benchmark tools; certify evidence completeness only after seal/rotation/loss checks. Do not use a second set of regex field maps.
3. Phase 3: render synthetic instances of the *same* schema in the native Logs table; do not invent one-off UI formats or bypass the shared redaction boundary.
4. Deferred owner/CI: reconcile affected tests, run exact-head checks and native Windows benchmark replay/large-log UX under G6 before Ready/Merge. No tests or validation were run to produce this audit.

## Source and review links

- [PR #105 implementation plan](../plans/2026-10-19-structured-log-viewer.md), especially G1–G6 and G5's exact benchmark parity table.
- [Existing centralized logger](../../src/dtflow/logging_setup.py), [gallery producer](../../src/dtflow/ui/gallery_page.py), [gallery service](../../src/dtflow/services/gallery.py), [generation service](../../src/dtflow/services/generation.py), [backend producer](../../src/dtflow/backends/drawthings/backend.py).
- [Windows benchmark consumer](../../tools/ui/windows_gallery_benchmark.py), [Grid benchmark report](../../tools/ui/grid_benchmark_report.py).
- [Structured diagnostics ownership](structured-diagnostics.md) and [Qt model/view guide](../guides/qt-model-view-patterns.md).

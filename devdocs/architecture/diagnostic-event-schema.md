# Diagnostic events v1 — schema proposal and legacy migration inventory

**Status: proposed, not implemented.** Prepared for [draft PR #105](https://github.com/djebaz/dtflow/pull/105) from repository source at `36202c6` (2026-10-19). This is a source-based migration reference, **not an analysis of retained user log files**: none were available in this review. The [PR plan and G1–G6](../plans/2026-10-19-structured-log-viewer.md) remain the binding project decisions; this document and the [machine-readable schema](../schemas/diagnostic-record-v1.schema.json) propose the concrete v1 record shapes. Writer/reader/event integration and all acceptance are pending.

## Compact and searchable record contract

**Proposal, not shipped:** aim for a JSONL stream close in size to existing text logs at equal verbosity and retained measurements. No representative session-byte comparison has yet been measured. This compact v1 supersedes the earlier 11-required-field envelope and 16 closed typed-event contracts.

- **Physical:** UTF-8 JSON object per LF-terminated line; no BOM, pretty-printing or partial records. Maximum 1 MiB including LF; retain the 1,000,000-byte rollover target. Do not truncate tracebacks or records.
- **Required on every record:** `v=1`, persistent increasing `seq`, source UTC millisecond `ts`, `level`, `component`, and hierarchical dotted `event`. These are stable search keys for time, severity, subsystem, event/sub-event and identity.
- **Optional:** `kind` (`event` by default; required for critical measurements and aggregates), readable `msg`, typed JSON-native `data`, source `mono_ns` for timing-critical measurements, safe correlation `ctx`, sanitized `error` and source-window statistics `agg`. A record carries at least `msg` or `data`. Do not shorten useful metric names such as `elapsed_ms` or `mounted`.
- **Session identity without repetition:** `session.started` contains `data.session_id`, build/runtime identity, instrumentation and capture policy. The reader binds subsequent records to a verified file/segment identity plus that startup identity; logical identity remains `(session_id, seq)`. If the startup record or required rotated segments have been pruned, mark evidence incomplete instead of reconstructing or certifying a clean run.
- **Flexible events:** arbitrary dotted event names and safe typed `data` additions are valid without a new registration or schema modification. The catalogue documents important semantics but is not an exhaustive whitelist. Only benchmark-critical (`gallery.gui_gap`, `gallery.scroll_intent`, `gallery.query_done`) and evidence-integrity (`session.started`, `session.closed`, `diagnostics.loss`, `diagnostics.record_rejected`) events have required consumer fields; additional safe fields remain allowed even there.
- **Search:** filter on top-level `ts`, `level`, `component`, `event` (exact or prefix), and `seq`; advanced filters compare typed `data.*` paths, e.g. `data.mode = grid` or `data.elapsed_ms >= 100`. Search sanitized `msg` and permitted structured values, without extracting fields from formatted messages. Whole-retained-session filtering/sorting/counting stays in the shared Qt-free, cancellable, bounded-memory reader.
- **Privacy and safety:** permissive event extensions do **not** authorize arbitrary request objects, paths, prompts, exception locals, cloud metadata, tokens or protobuf contents. One shared bounded capture/redaction implementation must allowlist keys and values, serialize only finite JSON-native data, and account for losses. JSON Schema validates the decoded line shape, not sanitization, line byte size, rotation, timestamp truth, or full-session completeness.
- **Volume:** preserve normal vs `-x` verbosity, batching and thresholded GUI-gap records. Avoid duplicating session metadata and redundant raw/aggregate samples. Do not drop individual events needed for PR #104 benchmark attribution or hide writer losses. Measure old-versus-new total bytes and retained evidence on the same workload before claiming storage parity; revisit rotation retention only with evidence.

### Illustrative records (not observed log files)

```jsonl
{"v":1,"seq":1,"ts":"2026-10-19T10:00:00.000Z","level":"INFO","component":"app","event":"session.started","data":{"session_id":"demo1","version":"unknown","source_commit":"unknown","build_state":"unknown","runtime":"Python","platform":"win32","dev":true,"instrumentation_version":"proposed-v1","schema_version":1,"queue_max_records":1024,"queue_max_bytes":8388608,"max_record_bytes":1048576,"aggregation_policy":"proposed-v1"}}
{"v":1,"seq":2,"ts":"2026-10-19T10:00:01.125Z","level":"DEBUG","component":"gallery","event":"gallery.gui_gap","kind":"measurement","mono_ns":1125000000,"data":{"elapsed_ms":117.4,"mode":"grid","mounted":100,"query_busy":false,"preparation_busy":false}}
{"v":1,"seq":3,"ts":"2026-10-19T10:00:02.000Z","level":"INFO","component":"generation","event":"generation.attempt_succeeded","data":{"outputs":1},"ctx":{"attempt_id":"example-attempt"}}
```

These illustrative lines deliberately do not include `session.closed`; they are **not** a verified complete benchmark session.
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

- **Ordinary vs typed:** consumer-critical event fields require review; ordinary new dotted events and sub-events require no schema registration, and unstructured Python messages go to `logging.message`, with no extracted machine data. Keep existing levels and -x verbosity boundaries unless the PR explicitly changes them.
- **Aggregation ownership:** `gallery.source_summary` represents summaries already computed in `_PerfBatchStats`. `gallery.performance_aggregate` represents listener aggregation over retained *source measurements* and requires a real sample-population/metrics object. Both have explicit source window bounds. Do not merge, reaggregate or compute a global p95 from summaries.
- **Named event completeness:** the schema requires only critical consumer fields; other dotted event names and safe `data` additions are inherently extensible. Add documentation when an automated consumer depends on a field, without turning the catalogue into a mandatory event registry.
- **Field types:** numeric milliseconds and bytes are numbers, counts/offsets are nonnegative integers, flags are booleans, absent optional context is omitted. Current string literals such as `error=none` map to an explicit no-error value in v1; never mistake text `"False"` for JSON `false`.
- **Event count vs sample count:** `seq` identifies persisted records, not input observations. `session.closed` counts reconcile writer losses separately from intentional aggregation; a successful close record exists only after drain and flush. Missing/unsupported/lost events invalidate clean benchmark evidence even if the data file is well-formed.
- **Timestamps:** source `ts` and (when relevant) `mono_ns` must be captured before queueing; aggregates give first/last source windows. Do not attribute scroll stalls to listener write time or derive source chronology from rotated filenames.
- **Privacy and volume:** avoid unbounded arbitrary `repr`, protobuf, backend dict, exception locals, prompt, cloud reference, image bytes or filesystem paths. Separate allowed generic log message text from safe typed payload; subject both to existing and enhanced redaction.
- **Schema versioning:** increment record `v` for breaking envelope/type/required-field changes; additional safe ordinary event families do not require a new schema or catalogue entry. Readers reject unsupported versions explicitly. Retained pre-JSONL text/CSV remains untouched and unsupported by new readers.

## Contract versus validation limits

[JSON Schema v1](../schemas/diagnostic-record-v1.schema.json) validates the shape of a **single decoded record** and typed data for the initial critical event IDs; it is **not** a validator for bytes-on-disk, secret safety, session completeness or PR #104 clean-trial semantics. The JSON Schema file is design documentation, not a new runtime dependency. The implementation must enforce G1–G6 and the event-specific privacy allowlists independently.

## Implementation handoff

1. Phase 1: reconcile all logging sites against this inventory, reconcile benchmark-critical mappings and inventory representative logging families without registering every ordinary event, and keep typed key definitions in one module/schema authority; preserve current measurement cadence before replacing string producers.
2. Phase 2: use the shared validator/reader for all viewers, exports and #104 benchmark tools; certify evidence completeness only after seal/rotation/loss checks. Do not use a second set of regex field maps.
3. Phase 3: render synthetic instances of the *same* schema in the native Logs table; do not invent one-off UI formats or bypass the shared redaction boundary.
4. Deferred owner/CI: reconcile affected tests, run exact-head checks and native Windows benchmark replay/large-log UX under G6 before Ready/Merge. No tests or validation were run to produce this audit.

## Source and review links

- [PR #105 implementation plan](../plans/2026-10-19-structured-log-viewer.md), especially G1–G6 and G5's exact benchmark parity table.
- [Existing centralized logger](../../src/dtflow/logging_setup.py), [gallery producer](../../src/dtflow/ui/gallery_page.py), [gallery service](../../src/dtflow/services/gallery.py), [generation service](../../src/dtflow/services/generation.py), [backend producer](../../src/dtflow/backends/drawthings/backend.py).
- [Windows benchmark consumer](../../tools/ui/windows_gallery_benchmark.py), [Grid benchmark report](../../tools/ui/grid_benchmark_report.py).
- [Structured diagnostics ownership](structured-diagnostics.md) and [Qt model/view guide](../guides/qt-model-view-patterns.md).

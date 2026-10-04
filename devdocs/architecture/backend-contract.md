# Backend contract and async execution

**Status:** Implementation baseline for Phase 2; APIs below are specified, not shipped. Refine this baseline from the first thin working slice rather than adding speculative interfaces.  
**Scope:** Phase 2A core/fake, 2B local gRPC and 2C direct DT+; [Phase 2 plan](../plans/phase-2-backends.md), [cloud protocol](cloud-protocol.md), [LoRA scope](../plans/lora-assets.md).  
**Decision basis:** drawthings-py 0.4.0 is async-native; preserve one service path for CLI and Qt.

## Async ownership and lifecycle

Backends and application services are async-native. The CLI creates one top-level asyncio event loop and owns its lifetime. It does not create a loop per request. Backends, channels, HTTP clients, refresh locks and tasks remain on their owning loop; adapters never call asyncio.run or create loops.

Qt later hosts the same services in one dedicated worker thread with its own long-lived asyncio loop. Submit commands thread-safely; return immutable events/results through Qt signals. Widgets never access channels, futures or provider objects. Network work stays off the GUI thread. Shutdown stops admission, cancels/waits for owned tasks, closes clients, then joins the worker; do not destroy a live loop.

No synchronous backend facade or qasync dependency is required initially. Blocking codecs/file operations run in bounded workers where necessary; task cancellation cannot kill a Python thread, so guard late results and preserve submission evidence. Pure core imports need neither Qt nor provider libraries.

## Specified operations

Use a typed Protocol and immutable dataclasses/enums in core. Method names below are the implementation contract, not existing CLI commands.

| Operation | Signature intent | Semantics |
| --- | --- | --- |
| Open | async open() -> None | Initialize owned resources within connect deadline; lazy connection is permitted but never lazy generation |
| Connectivity | async check_connection() -> ConnectionStatus | Bounded non-generative transport/auth/inventory checks; does not call generation authentication or consume generation quota |
| Inventory | async get_inventory(refresh: bool = False) -> InventorySnapshot | Explicit refresh/cache distinction; retain backend/server identity and observation time |
| Generation | async generate(request: ResolvedGenerationRequest, *, on_event: EventSink) -> GenerationResult | Return only after successful terminal RPC and valid required final artifacts; emit real events/previews separately |
| Close | async aclose() -> None | Idempotent bounded cleanup; owning service coordinates cancellation before close |

An EventSink accepts immutable events synchronously and returns promptly; it must not block or perform durable/network I/O. Services bridge to bounded queues and own persistence. Coalesce progress/preview updates under backpressure, never lose the submission-boundary event. Sink failure before submission prevents submission; afterward fail conservatively and close transport without reporting false success. No provider callback runs directly against Qt widgets.

One backend instance supports concurrent generate calls up to its declared client support; do not share mutable request builders. Scheduler policy is separate: the tested app/Bridge lane is one; configured cloud slots are a product choice, not entitlement. HTTP token refresh is shared within the configured credential scope. Channels may be per-attempt if required by the selected client; services see the same contract.

## Connection and inventory types

ConnectionStatus contains backend_id, endpoint identity without credentials, checked_at_utc, and separate transport, authentication and inventory states (ok / failed / unknown / not_applicable), plus sanitized diagnostics. A reachable socket is not authenticated health; empty inventory is not failure. Echo with missing shared secret must not be reported as authenticated inventory.

InventorySnapshot contains backend_id, server identity when exposed, observed_at_utc, refreshed flag, entries and diagnostics. Entries retain exact identifier, kind (model / lora / control / other / unknown), display name, architecture string or unknown, availability evidence and metadata source/revision. Preserve unknown future architecture strings. Raw file entries need no fabricated family/kind. No public cloud asset list is implied; see the LoRA plan.

Capabilities describe supported operations, supported sampler IDs, client/output limits and model-combination evidence separately. A transport accepting a field does not establish model compatibility. Phase 2 defines preflight types and metadata; bounded LoRA application is Phase 3A and cloud upload/reuse Phase 3B.

## Immutable resolved request

ResolvedGenerationRequest fields:

| Field | Type and initial rule |
| --- | --- |
| request_id | Stable local identifier for correlation; not a remote recovery handle |
| operation | txt2img initially |
| prompt, negative_prompt | Explicit strings; empty negative prompt permitted; model-specific prompt validation |
| model_id | Exact nonempty provider identifier; no filename guessing |
| seed | Explicit integer 0..4294967295 for this Draw Things scope; bool rejected; no random sentinel in a resolved request |
| width_px, height_px | Explicit effective pixel dimensions; initial 128..2048, multiples of 64 and at most 4,194,304 pixels; narrower backend/model limits apply |
| steps | Explicit integer 1..150 initially; model-specific restrictions apply |
| guidance | Finite number 0..50 initially; preserve effective float32 value in wire evidence |
| sampler_id | Core string enum: euler_a or dpmpp_2m_karras initially; advertised per-backend subset only |
| batch_size, batch_count | Explicit positive integers; product bounded by output limit; initially advertise 1/1 until batch seed/count behavior is fixture-verified |
| loras | Immutable tuple of exact identifier and finite explicit weight; empty initially; metadata/preflight contract now, supported generation combinations in 3A |
| resolution_evidence | Original input/config provenance and explicitly resolved effective values; credential-free |

Bounds above are DTFlow initial scope choices, not provider-wide limits or compatibility claims. Services resolve defaults/presets and any intentional dimension normalization before constructing this request, retaining entered and effective values; adapters reject unresolved/unsupported input rather than silently rounding or enabling extra fields. Advanced inputs/extensions are rejected until their contract is added.

Sampler mapping belongs in the provider integration: euler_a maps to EulerA (wire enum 1), dpmpp_2m_karras to DPMPP2MKarras (wire enum 0) in 0.4.0 bindings. Core does not import those enums. Fixture tests decode actual FlatBuffer configuration to assert sampler, seed, pixel-to-64-unit dimensions, batch values and all effective parameters. Default selection belongs to the resolver/model profile, not the adapter.

## Artifacts, results and resource limits

ImageArtifact contains artifact_id, designation (final / preview / partial), media_type (image/png initially), encoded bytes, decoded width/height and credential-free metadata. No Pillow/NumPy/protobuf objects cross the boundary. Decode provider tensors, validate dimensions and encode PNG inside the integration; services own filenames, durable writes and provenance.

GenerationResult contains request_id, backend/server identity, immutable final artifacts, effective wire configuration including seed, completion evidence (successful terminal status plus validated output) and warnings. Previews are events, never required final artifacts. Partial evidence on failure belongs to the typed failure/event path. Phase 3 reports application success only after durable artifact/metadata saving.

Initial DTFlow safety budgets: at most four final artifacts, 32 MiB encoded per artifact and 64 MiB aggregate final bytes per request; 64 MiB maximum individual transport payload and 64 MiB decoded tensor buffer; one retained latest preview per attempt, at most 2 MiB encoded. Account for intermediate allocations and use a shared 256 MiB admission budget for decoding/encoding buffers. These are configurable internal limits subject to target-machine validation, not a peak-RSS guarantee. Do not submit an obviously oversized request; enforce payload/dimension/count limits during transport and before decoding. A limit breach after possible submission closes transport and retains remote uncertainty. Do not silently truncate output or label it complete. Chunking remains disabled until bounded assembly is implemented and tested.

## Events, errors and submission evidence

BackendEvent contains request_id, monotonically ordered sequence, kind, timestamp and typed payload. Kinds include preparing, submission_possible, progress, preview, artifact_received and terminal. Progress carries signpost/stage and counters only when supplied; unknown percentage stays absent. Receiving an artifact is not request completion.

SubmissionStage is a separate evidence field:

- NOT_SUBMITTED: generation RPC has definitely not crossed the send boundary.
- POSSIBLY_SUBMITTED: marked conservatively before calling GenerateImage; remote execution may occur.
- TERMINAL_OBSERVED: terminal RPC status observed; final decoding/application may still fail.

No inspected generation job/status API resolves ambiguity. TERMINAL_OBSERVED alone is not confirmed success or a universal guarantee compute stopped. Carry terminal status when known and remote outcome separately (not_started / unknown / succeeded / failed / cancelled_confirmed); only promote an outcome when supported by evidence. Phase 3's durable potentially-executing mark precedes adapter dispatch; transport events cannot replace that write.

BackendFailure contains category, stable code, sanitized message, request_id, submission_stage, remote_outcome, known terminal status, timeout phase if relevant, diagnostics and optional partial artifact evidence. Categories: validation, credentials, authentication, connectivity, timeout, remote_rejection, protocol, decoding, resource_limit and cancellation. Preserve provider status behind a sanitized diagnostic field, not provider exception types. Unknown refusal stays generic remote_rejection; no invented quota taxonomy. Never auto-retry generation from category alone.

Cancellation evidence tracks requested, local_stopped, remote_confirmed and outcome_unknown explicitly. Cancelling an asyncio task closes its owned stream in a finally block, with bounded cleanup, then preserves CancelledError/control cancellation for the caller and publishes immutable outcome evidence through the service-owned attempt context. Do not swallow cancellation or return a success result. Shared channel cleanup must not cancel unrelated attempts. No separate server-cancel operation is promised.

## Timeout policy

All defaults below are DTFlow choices, not cloud/provider guarantees. Use monotonic clocks and injectable policy/time in tests.

| Limit | Default | Boundary |
| --- | --- | --- |
| Transport connect including TLS | 10 s | Per connection |
| Echo / inventory RPC | 10 s | Whole unary call after connection |
| Cloud token or generation-auth HTTP | 30 s total, 10 s connect | Per HTTP operation; see cloud spec |
| Stream idle | Disabled | Optional positive override: time since last received protocol message, not last UI update |
| Generation total | 600 s | Adapter call including prepare/auth/connect/stream/decode; excludes waiting in application queue |
| Cancellation/close cleanup | 5 s | Bounded stream/client cleanup |

Disabled idle timeout avoids interpreting silent server queueing as failure. Total deadline is finite and configurable for supported long workloads; expiry does not prove remote work stopped. Pass the remaining total budget into subordinate operations and gRPC deadlines rather than resetting it at each step. Distinguish connect, auth, inventory, stream_idle, generation_total and cleanup timeouts in diagnostics. Cleanup expiry preserves uncertainty; no replacement submission.

## Replaceable dependency boundary and credentials

Confine drawthings-py builders/serialization, tensor codecs, generated bindings, grpclib transport and private-access workarounds to one replaceable Draw Things integration module/package beneath backends. The local/cloud integrations may share this module; services/core/UI never subclass GrpcService or import its types. Keep the public cloud HTTP integration similarly private.

The probe's subclass and __getattr__ stub wrapper are diagnostic evidence, not an architecture to spread. Keep any required private access version-pinned, narrow, documented and covered by actual-adapter fixtures. Prefer public serializer/codec functions. In 0.4.0 build_grpc_message uses seed or -1; build/override the configuration using the exact resolved seed via the serializer inside this boundary, then decode/assert wire seed zero. Do not mutate installed dependency files or patch global behavior.

Keep an integration workaround ledger (version, source, reason, fixture, removal condition) with implementation. Dependency replacement/licensing evaluation must not alter the application contract. No licensing outcome is implied by module isolation.

Phase 2 implements the credential-provider interface, environment provider and injected fake saved provider only. Real Windows Credential Manager entry/storage arrives in Phase 4. Preserve environment-presence precedence, including empty/invalid failure with no fallback. The Phase 2 default saved provider is unavailable; tests inject it to cover precedence. Credentials/tokens never enter connection records, requests, artifacts or logs.

## Evidence and implementation gates

On 4 October 2026, an isolated uv sync --locked of PR d8e28fa's pyproject/lock/package files installed drawthings-py 0.4.0, betterproto 2.0.0b7 and grpclib 0.4.9 on Linux CPython 3.13.15. Import/source inspection confirmed ImageGenerationServiceBase with Echo and server-streaming GenerateImage, grpclib.server.Server.start(ssl=...), and RecvRequest metadata/deadline events. This is installed-interface evidence, not server execution, Windows packaging or generation testing.

Use those bindings for the Phase 2B fixture server; no second generated schema or gRPC stack is required. Capture authorization/deadline metadata through grpclib events when the high-level service base does not expose the stream. Test the contract through actual adapters, including terminal-error preservation and no final output. Revisit this evidence on dependency updates.

Sources: [0.4.0 builder](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/request_builder.py), [client](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/grpc/grpc_service.py), [configuration serializer](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/configs/gen_config.py), [generated bindings](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/generated/dt_grpc/image_service/__init__.py), [research](../research/dtflow-ecosystem-research-report.md).

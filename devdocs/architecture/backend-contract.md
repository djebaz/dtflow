# Backend contract and async execution

**Status:** Core contracts, fake backend and the minimal durable service/CLI slice are implemented in Phase 2A. The Phase 2B local gRPC adapter is implemented against scripted fixtures ([integration notes](drawthings-integration.md)); cloud behavior below remains specified for 2C, and fake evidence does not verify real transport.  
**Scope:** Phase 2A core/fake, 2B local gRPC and 2C direct DT+; [Phase 2 plan](../plans/phase-2-backends.md), [cloud protocol](cloud-protocol.md), [LoRA scope](../plans/lora-assets.md).  
**Decision basis:** drawthings-py 0.4.0 is async-native; preserve one service path for CLI and Qt.

## Async ownership and lifecycle

Backends and application services are async-native. The CLI creates one top-level asyncio event loop and owns its lifetime. It does not create a loop per request. Backends, channels, HTTP clients, refresh locks and tasks remain on their owning loop; adapters never call asyncio.run or create loops.

Qt later hosts the same services in one dedicated worker thread with its own long-lived asyncio loop. Submit commands thread-safely; return immutable events/results through Qt signals. Widgets never access channels, futures or provider objects. Network work stays off the GUI thread. Shutdown stops admission, cancels/waits for owned tasks, closes clients, then joins the worker; do not destroy a live loop.

Before Phase 4, run a small throwaway Qt/asyncio worker-thread spike covering signal delivery, interruption and shutdown while the GUI remains responsive. Record the result and revise this choice if it fails; the spike does not deliver the Qt shell or relax the pre-Qt CLI gates.

No synchronous backend facade or qasync dependency is required initially. Blocking codecs/file operations run in bounded workers where necessary; task cancellation cannot kill a Python thread, so guard late results and preserve submission evidence. Pure core imports need neither Qt nor provider libraries.

## Durable state and layer enforcement

Use standard-library sqlite3 for canonical execution state. Start with requests/attempts in the Phase 2A working slice; extend the same store with queues, reservations and asset receipts in Phase 3B. Use a local application state directory, WAL mode, synchronous=FULL, foreign_keys=ON per connection, a bounded busy timeout and short transactions. Keep database work off the event loop through a single owned worker; never hold a transaction across network or artifact I/O. SQLite permits one writer at a time; concurrent generation is separate from concurrent database writes.

Require a patched bundled SQLite runtime: 3.51.3 or newer, or an explicitly documented official backport. The official [WAL documentation](https://www.sqlite.org/wal.html) records the multi-connection WAL-reset fix in 3.51.3, 3.50.7 and 3.44.6. Inspect sqlite3.sqlite_version on each target; the inspected Linux runtime is 3.53.1, which is not Windows evidence. Do not silently ship an affected runtime.

WAL state stays on a local filesystem. Use SQLite's backup API for a consistent backup, rather than copying only the main database while WAL is active. FULL is a DTFlow durability choice; see [synchronous semantics](https://www.sqlite.org/pragma.html#pragma_synchronous). Verify crash behavior on the target filesystem. Database commits and image-file replacement are not one atomic transaction: save artifacts durably, then commit references/success; reconcile interrupted writes without resubmitting unknown attempts. Schema versions start in the first slice. No JSON execution store or ORM is needed.

In Phase 2A, add a small pytest import-boundary check using standard-library AST inspection and headless import coverage: core imports no Qt, backend implementation or provider types; services depend on core contracts, not concrete provider classes; provider imports stay inside the integration boundary. Cover absolute/relative imports and local imports. This enforces the written rules without a new lint dependency.

## TLS channel ownership

Installed drawthings-py 0.4.0 GrpcService.connect uses a module-level SSLContext loaded with its bundled CA; that context disables hostname checking and is not a constructor-injected trust path. Do not use or patch that process-global context for DTFlow production or fixture connections.

The replaceable integration owns channel creation with an explicit per-instance SSLContext, using a narrow version-pinned connection override where the dependency requires it. The local Draw Things endpoint defaults to TlsPolicy.drawthings_local(): trust only the bundled Draw Things Root CA, retain certificate-chain verification, disable hostname checking as in the working probe, and clear VERIFY_X509_STRICT for the bundled CA's missing key-usage extension on Python 3.13. This explicit compatibility policy does not fall back from failed system/custom trust. System/custom-CA policies retain certificate and hostname checks. Direct DT+ uses the probe's ssl.create_default_context() plus h2, explicitly selecting system trust rather than inheriting the local policy. No additional cloud certificate pinning or TLS requirements are introduced beyond that probe baseline. Fixtures inject their own CA/context into the same channel-construction path; existing strict-policy fixture checks remain applicable. Preserve discovery and bounded cleanup; no provider/channel types cross into services.

## Specified operations

Use typing.Protocol, frozen slotted standard-library dataclasses and enums in core. Validate inputs explicitly at configuration/import boundaries and preserve immutable nested values. Do not add pydantic-core or another compiled validation dependency for these types; any new native dependency needs evidence on the target Pentium E6500 before adoption. Method names below are the implementation contract, not existing CLI commands.

| Operation | Signature intent | Semantics |
| --- | --- | --- |
| Open | async open() -> None | Initialize owned resources within connect deadline; lazy connection is permitted but never lazy generation |
| Connectivity | async check_connection() -> ConnectionStatus | Bounded non-generative transport/auth/inventory checks; does not call generation authentication or consume generation quota |
| Inventory | async get_inventory(refresh: bool = False) -> InventorySnapshot | Explicit refresh/cache distinction; retain backend/server identity and observation time |
| Generation | async generate(request: ResolvedGenerationRequest, *, on_event: EventSink) -> GenerationResult | Return only after successful terminal RPC and valid required final artifacts; emit real events/previews separately |
| Close | async aclose() -> None | Idempotent bounded cleanup; owning service coordinates cancellation before close |

An EventSink accepts immutable events synchronously and returns promptly; it must not block or perform durable/network I/O. Services bridge to bounded queues and own persistence. Coalesce progress/preview updates under backpressure, never lose the submission-boundary event. Sink failure before submission prevents submission; after possible submission but before validated completion, fail conservatively and close transport without reporting false success. Once terminal OK and required valid outputs are established, final notification failure retains the result with a sanitized warning. No provider callback runs directly against Qt widgets.

One backend instance supports concurrent generate calls up to its declared client support; do not share mutable request builders. Scheduler policy is separate: the tested app/Bridge lane is one; configured cloud slots are a product choice, not entitlement. HTTP token refresh is shared within the configured credential scope. Channels may be per-attempt if required by the selected client; services see the same contract.

## Connection and inventory types

ConnectionStatus contains backend_id, endpoint identity without credentials, checked_at_utc, and separate transport, authentication and inventory states (ok / failed / unknown / not_applicable), plus sanitized diagnostics. A reachable socket is not authenticated health; empty inventory is not failure. Echo with missing shared secret must not be reported as authenticated inventory.

InventorySnapshot contains backend_id, server identity when exposed, observed_at_utc, refreshed flag, entries and diagnostics. Entries retain exact identifier, kind (model / lora / control / other / unknown), display name, architecture string or unknown, availability evidence and metadata source/revision. Preserve unknown future architecture strings. Raw file entries need no fabricated family/kind. No public cloud asset list is implied; see the LoRA plan.

Capabilities describe supported operations, supported sampler IDs, client/output limits and model-combination evidence separately. A transport accepting a field does not establish model compatibility. Phase 2 defines preflight types and metadata; bounded LoRA selection is implemented in Phase 3A (offline-verified wire mapping, application unconfirmed) and cloud upload/reuse is Phase 3B.

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
| batch_size, batch_count | Explicit positive integers; product bounded by output limit; advertise 1/1 until batch seed/count behavior is verified; the #280 [findings](../research/2026-11-08-native-batching-findings.md) found no safe native combination, so native batching stays disabled |
| loras | Immutable tuple of exact identifier and finite explicit weight; empty initially; metadata/preflight contract now, supported generation combinations in 3A |
| resolution_evidence | Original input/config provenance and explicitly resolved effective values; credential-free |

Bounds above are DTFlow initial scope choices, not provider-wide limits or compatibility claims. Services resolve defaults/presets and any intentional dimension normalization before constructing this request, retaining entered and effective values; adapters reject unresolved/unsupported input rather than silently rounding or enabling extra fields. Advanced inputs/extensions are rejected until their contract is added.

Sampler mapping belongs in the provider integration: euler_a maps to EulerA (wire enum 1), dpmpp_2m_karras to DPMPP2MKarras (wire enum 0) in 0.4.0 bindings. Core does not import those enums. Fixture tests decode actual FlatBuffer configuration to assert sampler, seed, pixel-to-64-unit dimensions, batch values and all effective parameters. Default selection belongs to the resolver/model profile, not the adapter.

## Artifacts, results and resource limits

ImageArtifact contains artifact_id, designation (final / preview / partial), media_type (image/png initially), encoded bytes, decoded width/height and credential-free metadata. No Pillow/NumPy/protobuf objects cross the boundary. Decode provider tensors, validate dimensions and encode PNG inside the integration; services own filenames, durable writes and provenance. Offline derived-image export is a deliberate exception: services/image_export.py and image_quality.py may use Pillow internally for JPEG copies and advisory pixel analysis. No Pillow objects cross the backend/core boundary; other services remain under the original dependency guard.

GenerationResult contains request_id, backend/server identity, immutable final artifacts, effective wire configuration including seed, completion evidence (successful terminal status plus validated output), warnings and optional client-observed backend elapsed seconds. The elapsed value is measured monotonically from the submission-possible boundary to the terminal event; it may include provider-side queueing or serialization and is not labelled inference/generation time. Previews are events, never required final artifacts. Partial evidence on failure belongs to the typed failure/event path. Phase 3 reports application success only after durable artifact/metadata saving.

Validated backend completion survives later local notification or cleanup failure. Preserve its stronger terminal/output evidence and report the local failure separately; do not downgrade the remote outcome to unknown. This does not establish application success: the service still validates and durably saves outputs/metadata, and local saving may fail.

Initial DTFlow safety budgets: at most four final artifacts, 32 MiB encoded per artifact and 64 MiB aggregate final bytes per request; 64 MiB maximum individual transport payload and 64 MiB decoded tensor buffer; one retained latest preview per attempt, at most 2 MiB encoded. Account for intermediate allocations and use a shared 256 MiB admission budget for decoding/encoding buffers. These are configurable internal limits subject to target-machine validation, not a peak-RSS guarantee. Do not submit an obviously oversized request; enforce payload/dimension/count limits after receipt and before decoding (gRPC delivers whole messages, so limits during transport are not enforced; see the integration notes for the residual fpzip allocation risk). A limit breach after possible submission closes transport and retains remote uncertainty. Do not silently truncate output or label it complete. Chunking remains disabled until bounded assembly is implemented and tested.

## Events, errors and submission evidence

BackendEvent contains request_id, monotonically ordered sequence, kind, timestamp and typed payload. Kinds include preparing, submission_possible, progress, preview, artifact_received and terminal. Progress carries signpost/stage and counters only when supplied; unknown percentage stays absent. Receiving an artifact is not request completion. The queue dispatcher may durably mirror selected lifecycle events as diagnostic evidence, but that event log is not scheduling state and logging failure must not turn a successful generation into failure.

SubmissionStage is a separate evidence field:

- NOT_SUBMITTED: generation RPC has definitely not crossed the send boundary.
- POSSIBLY_SUBMITTED: marked conservatively before calling GenerateImage; remote execution may occur.
- TERMINAL_OBSERVED: terminal RPC status observed; final decoding/application may still fail.

No inspected generation job/status API resolves ambiguity. TERMINAL_OBSERVED alone is not confirmed success or a universal guarantee compute stopped. Carry terminal status when known and remote outcome separately (not_started / unknown / succeeded / failed / cancelled_confirmed); only promote an outcome when supported by evidence. Phase 3's durable potentially-executing mark precedes adapter dispatch; transport events cannot replace that write.

BackendFailure contains category, stable code, sanitized message, request_id, submission_stage, remote_outcome, known terminal status, timeout phase if relevant, diagnostics and optional partial artifact evidence. Categories: validation, credentials, authentication, connectivity, timeout, remote_rejection, protocol, decoding, resource_limit and cancellation. The application service adds persistence for state/output failures. Preserve provider status behind a sanitized diagnostic field, not provider exception types. Unknown refusal stays generic remote_rejection; no invented quota taxonomy. Never auto-retry generation from category alone: the one shared rule (`core/retry_policy.py`, #241) also requires a definite outcome (not started or failed), or the Draw Things+ terminal `INTERNAL` "unknown error processing request" case with no image.

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

Keep an integration workaround ledger (version, source, reason, fixture, removal condition) with implementation. In 2D, size replacement work separately for generated gRPC/protobuf bindings, FlatBuffer configuration serialization and compressed tensor decoding/PNG encoding; identify source licenses, fixture coverage and target-machine constraints. This is a risk estimate, not authorization to rewrite or relicense the dependency. Dependency replacement/licensing evaluation must not alter the application contract. No licensing outcome is implied by module isolation.

Phase 2 implements the credential-provider interface, environment provider and injected fake saved provider only. Real Windows Credential Manager entry/storage arrives in Phase 4. Preserve environment-presence precedence, including empty/invalid failure with no fallback. After stripping surrounding whitespace, a present empty/whitespace-only value is a selected-but-invalid credential and fails before network I/O; it never selects the saved provider. The diagnostic probe combines absent/empty values in its missing-key error and has no saved-provider fallback, so its fail-before-network behavior agrees with this policy without establishing the future error taxonomy. The Phase 2 default saved provider is unavailable; tests inject it to cover precedence. Credentials/tokens never enter connection records, requests, artifacts or logs.

## Evidence and implementation gates

Test each behavior at the lowest authoritative layer. Common serialization, codecs, TLS and stream behavior are tested once at their owning shared/2B layer; adapter composition needs coverage where it adds distinct behavior. Reuse counts only when the cloud adapter executes that same implementation path, not merely because both adapters use the same dependency. Phase 2B now provides serialization/codecs and scripted TLS/gRPC fixtures; Phase 2C must demonstrate any shared channel/stream execution it relies on. Multiple acceptance requirements may be satisfied by one scenario. See the [cloud acceptance mapping](cloud-protocol.md#phase-2c-acceptance-and-test-layer-mapping).

On 4 October 2026, an isolated uv sync --locked of PR d8e28fa's pyproject/lock/package files installed drawthings-py 0.4.0, betterproto 2.0.0b7 and grpclib 0.4.9 on Linux CPython 3.13.15. Import/source inspection confirmed ImageGenerationServiceBase with Echo and server-streaming GenerateImage, grpclib.server.Server.start(ssl=...), and RecvRequest metadata/deadline events. This is installed-interface evidence, not server execution, Windows packaging or generation testing.

Use those bindings for the Phase 2B fixture server; no second generated schema or gRPC stack is required. Capture authorization/deadline metadata through grpclib events when the high-level service base does not expose the stream. Test the contract through actual adapters, including terminal-error preservation and no final output. Revisit this evidence on dependency updates.

Sources: [0.4.0 builder](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/request_builder.py), [client](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/grpc/grpc_service.py), [configuration serializer](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/configs/gen_config.py), [generated bindings](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/generated/dt_grpc/image_service/__init__.py), [research](../research/dtflow-ecosystem-research-report.md).

## Phase 2A implementation evidence and limits

The source-run `dtflow` entry point owns one loop. `generate --backend fake` calls `GenerationService` with `FakeBackend` and an injected `AttemptStore`; the CLI composes `SQLiteAttempts`. Provider imports are absent from core/services and checked by AST tests. Requests use frozen slotted dataclasses; nested request/result metadata and artifact payloads reject mutable values. The original Phase 2 slice established inventory/preflight boundaries; later Phase 3 work implemented LoRA compatibility evidence and cloud asset preparation/reuse. These remain backend/model-specific rather than proof of every LoRA's application.

The fake model `fake-fixture` returns the bundled illustrative image fitted to the requested size (the shared output validation still requires the artifact to match the request), `fake-grid` returns a deterministic calibration grid, and either records every requested parameter and explicit seed zero. It does not exercise sampler algorithms or diffusion steps. Fake scenarios include pre-submission refusal, lost connection, empty output, terminal failure, controlled delay (optionally simulated as stepped progress with preview images) and concurrent calls. The empty scenario deliberately violates successful-output expectations to exercise defensive service validation. Fake events are explicitly fixture events. Authentication is not applicable; no credential is read by fake generation.

Attempt schema version 1 has unique request identities and transactional state changes. The service commits `prepared` before admission and `possibly_submitted` before calling the backend. Terminal OK plus validated PNG moves an attempt to `saving`; only completed file/metadata writes and a final commit produce `succeeded`. Reopening an owned store reconciles `prepared` to `interrupted_before_submission`, `possibly_submitted` to `unknown`, and `saving` to `save_failed` while preserving known backend success. No path automatically submits these records again. The current `attempts` command inspects. A manual Retry (Generate's Retry button, Queues' Retry item, #240) never reuses an attempt: it copies the recorded request with a new `request_id` (same seed and settings, evidence `retry.of_request` naming the previous one), so the old attempt keeps its final state and the retry is a new attempt. Queues retries in the same item: the item returns to `pending` with the new request, its previous attempt is named in an `item_retried` queue event.

One OS advisory owner lock covers the state directory. SQLite uses its owned worker, WAL, FULL, foreign keys and a bounded busy timeout; backup uses the SQLite API. Unsupported schema/runtime versions fail without reset. Local filesystem placement is an operator requirement; UNC paths are rejected, but mounted network filesystems are not reliably detected. Windows process-crash, locking and file-durability acceptance still needs target-machine evidence. Individual files use same-directory replacement with file fsync and directory fsync where supported. Files and the database are not jointly atomic. Failed/interrupted saves retain the attempt directory as diagnostic evidence; they are never presented as application success or automatically promoted on restart.

Admission reserves the requested RGBA-sized buffer plus three encoded-artifact allowances and a preview allowance against the shared service budget before backend submission. Direct-cloud text-to-image is the owner-accepted exception (PR #138): it reserves one encoded-artifact allowance (32 MiB) so ten requests fit the 320 MiB budget, although results are still decoded client-side; this relies on headroom, not a measured bound. Image edits keep the full conservative allowance. Codec validation and saving are serialized per service; callers composing multiple services must inject the same budget for aggregate admission. Returned results and external consumer retention are outside that budget; it is not an RSS guarantee. Event buffering coalesces transient updates and does not retain final artifact payloads. Backend-total deadlines exclude admission and durable saving. Already started worker operations finish before cancellation propagates; Python cannot kill a blocked worker, so the cleanup deadline does not bound disk/codec worker completion. Do not destroy their loop or release state ownership early.

Phase 2A tests cover fake lifecycle/inventory, failure boundaries, PNG validation, bounded event/memory admission, owned-worker cancellation, fresh-process restart and CLI inspection without replay. The root test guard installs before collection, permits exact registered numeric IP/port fixtures, and guards DNS, socket and asyncio transport sends. Only explicitly registered Python children bootstrap that same guard. This is an accidental-call guard, not an OS sandbox. Phase 2B adds the scripted TLS/gRPC fixture, and Phase 2C composes it with HTTP mocks.

Build metadata currently reports the package version and explicitly unknown source revision/status, without runtime Git. Bundled revision generation, production logging, secure saved credentials and packaged per-user paths remain later milestones. Fake CLI progress goes to stderr and result/inspection JSON to stdout. No release tag is created.

Phase 2C implements [direct-cloud composition](cloud-integration.md) by inheriting the local adapter's actual channel, stream, serialization and codec path. Only authorization/health hooks, backend identity and client concurrency differ; shared behavior remains authoritative in 2B tests. Cloud-specific auth/parser/HTTP tests cover the new boundaries.

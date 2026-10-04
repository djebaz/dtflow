# DTFlow: Draw Things ecosystem implementation research

Prepared: 4 October 2026 (Europe/Paris)  
Updated: 4 October 2026 — backend-contract and implementation-phase follow-up  
Purpose: identify code, algorithms, workflows and architectural methods useful for developing [DTFlow](https://github.com/djebaz/dtflow).

## 1. Findings and recommendation

The most useful combination for DTFlow is:

- **Keep DTFlow’s existing drawthings-py integration and diagnostic evidence as the starting point.**
- **Use dts-utils as a Python implementation reference**, selectively adapting missing transport, configuration, streaming, image-codec and artifact-management behavior.
- **Use TanqueStudio as the principal queue and workflow reference**, especially batch expansion, workflow compilation, preflight checks and configuration fidelity.
- **Use DrawHeadlessThings as a capability-contract reference**, rather than adopting its Apple-specific engine.
- **Bring a narrow native Python DT+ backend forward into Phase 2**, alongside the app gRPC backend, to exercise the backend-neutral contract against both paths. This is a proposed roadmap change, not an implemented feature.
- **Add an explicitly selected deterministic fake backend** for offline development and application-level tests.
- Add an MCP interface only when agent-driven use is a concrete product requirement. MCP is an automation interface, not a prerequisite for implementing the desktop application.

These are engineering recommendations from inspected source and documentation. None of the upstream applications was installed or run during this investigation.

### Important correction to the preceding assessment

The first inspection found a mostly empty DTFlow scaffold. A later retrieval during report preparation found a substantially newer main branch: Python client dependencies, a concurrency probe and research documenting successful direct-cloud experiments had been added.

Consequently, “DTFlow is only a scaffold” is no longer an adequate description. Its application packages remain largely placeholders, but its diagnostic integration already provides meaningful implementation and compatibility evidence. The recommendations below account for that newer state.

## 2. Scope and evidence

This report concerns reusable implementation for DTFlow. It also places the previously discussed MCP projects and TanqueStudio in context.

Evidence labels used here:

| Label | Meaning |
| --- | --- |
| Source-observed | Behavior or structure directly visible in retrieved source |
| Documented | A repository document states the behavior |
| Repository-recorded live evidence | DTFlow records prior live tests; this investigator did not repeat them |
| Earlier conversation finding | Reported in the preceding conversation, without a new source inspection here |
| Recommendation | Proposed DTFlow design, not existing behavior |
| Unverified | Not established by this investigation |

The source review was selective, not a full audit of every repository. Some files were inspected in relevant excerpts. Repository trees establish file presence, not implementation correctness.

The GitHub branch links in the appendix can change. The captured recursive-tree identifiers were:

| Repository | Earlier capture | Updated DTFlow capture |
| --- | --- | --- |
| djebaz/dtflow | 173a6cdfa41f9a2ac48639d1cfa1d0172b15ab68 | 595abf9161a1d9672b055a66cb3e4c659617675f |
| funkatron/dts-utils | 559ae2a53f99e0e64aa4c35e93a986fd9da4a028 | — |
| skeptict/TanqueStudio | 6474f1ea575afa50dea92ab56ef01b5bf50d1bd7 | — |
| gwenn-ha-dev/DrawHeadlessThings | 3a8a0c0053db4cde64718ca6a655695ad274d10f | — |

These identify captured repository trees returned by GitHub. File reads in the earlier comparison used main and were not all pinned to one atomic revision. The newer DTFlow research and probe reads were pinned to its updated captured identifier.

## 3. DTFlow’s current direction

The repository contract and plan define a Windows desktop application using Python, PySide6, Qt Widgets and canonical Qt Designer .ui files. The core should remain independent of Qt, protobuf, gRPC and provider-specific types.

The intended ownership is:

| Area | Responsibility |
| --- | --- |
| core | Requests, results, progress, jobs, workflows, presets and state transitions |
| backends | Provider connections, protocol conversion, authentication and transport |
| services | Submission, scheduling, connection management and orchestration |
| persistence | Durable application state, metadata and output references |
| ui | Presentation and interaction |
| tools/drawthings | Diagnostic tooling, outside the production application core |

The refreshed pyproject.toml selects Python >=3.13,<3.14, drawthings-py >=0.4.0,<0.5.0, and a uv NumPy override to 2.3.5. The research records successful imports, connection and generation on a legacy Windows Pentium E6500 environment. This is evidence for that tested configuration, not a guarantee for every Windows machine.

### Existing diagnostic work

The inspected probe imports drawthings-py, builds controlled generation jobs, supports serial and concurrent comparisons, and includes direct DT+ mode. Its cloud path uses an API key from an environment variable; the source header explicitly says credentials and derived tokens must not be printed or persisted.

DTFlow’s research documents the following direct-cloud protocol path:

1. Obtain a short-term token through /sdk/token.
2. Authenticate each generation through /authenticate.
3. Connect over TLS to compute.drawthings.ai:443.
4. Submit generation and decode/save returned images through the Python client stack.

The research records successful user-run experiments. This investigator did not perform those experiments. The follow-up inspected the official public protocol and cloud-authentication implementation, but did not independently exercise the deployed service. MediaGenerationKit is identified there as a protocol reference, not a runtime dependency of the tested Python path.

### Backend concurrency must remain separate

The repository records a three-job iPhone/Bridge concurrent run taking 31.30 seconds, compared with 25.20 seconds serially. It describes that tested path as an effective single generation lane.

It also records six direct DT+ requests completing concurrently in 13.84 seconds, compared with 54.42 seconds of summed serial generate() durations. The reported approximately 3.93× ratio compares those particular timing boundaries; it is not a universal speedup or a proven six-slot entitlement.

Recommendation: preserve a distinct scheduling policy per backend. Do not transfer direct-cloud concurrency assumptions to the app/Bridge backend. Production cloud throttling, cancellation, retries, token refresh and recovery remain open work.

## 4. dts-utils: the most directly adaptable Python reference

### 4.1 Transport and connection configuration

The inspected grpc/connection.py creates secure or insecure channels, sets send/receive message limits, accepts root certificates and constrains its ordinary trust-presented-certificate option to loopback.

This is useful when designing DTFlow connection profiles and diagnostics. Certificate fetching, hostname overrides and trust behavior should be reviewed against DTFlow’s selected client stack and packaged Windows environment.

DTFlow already uses a different Python client path. Adapting the ideas does not require introducing a second gRPC stack. Keep one coherent transport implementation unless a verified missing capability justifies another.

### 4.2 Configuration conversion

configuration_build.py contains extensive mappings between Draw Things JSON keys and FlatBuffer field names. It also converts selected pixel dimensions to the schema’s 64-pixel units and invokes flatc.

Useful applications include preset import, explicit wire conversion and conversion tests. The observed legacy heuristic distinguishes some snake_case dimensions based on their numeric value; its own comment documents ambiguity for a value of 64.

Recommendation: use explicit configuration format/version information rather than inheriting ambiguous unit detection. Preserve the requested configuration and record any normalized dimensions.

Because this implementation invokes an external flatc executable, it is a packaging reference rather than automatically suitable for DTFlow’s Windows single-executable goal. Verify whether drawthings-py already supplies the required encoding before adopting it.

### 4.3 Streaming and result handling

generation_stream.py:

- iterates GenerateImage responses;
- decodes preview payloads;
- combines MORE_CHUNKS payloads;
- collects final image tensors;
- offers count-based diagnostic logging without dumping image payloads.

These are valuable references for previews, chunk assembly and protocol tests.

However, the inspected implementation emits its best decodable preview as an image when no final images were collected. DTFlow should not inherit that as a successful completed result. A preview may be saved for diagnosis, but final-output absence needs a distinct outcome.

The inspected generator also collects final tensors before yielding them after stream completion. Therefore it should not be described as guaranteeing incremental persistence of every final frame.

### 4.4 Tensor and image codec

tensor_png.py contains Python routines for Draw Things tensor headers, uncompressed float16 tensors, fpzip-compressed tensors, PNG output and image-to-tensor input conversion.

This provides concrete material for decoding fixtures and image-input support. It depends on NumPy, Pillow and fpzip. Review native dependency availability against DTFlow’s actual Windows environment and compare existing drawthings-py functionality before duplicating a codec.

Do not assume the inspected image conversion preserves masks, transparency or every input mode: its input normalization and encoder convert images to RGB.

### 4.5 Pipeline artifacts and persistence

pipeline/contracts.py defines artifact references, image/video metadata, parent artifact IDs and step records. pipeline/runner.py implements sequential steps, output handoffs, heartbeats, cache indexing and per-step/run JSON manifests.

The useful design is explicit provenance: each output points to the step and upstream artifacts that produced it.

It is not a complete restart-recovery engine. The inspected runner uses plain JSON writes and in-memory execution records; no transaction, atomic-write or recovery guarantee was established.

Additional behaviors to avoid copying unchanged:

- The runner writes a cache entry after execution even when allow_cache is false. DTFlow’s --no-cache contract requires disabling both cache reads and writes.
- An out-of-memory retry can reduce dimensions and duration. DTFlow should require an explicit policy and persist the effective request for each attempt.
- Cache identity should include all output-affecting inputs and reliable model identifiers. A hash alone does not establish reproducibility.

### 4.6 How to reuse it

dts-utils has a directly inspected MIT license. Selected code can be adapted while retaining the applicable copyright and permission notices.

Recommendation: adapt focused utilities only after comparing them with the existing dependency. Avoid importing its CLI router, web server, MCP layer or macOS installer into DTFlow’s core merely to obtain a small backend helper.

## 5. TanqueStudio: the strongest workflow and queue reference

TanqueStudio is a native macOS client. Its Swift/AppKit/SwiftUI implementation is not a drop-in Python/PySide6 component. Its algorithms, data boundaries, fixtures and diagnostic lessons are still relevant to DTFlow.

### 5.1 Pure queue expansion

RenderQueueExpander.swift separates planning from persistence and execution. Plain values become resolved jobs, with warnings available before enqueueing.

It supports two useful semantics:

| Mode | Example | Jobs |
| --- | --- | ---: |
| Cross-product | 3 images × 3 prompts | 9 |
| Paired | Image i uses prompt i | 3 |
| Mixed | 3 paired image/prompt entries × 2 step settings | 6 |

Unequal paired axes stop at the shortest length and produce a warning. Active-axis order determines variation order. Parameters include prompts, negative prompts, model, sampler, seed settings, LoRA sets, steps, guidance, strength, shift and source images.

The source also contains canvas fitting at the configuration’s pixel budget instead of adopting an oversized source image’s dimensions.

Recommendation: implement a pure Python planner in core, producing immutable resolved requests for services to enqueue. Add a maximum expansion size before allocating or submitting a large matrix. Report invalid numeric values explicitly rather than silently dropping them.

### 5.2 Workflow compilation

StoryFlowCompiler.swift turns story scenes into reusable workflow steps. It re-applies base configuration and clears accumulated prompt/reference state between scenes, preventing one scene’s overrides from leaking into the next.

This is a useful general pattern: compile a higher-level workflow into explicit executable steps, then execute those steps through the same job services as ordinary generation.

DTFlow need not adopt narrative authoring immediately. The compiler pattern applies equally to “generate → img2img → upscale” and controlled parameter experiments.

### 5.3 Preflight validation

StoryFlowRunPreflight.swift classifies unsupported instructions by their effect, detects workflows that produce no images and identifies renders with no accumulated prompt.

For DTFlow, preflight should resolve references, verify required inputs, identify unsupported operations and show the concrete execution plan before generation.

Preserving an imported instruction is different from executing it. A workflow should not appear fully successful when skipped instructions materially changed its inputs.

### 5.4 Engine semantics and documentation drift

The current StoryFlowEngine.swift includes run states, per-step results/logs, loop counters, wildcard tracking, cancellation entry points, approval state and raw generation-stage timing.

The older Docs/workflow-execution.md describes trigger and canvas behavior that differs from the newer source/preflight comments. It should not be used alone as the current executable contract.

Recommendation: establish versioned workflow semantics from source and controlled fixtures. Reproduce relevant behavior deliberately; do not mix incompatible generations of the StoryFlow format.

The engine uses Apple frameworks, including Vision. Porting a Python state machine does not automatically port its mask detection or image processing.

### 5.5 Connection reliability

The inspected connection-reliability specification documents several failure patterns:

- stalled calls without deadlines;
- loading flags that never reset after failure;
- refresh buttons blocked by stale in-flight state;
- connection checks that omit authentication;
- confusing an empty inventory with a disconnected server.

Useful DTFlow methods are bounded connection calls, reliable cleanup, cancelable refresh attempts and separate health/authentication/inventory states.

These are repository-documented incidents and remedies. Their presence does not prove identical failures occur in DTFlow or every Draw Things version.

### 5.6 Configuration fidelity

The gRPC parity specification traces UI fields through model conversion and wire encoding. It records seed-mode mappings that changed actual wire values despite plausible UI labels, and distinguishes different dimension encodings.

Recommendation: create an end-to-end field inventory for DTFlow:

UI value → core request → backend conversion → encoded request → saved metadata.

A control should be functional, explicitly unavailable, or absent. Persisting the UI value is insufficient if the backend encodes something else.

The inspected queue source also discusses ambiguity between the server’s file inventory and models available through Bridge Mode. Treat “not advertised” as unconfirmed when the protocol cannot establish availability; do not invent confirmed compatibility.

### 5.7 Licensing status

TanqueStudio’s README links to an MIT LICENSE, but the inspected recursive tree contained no LICENSE file. This investigation therefore confirms a README claim, not an inspected license grant covering every source component.

Clarify the license and inspect component notices before copying substantial code. Its bundled third-party decoder material also needs its own provenance review. Algorithmic ideas can guide a separately implemented DTFlow design.

## 6. DrawHeadlessThings: capability-aware design

DrawHeadlessThings embeds a Draw Things engine in an Apple Silicon/macOS server. That is a different architecture from DTFlow’s Windows client controlling an existing backend.

CapabilityMap.swift models operations such as text-to-image, image-to-image, inpainting, editing, video and restoration. Its contracts represent field types, required fields, numeric constraints, accepted parameters, refused fields and silent drops.

The useful method is to separate:

- model identity and architecture;
- operation support;
- request constraints;
- backend-specific limitations.

DTFlow could expose backend-neutral capability information to its UI and preflight layer. Unknown support should remain unknown until reliable metadata or testing establishes it.

Do not copy this server’s capability tables as universally authoritative for the app’s gRPC backend. It uses its own engine integration and SDK revision.

Its inspected LICENSE is GNU GPL version 3. Copying implementation into DTFlow requires a separate compatibility decision for DTFlow’s MIT distribution. The present recommendation is design reference, not direct incorporation.

## 7. MCP projects and their relevance

Earlier conversation findings described:

| Project | Earlier documented scope | DTFlow relevance |
| --- | --- | --- |
| james-see/mcp-drawthings | Basic HTTP status/configuration and image generation/transformation | A small example of agent-facing tool boundaries |
| my13each/Drawthings_MCP | A related HTTP bridge with FLUX schnell defaults | No established reason to prefer it as DTFlow’s gRPC foundation |
| dts-utils MCP | Tools over the same Python generation/configuration APIs | Useful later if DTFlow exposes agent automation |
| DrawHeadlessThings MCP | Tools over its own headless engine | Capability and request-validation reference |

The first two repositories were not newly source-audited in the DTFlow comparison. Their earlier capability descriptions must not be treated as new verification.

An MCP server can let a coding agent invoke generation and inspect outputs, but it does not itself supply DTFlow’s Qt application, durable queue or workflow editor. If added, MCP should call the same application services as the GUI rather than create a parallel execution engine.

## 8. Revised recommended implementation sequence

At the original inspection, the roadmap placed the desktop shell in Phase 3 and production DT+ integration in Phase 11. Subsequent planning updates bring narrow direct DT+ into Phase 2 and require Phase 3A single-generation services plus Phase 3B durable rolling queues before Phase 4 Qt. The recommendations below reflect that revised plan; they do not claim runtime implementation.

| Priority | Work | Scope and references |
| --- | --- | --- |
| 1 | Write the backend contract and run a Windows packaging investigation | Resolve completion, cancellation, uncertain outcomes, dependency/resource packaging and distribution constraints |
| 2 | Implement backend-neutral requests, results, events, errors and capabilities | Keep Qt, protobuf and provider types outside core |
| 3 | Implement a deterministic fake backend | Offline success/failure/cancellation scenarios through the same contract |
| 4 | Wrap the existing Draw Things app gRPC path | Preserve the validated Python dependency path; compare dts-utils only for gaps |
| 5 | Implement narrow native Python direct DT+ support | Environment-supplied API key, expiry-aware authentication, TLS, subscription-only policy and truthful errors |
| 6 | Deliver the headless CLI and shared services before Qt | Backend selection, connectivity, inventory, text-to-image, durable outputs/metadata and honest interruption |
| 7 | Deliver durable rolling queues through the CLI before Qt | Phase 3B: bounded refill, stable item identity, paused restart and uncertain reservations; no remote reattachment |
| 8 | Build the minimal PySide6 desktop shell | Reuse CLI-exercised services; canonical Designer sources and responsive execution |
| 9 | Wire GUI generation and queue controls through shared services | Successful stream termination and validated outputs precede completed job state |
| 10 | Add bounded batch planning and advanced scheduling | TanqueStudio pair/cross expansion over the existing rolling scheduler |
| 11 | Add presets, image inputs and compiled workflows | Capability preflight, verified combinations and provenance |
| 12 | Broaden DT+ hardening and optional MCP automation | Additional inputs, billing opt-in if requested, verified cancellation and service-backed automation |

Phase 3 should deliver a working headless CLI through shared application services before Phase 4 adds Qt. Include explicit backend selection, non-generative connectivity, advertised model inventory, supported txt2img generation, output/reproducibility metadata saving and honest interruption. Qt must call the same services; it must not introduce a parallel orchestration path. Phase 3B also requires functional durable queues and rolling dispatch tested through the CLI, including ten items at a cap of four, paused restart and uncertainty handling. Full backend feature parity, cloud uploads, workflows and advanced scheduling remain separately scoped work.

MediaGenerationKit should remain a protocol/reference source. No Swift runtime is required by the recorded successful Python diagnostic path. A Swift bridge is a fallback only for a concrete capability that cannot be implemented reliably in Python.

Do not turn the diagnostic probe into production code simply by moving it. Its cached short-term token has no expiry handling; production errors, cancellation, persistence and credential handling need explicit contracts. The six-job diagnostic result does not establish a production concurrency ceiling.

## 9. Fake backend and validation strategy

Use a fake implementation of the same generation-backend contract, rather than building an unrelated mock execution engine into the UI. This is proposed work, not an existing DTFlow component.

| Fake scenario | Application behavior to validate |
| --- | --- |
| Deterministic success | Progress events, output persistence, metadata and gallery wiring |
| Slow generation | UI responsiveness, pending jobs and shutdown |
| Rejection before submission | Connection/authentication failure handling without ambiguous execution |
| Transport loss after submission | Unknown outcome without automatic resubmission |
| Cancellation | Distinguish local interruption from confirmed remote stopping |
| Configurable concurrency | One effective generation lane or configurable parallel capacity |

Return a bundled fixture image with metadata explicitly identifying the result as fake. Use controllable timing so automated tests run quickly. Fake mode must be explicitly selected and visibly identified; a failed real backend must never silently fall back to it.

Separate three kinds of validation:

1. Fake-backend contract tests for application orchestration, later persistence and UI behavior.
2. Local gRPC server tests through the actual adapters/client transport: previews, chunks, empty outputs, terminal gRPC status, deadlines and cancellation/disconnects. DT+ HTTP mocks cover token acquisition, expiry/refresh, per-generation authentication and subscription-only policy. Reuse the local generation server where the wire contract is shared and assert cloud authorization metadata on its gRPC side.
3. Separately gated live checks for deployed behavior that cannot be established offline.

Cheap checks should cover enum/unit conversion, seed fidelity, queue ordering, paired-axis mismatches, incomplete chunks, missing final output, cache bypass and restart reconciliation. Qt tests should focus on responsiveness and lifecycle wiring. Ordinary tests must not submit real generation.

Make test endpoints and TLS trust injectable without weakening production certificate/hostname verification. Ordinary tests must fail on unexpected external connections so missing mocks cannot reach production. Keep simulators small and scenario-driven, without models or a reimplementation of Draw Things internals. Exercise durable queues and restart behavior in Phase 3B before Qt; extend UI-specific coverage when Qt arrives.

A fake or local protocol simulator can reproduce a defined scenario; it cannot prove production compute stops, actual billing semantics, model compatibility or Windows packaging. Fabricated quota errors test application behavior only; production classifications require documented responses or sanitized captured fixtures.

## 10. Follow-up findings on the eight backend questions

### 10.1 Evidence scope

The follow-up successfully retrieved source using authenticated GitHub reads. It inspected `drawthings-py` at tag `v0.4.0` (whose manifest declares version 0.4.0) and official Draw Things source pinned to `b5e9fb925ca5394b747e0e98a8c293bbab5091ca`.

The Python repository's main tree was also listed at `e459f75a430ce5bdd21ce80f70b19e846682f511`, but the implementation findings below use the v0.4.0 files. They do not assert identical behavior for every possible 0.4.x release.

The investigator inspected source, not installed runtime behavior. Previously recorded successful local/cloud runs remain repository-recorded live evidence. The original ecosystem comparison's TanqueStudio and dts-utils findings were retained; they were not freshly re-audited in this follow-up.

### 10.2 Operations available through drawthings-py 0.4.0

| Operation | Source-observed behavior | Live evidence available in DTFlow |
| --- | --- | --- |
| Connect and discover models/files | Async channel setup and Echo-based model inventory | Recorded connection/model-browser success |
| Text-to-image | Request construction, streamed generation and returned tensor decoding | Recorded app/server and direct-cloud success |
| Image-to-image | Init-image input, resized to request dimensions and RGB | Combination-specific behavior unverified |
| Inpainting | Binary mask; white is documented as edited, black as preserved; optional alpha-based mask | Combination-specific behavior unverified |
| Control hints | `depth`, `pose`, `scribble`, `color`, `shuffle`, `custom`; weighted image tensors | Compatibility matrix unverified |
| Moodboard/reference inputs | Multiple weighted shuffle hints | Model-specific interpretation unverified |
| Progress/previews | Callback receives a protocol signpost and decoded preview | API described as unstable; previews may be discolored |
| Video/audio results | Frames collected as image buffers; optional audio tensor decoding | Not established by the recorded lightweight image workload |
| Direct DT+ authentication | DTFlow probe adds token requests and authenticated transport | Recorded controlled txt2img success; not built into the standard GrpcService |
| Remote cancellation/status/reconnect | No explicit high-level cancel/status/reattach operation in inspected client | Unverified or unavailable through this interface |

The request builder stores ordinary controls in a dictionary keyed by control type, so setting another image for the same ordinary type replaces the previous entry. Moodboard images are a separate list. This is a client representation constraint, not proof of a universal server constraint.

The result collector infers video when output count differs from its batch metadata count. Audio sample-rate calculation assumes 25 fps. DTFlow should not promote these heuristics into authoritative media metadata.

### 10.3 Protocol conversions DTFlow should own

DTFlow should own application-to-backend mappings and their tests:

- explicit prompts, negative prompts, model identifiers, LoRA settings, dimensions, steps, guidance and seeds;
- image/control/mask preparation policy and effective normalized parameters;
- backend-neutral progress and capability information;
- sanitized typed errors and request-stage information;
- final artifacts and durable provenance;
- direct-cloud token lifecycle, per-request authentication and billing policy.

Initially reuse drawthings-py's existing FlatBuffer serializer and tensor/image codecs. Avoid duplicating generated protocol bindings or adding another gRPC stack without a verified need. Any compatibility workaround belongs inside the backend integration.

Two concrete source-level issues need focused coverage:

- **Seed zero:** `build_grpc_message` uses `builder.config.seed or -1`, so a configured zero is routed to the seed provider rather than directly preserved. DTFlow must verify/fix zero-seed fidelity in its adapter and persist the effective wire seed.
- **Chunked images:** GrpcService extends its image list with each payload without reassembling `MORE_CHUNKS` / `LAST_CHUNK`. The request builder leaves `chunked` false. Keep chunked output disabled initially, or implement/test assembly before enabling it. dts-utils remains a useful algorithmic reference.

A `LAST_CHUNK` completes an artifact's chunk sequence; it is not necessarily a final completion marker for the whole RPC.

### 10.4 Authoritative final completion

For both inspected generation paths, DTFlow should require:

1. Successful terminal gRPC status, after consuming the response stream.
2. Complete, decodable required final artifacts, with output-count expectations appropriate to the operation.
3. Successful durable artifact and metadata persistence before the local job becomes completed.

This is a proposed application invariant grounded in the inspected stream semantics. A preview, sampling count, image-decoded signpost or one received image does not alone establish completion. Preserve partial artifacts as partial evidence if the stream later fails. A preview saved when final generation fails must remain explicitly labeled as a preview or diagnostic artifact in persisted metadata and the UI; it must never be promoted to a completed result or make the job appear successful.

The reference server can return an OK terminal status with no generated images; its delegate separately treats empty image output as unsuccessful. GrpcService rejects a stream with no images. Consequently, OK status alone is insufficient.

### 10.5 Cancellation semantics

The official reference server registers `context.closeFuture` to set a cancellation flag and invoke the generator cancellation callback. Progress feedback checks the flag. Source therefore shows an attempt to stop server-side generation cooperatively, not only to stop client waiting.

However:

- The protocol contains no separate cancel-generation RPC.
- The inspected Python client has no explicit server-cancel method.
- Source wiring does not establish cancellation latency or production iPhone/Bridge/cloud behavior.
- Disconnecting does not establish that compute stopped, quota was refunded, or billing ended.

Represent cancellation requested, local waiting stopped, confirmed remote cancellation when available, and unknown remote outcome separately. A fake backend's ability to confirm cancellation must not imply the real backends have that ability.

### 10.6 Restart recovery

The inspected service exposes GenerateImage, FilesExist, UploadFile, Echo, Pubkey and Hours. It exposes no generation job ID, job-status query or stream reattachment operation. `serverIdentifier` identifies a server; it is not a generation job handle.

DTFlow can recover locally persisted queued requests, resolved parameters, completed artifacts and metadata. Interrupted in-flight jobs should be reconciled to an explicit unknown outcome unless stronger evidence exists.

Do not automatically resubmit ambiguous jobs. A local UUID or saved authentication token does not make a remote generation recoverable. Remote recovery is unavailable through the inspected contract; other private/uninspected service capabilities remain unknown.

### 10.7 Subscription quota and PAYG refusal

Official cloud code reads `/billing/stripe/payg`, parses `paygEnabled` and `paygEligible`, and may set `consumableType` to `payg` when PAYG is available and estimated units are positive. It also sends an estimated amount where available. DTFlow's diagnostic probe instead leaves consumable type and amount unset.

Recommendation: DTFlow owns an explicit subscription-only default, with PAYG opt-in only if deliberately implemented and selected. Do not inherit an account-enabled PAYG behavior automatically from the reference implementation.

The source inspected does not provide a complete structured quota/billing-refusal taxonomy or an authoritative remaining-credit balance. Do not invent HTTP-code meanings. Introduce specific quota-exhausted, billing-refused or throttled errors only where documented responses or captured sanitized fixtures support them; otherwise preserve a generic authentication/remote rejection with an unknown reason.

The Python client's error helper maps INTERNAL and UNAVAILABLE to broad exceptions. It does not establish billing semantics. Other GRPCError statuses have no explicit raising branch in that helper; test that the adapter preserves terminal errors rather than relying on this helper to classify every failure.

### 10.8 Image/control/model compatibility

Request expressibility, server inventory, and successful generation are three separate evidence levels. No verified cross-backend compatibility matrix was established.

Maintain per-backend capability records including:

- exact model/checkpoint identity and operation;
- LoRA/control identifiers and required architecture;
- input image, mask and control requirements;
- supported dimensions and other request constraints;
- advertised availability versus verified combination;
- app/server/client versions and test evidence.

Begin the real adapters with the recorded controlled txt2img workload. Broaden img2img, masks, controls and LoRAs deliberately. Validate field mapping offline first and use separately authorized live checks where necessary.

The official remote executor checks requested cloud models against its handshake-derived inventory. That establishes its availability policy, not universal compatibility for all inputs or adapters.

### 10.9 Windows dependencies and distribution

DTFlow records successful source execution on the legacy Pentium E6500 Windows machine with Python 3.13, drawthings-py 0.4.0 and NumPy 2.3.5. NumPy 2.4.4/2.5.3 import failures and the Python 3.14/fpzip build problem are repository-recorded evidence, not tests repeated in this follow-up.

The inspected dependency manifest declares NumPy, Pillow and fpzip, alongside grpclib, betterproto, flatbuffers, tqdm and strictyaml. An optional video-export dependency adds imageio-ffmpeg. PySide6 remains planned in DTFlow; it is not yet a declared runtime dependency in the checked manifest.

A successful source run does not prove reliable single-executable packaging. Perform a small packaging investigation on the actual target Windows machine covering native modules, CPU compatibility, bundled resources, certificate loading, Qt plugins when added, fixture tensor decoding and non-generative TLS/model-browser connectivity. Do not require generation merely to validate packaging.

Distribution question: drawthings-py 0.4.0 declares **GPL-3.0-only**, while DTFlow declares MIT. Determine an appropriate distribution/licensing approach before shipping a bundled executable or copying dependency code. This is a directly observed metadata difference, not a legal determination that a particular distribution is compliant or prohibited.

### 10.10 Official cloud token lifecycle

CloudAuthenticator parses `shortTermToken` and `expiresIn`, caches a token with its expiry, refreshes before a configurable threshold, and coalesces concurrent token requests. DTFlow's probe only caches the token string for its process lifetime.

A production Python adapter should implement expiry-aware reuse, bounded token acquisition and concurrency-safe refresh. Token refresh before generation is different from retrying an ambiguously submitted generation. Authentication retries must not accidentally trigger duplicate generation.

### 10.11 Decisions still requiring evidence

| Question | Current conclusion | Next evidence needed |
| --- | --- | --- |
| Basic image generation | Recorded successful app and direct-cloud path | Adapter-level mocked checks; explicit live smoke if needed |
| Cancellation | Cooperative hook in reference server; production effect unverified | Controlled cancel/disconnect observations for each backend |
| Remote restart recovery | No job lookup or reattachment in inspected protocol | Additional documented endpoint, otherwise remain unavailable |
| Quota and billing refusals | Policy can be explicit; refusal taxonomy incomplete | Official contract or sanitized real failure fixtures |
| Images, masks, controls and LoRAs | Client can express inputs; combinations unverified | Versioned compatibility matrix and focused checks |
| Windows executable | Source stack recorded working; packaging unverified | Actual target-machine packaging smoke |
| Dependency distribution | GPL dependency versus MIT project observed | Explicit licensing/distribution decision |

### Verification statement

This investigation read repository documentation, selected source files, manifests and recursive trees. It produced this report and subsequently updated it with the source-inspected backend findings in Section 10.

It did not modify DTFlow, install upstream tools, run tests, train models, submit inference, download checkpoints or submit potentially billable generation. GitHub retrievals were made; Draw Things/cloud-generation requests were not. DTFlow’s recorded live results remain attributed to its research and prior user-run tests.

## Appendix A — Sources directly retrieved or inspected

Links below are ordinary source links without credentials or temporary download tokens. “Tree inspection” means file names were listed; it does not mean every listed file was read.

### A.1 DTFlow

1. [Repository](https://github.com/djebaz/dtflow) — project identity; initial public-web retrieval failed, authenticated GitHub access succeeded.
2. [Repository contract](https://github.com/djebaz/dtflow/blob/main/AGENTS.md) — architecture, uv, cache, Qt, testing and packaging rules.
3. [Initial development plan](https://github.com/djebaz/dtflow/blob/main/devdocs/plans/initial.md) — inspected both earlier and updated contents.
4. [Project metadata](https://github.com/djebaz/dtflow/blob/main/pyproject.toml) — inspected both earlier and updated dependency configuration.
5. [Unreleased notes](https://github.com/djebaz/dtflow/blob/main/devdocs/releases/unreleased.md) — current progress and documented compatibility.
6. [gRPC proof research](https://github.com/djebaz/dtflow/blob/main/devdocs/research/drawthings-grpc-proof.md) — repository-recorded live evidence and cloud direction.
7. [Concurrency/cloud probe](https://github.com/djebaz/dtflow/blob/main/tools/drawthings/drawthings_concurrency_test.py) — diagnostic source, relevant sections inspected.
8. [Recursive tree API](https://api.github.com/repos/djebaz/dtflow/git/trees/main?recursive=1) — earlier and updated repository inventories.
9. [Root contents API](https://api.github.com/repos/djebaz/dtflow/contents/) — initial inventory.

The attached project AGENTS.md was also read locally. It is a separate project instruction snapshot, not the repository AGENTS.md; no public URL was provided for that attachment.

### A.2 dts-utils

10. [Repository and README](https://github.com/funkatron/dts-utils) — documented architecture, CLI, MCP and platform boundaries.
11. [Connection implementation](https://github.com/funkatron/dts-utils/blob/main/src/dts_utils/grpc/connection.py).
12. [Configuration conversion](https://github.com/funkatron/dts-utils/blob/main/src/dts_utils/configuration_build.py).
13. [Generation stream](https://github.com/funkatron/dts-utils/blob/main/src/dts_utils/generation_stream.py).
14. [Tensor/image codec](https://github.com/funkatron/dts-utils/blob/main/src/dts_utils/tensor_png.py).
15. [Pipeline contracts](https://github.com/funkatron/dts-utils/blob/main/src/dts_utils/pipeline/contracts.py).
16. [Pipeline runner](https://github.com/funkatron/dts-utils/blob/main/src/dts_utils/pipeline/runner.py).
17. [MIT license](https://github.com/funkatron/dts-utils/blob/main/LICENSE).
18. [Recursive tree API](https://api.github.com/repos/funkatron/dts-utils/git/trees/main?recursive=1).
19. [Root contents API](https://api.github.com/repos/funkatron/dts-utils/contents/).

### A.3 TanqueStudio

20. [Repository](https://github.com/skeptict/TanqueStudio) — initial public-web retrieval failed; GitHub source retrieval succeeded.
21. [README](https://github.com/skeptict/TanqueStudio/blob/main/README.md).
22. [Queue expander](https://github.com/skeptict/TanqueStudio/blob/main/DrawThingsStudio/RenderQueueExpander.swift).
23. [Workflow compiler](https://github.com/skeptict/TanqueStudio/blob/main/DrawThingsStudio/StoryFlowCompiler.swift).
24. [Workflow preflight](https://github.com/skeptict/TanqueStudio/blob/main/DrawThingsStudio/StoryFlowRunPreflight.swift).
25. [Workflow engine](https://github.com/skeptict/TanqueStudio/blob/main/DrawThingsStudio/StoryFlowEngine.swift).
26. [Older workflow execution document](https://github.com/skeptict/TanqueStudio/blob/main/Docs/workflow-execution.md).
27. [Connection reliability specification](https://github.com/skeptict/TanqueStudio/blob/main/Docs/connection-reliability-spec.md).
28. [gRPC configuration parity specification](https://github.com/skeptict/TanqueStudio/blob/main/Docs/grpc-config-parity-spec.md).
29. [Recursive tree API](https://api.github.com/repos/skeptict/TanqueStudio/git/trees/main?recursive=1).
30. [Root contents API](https://api.github.com/repos/skeptict/TanqueStudio/contents/).

The README’s relative LICENSE link was observed, but no matching file appeared in the captured tree. It is not listed as a successfully read license.

### A.4 DrawHeadlessThings

31. [Repository and README](https://github.com/gwenn-ha-dev/DrawHeadlessThings).
32. [Capability map](https://github.com/gwenn-ha-dev/DrawHeadlessThings/blob/main/Sources/dht-server/CapabilityMap.swift).
33. [GPLv3 license](https://github.com/gwenn-ha-dev/DrawHeadlessThings/blob/main/LICENSE).
34. [Recursive tree API](https://api.github.com/repos/gwenn-ha-dev/DrawHeadlessThings/git/trees/main?recursive=1).
35. [Root contents API](https://api.github.com/repos/gwenn-ha-dev/DrawHeadlessThings/contents/).

### A.5 Backend-contract follow-up sources

36. [drawthings-py repository](https://github.com/kcjerrell/drawthings-py) — README and main tree inspected; version-specific code below used for findings.
37. [v0.4.0 gRPC client](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/grpc/grpc_service.py) — connection, inventory, progress, stream collection, result decoding and error handling.
38. [v0.4.0 request builder](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/request_builder.py) — inputs, masks, control types, seed handling and wire construction.
39. [v0.4.0 dependency/license manifest](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/pyproject.toml).
40. [v0.4.0 error helper](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/util/_errors.py).
41. [drawthings-py recursive tree](https://api.github.com/repos/kcjerrell/drawthings-py/git/trees/main?recursive=1).
42. [Official image-service protocol](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/GRPC/Models/Sources/imageService/imageService.proto).
43. [Official server implementation](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift) — cancellation, final responses and terminal status; relevant excerpts inspected.
44. [Official cloud token authenticator](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/MediaGenerationKit/Sources/CloudAuthenticator.swift).
45. [Official cloud generation authentication](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/MediaGenerationKit/Sources/MediaGenerationCloudAuthentication.swift) — PAYG flags/policy and refusal handling; relevant excerpts inspected.
46. [Official remote execution](https://github.com/drawthingsai/draw-things-community/blob/b5e9fb925ca5394b747e0e98a8c293bbab5091ca/Libraries/MediaGenerationKit/Sources/MediaGenerationRemoteExecution.swift).
47. [Official community recursive tree](https://api.github.com/repos/drawthingsai/draw-things-community/git/trees/main?recursive=1).
48. [MediaGenerationKit wrapper repository and guide](https://github.com/drawthingsai/media-generation-kit) — remote/cloud public surface and integration examples.
49. [MediaGenerationKit pipeline documentation](https://drawthingsai.github.io/media-generation-kit/documentation/mediagenerationkit/mediagenerationpipeline/) — surfaced in search; not separately opened.
50. [drawthings-py PyPI listing](https://pypi.org/project/drawthings-py/) — surfaced in search; version-specific source used for conclusions.

## Appendix B — Earlier conversation sources and follow-up references

These links were discussed earlier or identified as useful follow-up material. They were not all freshly read for this report; inclusion does not imply verified implementation or compatibility.

| Source | Relationship to this report |
| --- | --- |
| [james-see/mcp-drawthings](https://github.com/james-see/mcp-drawthings) | Earlier conversation’s basic HTTP MCP bridge |
| [my13each/Drawthings_MCP](https://github.com/my13each/Drawthings_MCP) | Earlier conversation’s related bridge |
| [Draw Things community source](https://github.com/drawthingsai/draw-things-community) | Official protocol/server and MediaGenerationKit files inspected in the follow-up; see A.5 |
| [Draw Things website](https://drawthings.ai/) | Product context |
| [Draw Things downloads](https://drawthings.ai/downloads/) | Earlier login/platform discussion |
| [DT API dashboard](https://api.drawthings.ai/dashboard) | Earlier API-key and subscription investigation |
| [dts-utils gRPC guide](https://github.com/funkatron/dts-utils/blob/main/DRAW-THINGS-GRPC-API.md) | Listed in inspected README/tree; not separately read here |
| [dts-utils protobuf guide](https://github.com/funkatron/dts-utils/blob/main/PROTOBUF.md) | Listed in inspected README/tree; follow-up encoding reference |
| [dts-utils MCP tool documentation](https://github.com/funkatron/dts-utils/blob/main/docs/generated/mcp-tools.md) | Listed in tree; future automation reference |
| [TanqueStudio queue input spec](https://github.com/skeptict/TanqueStudio/blob/main/Docs/render-queue-image-inputs-spec.md) | Referenced by inspected expander |
| [TanqueStudio StoryFlow spec](https://github.com/skeptict/TanqueStudio/blob/main/Docs/storyflow-260723-spec.md) | Referenced by inspected preflight |
| [TanqueStudio tests](https://github.com/skeptict/TanqueStudio/tree/main/TanqueStudioTests) | Tree-inspected fixtures/test names; tests not executed |
| [dts-utils tests](https://github.com/funkatron/dts-utils/tree/main/tests) | Tree-inspected tests; tests not executed |
| [DrawHeadlessThings tests](https://github.com/gwenn-ha-dev/DrawHeadlessThings/tree/main/Tests/DHTServerTests) | Tree-inspected tests; tests not executed |

The follow-up retrieved the official MediaGenerationKit repository at https://github.com/drawthingsai/media-generation-kit; see A.5. Its runtime is not required by DTFlow’s recorded native Python diagnostic path.

## Appendix C — Earlier DT+ account findings

These findings explain the API context but are separate from the implementation comparison:

- The user reported that signing into the dashboard with Apple created an account without their existing subscription.
- The user then confirmed their Draw Things account originally used Google. Signing in with that Google account exposed their Plus membership and API key.
- App Store payment identity and Draw Things login identity were therefore distinct in this case.
- The user tested an additional API key and reported that it used their subscription allowance. This corrected the earlier recommendation that only the default key should be used.
- The earlier conversation cited an allowance of up to 200 tasks/month. This report did not reverify current quota terms, additional-key billing rules or all entitlement edge cases.

Do not infer that every key has independent quota, that quota terms cannot change, or that the user’s observed subscription behavior proves every PAYG configuration behaves identically.

## Appendix D — Research access and completeness

The original DTFlow comparison used direct URL retrievals and GitHub file/tree reads without new keyword-search queries. The subsequent backend follow-up used the two search queries listed below, followed by GitHub source reads. A shell git ls-remote attempt for DTFlow failed to obtain access. It did not establish repository absence; authenticated GitHub reads subsequently succeeded.

Earlier conversation messages refer to a separate API-authentication report containing 31 search queries. Its current file contents were not supplied or read for this task. Those query strings and unidentified source URLs are not reconstructed here.

This appendix enumerates the successful source reads, inventory endpoints and known failed access attempts available to this investigation, plus clearly labeled earlier/follow-up references. It does not claim to reproduce an unavailable historical browsing log.


### D.1 Follow-up search and failed access log

The backend follow-up issued these keyword queries:

1. `github drawthings-py MediaGenerationKit Draw Things`
2. `github skeptict TanqueStudio`

Search results included the SDK, MediaGenerationKit, its documentation, community source, PyPI listings and a TanqueStudio fork. Conclusions in Section 10 use the successfully retrieved primary source files enumerated in A.5. Other surfaced results were not treated as inspected implementation evidence.

A shell `git clone --depth 1 https://github.com/djebaz/dtflow.git` failed because credentials were unavailable to Git. Authenticated GitHub reads subsequently succeeded. A follow-up public-web open of TanqueStudio failed; this does not invalidate the original report’s separately recorded successful GitHub source inspection. A GitHub tags-endpoint read was rejected by the connector URL rules; direct v0.4.0 source reads succeeded. Public-web attempts to open six raw source URLs failed, while authenticated GitHub retrieval of those files succeeded. These failures are not claimed as successful source verification.

No repository implementation changes, tests, installations, checkpoint downloads, training, inference or billable jobs were performed during this update. Only document editing and source retrieval occurred.

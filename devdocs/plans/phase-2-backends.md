# Phase 2 — Backend contract and initial adapters

**Status:** Phase 2A core/fake and durable headless foundation implemented with offline acceptance tests; 2B/2C and parallel 2D remain pending.  
**Baseline:** main commit `595abf9161a1d9672b055a66cb3e4c659617675f`.  
**References:** [backend/async contract](../architecture/backend-contract.md), [pinned cloud protocol](../architecture/cloud-protocol.md), [initial roadmap](initial.md), [ecosystem research](../research/dtflow-ecosystem-research-report.md), [recorded gRPC proof](../research/drawthings-grpc-proof.md).

## Prerequisite: Phase 1 handoff

The [Phase 1 probe](../research/drawthings-grpc-proof.md) already records local connectivity/inventory, successful generation/decoding/saving and user-reported direct-cloud/concurrency evidence. The [Phase 1 closeout matrix](../research/drawthings-grpc-proof.md#phase-1-closeout-decision) records Phase 1's bounded feasibility closure and assigns every remaining gap. Phase 2A follows in a separate implementation PR. Confirmed remote cancellation and detailed progress/error coverage remain unverified and are Phase 2 validation work; do not describe the entire Phase 1 checklist as tested. Do not rerun live generation merely to close a planning checkbox.

## Goal and boundaries

Establish one backend-neutral generation contract, exercised by a deterministic fake, Draw Things gRPC and narrow direct DT+ Cloud Compute adapters. Keep Qt, client/protobuf types and cloud authentication inside their proper boundaries.

Start real adapters with controlled text-to-image requests. Define model/LoRA metadata and compatibility preflight contracts now; investigate the separate cloud LoRA storage protocol for Phase 3 delivery. Image-to-image, inpainting, broad control/LoRA combinations, video, account login, PAYG opt-in and advanced scheduling are not Phase 2 promises. Request expressibility is not verified compatibility.

Direct DT+ is the primary expected production backend; local gRPC is a supported runtime and the frequent development/smoke-test path. Local smoke success does not prove cloud readiness.

Phase 2A proves a minimal headless fake-generation path with durable attempts and saved artifacts; Phase 3A expands it into the usable real-backend CLI. Phase 3B adds durable queues, rolling orchestration and custom cloud LoRA upload/reuse before Qt. Phase 2 must expose enough outcome information to support both safely.

## Implementation slices and parallel investigation

### Phase 2A — Contract, fake and foundational fixtures

Implement the [backend contract](../architecture/backend-contract.md): immutable request/result/artifact/event types, structured failures with independent submission evidence, capabilities, lifecycle/close, bounded connectivity and inventory operations. Use async-native services/backends; the CLI owns the top-level loop and Qt later hosts the same layer in a dedicated worker thread. Adapters never create loops. Implement the deterministic fake and shared contract scenarios first.

Define timeout/resource policies and cancellation propagation now. Establish pytest-asyncio support, fixture endpoint registration, offline network guard and live-test gates before adapter tests. Follow the repository uv workflow for any implementation dependencies; this plan changes none.

The opening 2A maintenance commit renames the diagnostic tool to tools/drawthings/drawthings_probe.py so pytest cannot collect it as a *_test.py module. Its existing checks live under tests/unit/tools/ with corrected path resolution and a one-line "Retires with the diagnostic probe" note. Current references use the new path. Preserve historical pinned source URLs. Add no new probe scenarios: retain readiness/guard coverage and carry empty-output, malformed-JSON and sanitized-error scenarios into actual-adapter fixtures in 2B/2C.

The opening maintenance commit adds pytest-asyncio through uv and removes pytest-qt from the default dev group until Qt is implemented, along with the now-unneeded -p no:pytest-qt setting. It updates pyproject.toml and uv.lock together and sets strict async test ownership with function-scoped loop fixtures. Shared async network-guard fixtures must depend explicitly on the owning event-loop fixture so the loop initializes before the guard; test bodies still run under the guard. Preserve refusal of loopback/DNS and exact fixture-endpoint registration.

Phase 2A can start from the closed feasibility proof. Build a thin working slice first: a source-run fake-generation command calls one shared async service, persists a resolved request/attempt in the selected SQLite store before dispatch, saves a fixture PNG plus reproducibility metadata and reports the actual outcome. Keep command parsing/presentation separate from services. Start with one attempt and explicit fake selection; do not build the queue scheduler or all Phase 3 CLI features here.

Prove success, failure before submission, uncertain interruption, failed persistence preventing dispatch and restart inspection without replay. Add the import-boundary check. Refine the existing architecture notes from this slice; do not require another speculative specification before coding. This brings forward only the minimal Phase 3A service/entry-point/persistence foundation. It does not complete Phase 3A, create a release or change the 3A/3B gates.

Its gate is the working path and core/fake contract acceptance without Qt/provider types. Target-Windows packaging runs separately.

### Phase 2B — Local gRPC adapter and fixture server

Wrap the recorded Python gRPC path inside the replaceable integration boundary. Implement open/close, non-generative connectivity, explicit inventory refresh with provenance and controlled txt2img. Keep shared-secret missing/wrong, empty inventory and connection failure distinct. Exercise the actual adapter with the installed generated service base and grpclib fixture server described below.

Assert wire configuration, explicit seed zero, sampler/dimension mapping, final-output decoding, terminal status, limits, deadlines and cancellation/disconnect evidence. Preserve private-access workarounds in one pinned/tested module; do not spread the probe's subclass/wrapper into services. Gate 2B on its source/installed-interface evidence and offline fixtures.

### Phase 2C — Direct cloud adapter and pinned auth fixtures

Implement the [cloud protocol](../architecture/cloud-protocol.md), sharing provider serialization/codecs privately with 2B. Use the selected async HTTP client, expiry-aware concurrent token refresh, per-generation authorization, TLS and subscription-only fields. Implement non-generative token/transport/Echo health and inventory independently from generation entitlement.

Exercise actual cloud adapter calls against HTTP mocks and the local gRPC fixture, capturing exact authorization metadata. Cover cancellation during auth, token expiry/scope changes, malformed responses and generation uncertainty. Gate 2C on the pinned source contract and fixtures; no deployed compatibility or billing claim follows from mocks.

The model/LoRA preflight and separate storage audit are contract/investigation deliverables in this phase. Implement bounded LoRA selection in 3A and custom cloud upload/reuse in 3B under [the LoRA plan](lora-assets.md), not in 2C.

### Phase 2D — Packaging, licensing and feature inventory

Run this investigation alongside 2A–2C; it does not block starting 2A. Licensing findings inform deeper drawthings-py investment and any distribution decision. Portable packaging investigation requires the owner's Windows 10 / Intel Pentium E6500 machine; CI/Linux cannot establish that outcome. Retain an installer option if needed. Inspect Python 3.13, locked native codecs, TLS/certificate resources and eventual Qt resources.

Deliver a version-pinned upstream feature inventory from TanqueStudio source/UI/docs into the parity matrix: feature, evidence, shared ownership, CLI/Qt exposure, backend limits, target milestone and acceptance. Mark gaps/exclusions explicitly; no full-parity claim while inventory remains incomplete.

Record packaging and distribution findings or explicit pending/blocker status with the evidence required. Actual target-machine acceptance remains a packaged-release gate; no inferred Windows success is required to merge the core/fake slice. Keep a short replacement estimate for the generated protocol bindings, FlatBuffer configuration and tensor codecs, with sources/licenses and required fixtures. Validate the bundled SQLite version as well as native codecs on the target machine. Do not silently change licenses or replace dependencies.

2D deliverables and status: [license decision record](../decisions/0001-drawthings-py-license.md) (proposed; decision deferred to the first public build), [replacement estimate](../research/drawthings-py-replacement.md), [Windows packaging checklist and findings](../research/windows-packaging.md) (results recorded only from owner-run checks on the target machine) and the [pinned TanqueStudio inventory](../research/tanquestudio-inventory.md).

## Required semantics

### Completion and artifacts

Require successful terminal RPC status and complete, decodable required final output. Progress, previews and an artifact's last chunk are insufficient to establish request completion. Phase 2A proves the minimal durable pre-submission request/attempt record plus output/metadata saving; Phase 3A completes that path for real backends. Required artifact and metadata writes must finish before reporting application success. Phase 3B extends it with durable queue/job lifecycle state and tested rolling scheduling.

Preserve incomplete outputs as partial evidence. A preview saved after failed generation must remain labeled as a preview or diagnostic artifact in metadata and the UI; never promote it to a completed result or use it to imply success.

### Cancellation and uncertainty

Distinguish cancellation requested, local waiting stopped and confirmed remote cancellation. The reference server has cooperative cancellation hooks; production iPhone/Bridge/cloud stopping behavior and latency remain unverified.

Record whether a failure occurred before submission or after remote execution became possible. Do not automatically retry ambiguous generation submissions. The inspected protocol has no job lookup or stream reattachment: a local UUID is not a recoverable remote handle.

### Progress and capabilities

Map actual signposts to backend-neutral events. Do not advertise estimated client progress as authoritative server completion.

Represent verified, unsupported and unknown capabilities distinctly. Keep model inventory separate from evidence that a specific model/input/adapter combination works. Start with one effective app/Bridge generation lane; direct-cloud scheduling policy remains separately configurable and must not assume six entitled slots.

### Model/LoRA metadata and preflight

Preserve exact provider identifiers, architecture/version, availability, backend/server provenance, observation time and metadata source. Reuse drawthings-py's structured Echo metadata where available; filename presence alone does not establish architecture or compatibility. Unknown/missing metadata stays unknown, with no guessed architecture default.

Shared preflight returns structured diagnostics for each selected LoRA and separates architecture match/mismatch/unknown from availability and verified generation evidence. DrawHeadlessThings is the validation-design reference; pin the applicable official Draw Things rules, including refiner/exception behavior, rather than imposing universal architecture equality. Successful generation is not proof a requested LoRA was applied.

Define a separate cloud asset service and durable receipt contract, sharing credentials/token lifecycle with generation without treating upload as generation or remote job recovery. Phase 3A implements bounded LoRA selection; Phase 3B implements upload/reuse before Qt. See [LoRA compatibility and cloud asset plan](lora-assets.md) for source evidence, unsupported remote listing and acceptance.

### Direct-cloud authentication and billing

Phase 2 implements injectable credential resolution, the environment provider and an injected fake saved-credential provider behind a Qt-independent interface. The production saved provider is unavailable in this phase; real Windows Credential Manager entry/storage arrives in Phase 4 through the same interface. Never silently save plaintext if secure storage fails. Keep credentials/tokens out of logs and ordinary persisted metadata. Implement expiry-aware short-term-token caching and concurrency-safe refresh using the inspected official token contract, plus per-generation authentication and normal TLS hostname validation.

Credential selection is deterministic: if `DRAWTHINGS_API_KEY` is present, select it; otherwise use the saved Windows Credential Manager key when available. An empty or invalid selected key fails clearly, without silently switching credential sources after validation or authentication failure. If neither source exists, report missing credentials. Expose only the selected source in sanitized diagnostics, never the key.

Keep subscription-only behavior explicit and PAYG opt-in disabled. Do not inherit account-enabled PAYG automatically. Classify quota/billing refusals only from verified response semantics; preserve sanitized generic rejection when the reason is unknown.

Authentication retry policy must remain separate from generation resubmission.

## Fake backend

Use bundled fixture output and explicitly fake metadata. Selection must be deliberate; never fall back silently from a real-backend failure.

Provide deterministic success, controllable delay, pre-submission rejection, post-submission uncertain failure, cancellation and configurable concurrency scenarios. Keep automated timing controllable and fast. Remote cancellation simulated by the fake does not establish real backend support.

## Offline testing boundaries

Implement the fake first, then add transport fixtures alongside each real adapter.

| Component | Boundary validated | Timing |
| --- | --- | --- |
| Deterministic fake backend | Core contract, progress and uncertain outcomes; later UI, jobs, queues and persistence | First |
| Local gRPC test server | Actual adapters/client transport, streamed previews and final output, chunks, empty output, terminal status, deadlines and cancellation/disconnects | Alongside the Draw Things adapter |
| DT+ HTTP mocks | Token acquisition, expiry, concurrent refresh, per-generation authentication and subscription-only request policy; later signed-upload/deduplication fixtures | Alongside the cloud adapter; asset cases in Phase 3B |

Reuse the local gRPC server for cloud generation where the inspected wire contract is shared. Assert cloud-specific authorization metadata on the gRPC side, in addition to checking HTTP token/authentication requests. Exercise the actual adapters, not only isolated payload decoders.

Make HTTP/gRPC endpoints and test TLS trust injectable so fixtures stay local without weakening production TLS hostname/certificate validation. Ordinary tests must fail on unexpected external connections; a missing mock must never reach a real app/cloud endpoint.

Keep simulators small and scenario-driven, returning defined protocol fixtures without loading models or recreating Draw Things internals. Simulated cancellation verifies DTFlow's handling, not that production compute stops. Fabricated quota errors verify presentation/application behavior, not actual billing semantics; production classifications require documented responses or sanitized captured fixtures.

Phase 2 covers the contract and adapters. Phase 3B extends fake and transport-fixture coverage through the CLI for durable queues, rolling dispatch and restart reconciliation; add Qt-specific coverage when the UI arrives.

## Test infrastructure and live gating

Phase 2A ships pytest-asyncio, the exact-endpoint guard and live gates. gRPC/TLS and HTTP infrastructure below is implemented alongside 2B/2C.

- Use asyncio and pytest-asyncio with explicit async test/fixture ownership. Add pytest-asyncio to the dev group through uv when implementing 2A; commit pyproject.toml and regenerated uv.lock together. Add the bounded HTTPX runtime dependency through uv in 2C. Do not add AnyIO as a separate service runtime merely because HTTPX uses it internally.
- Installed inspection after an isolated uv sync --locked confirmed drawthings-py 0.4.0 exposes ImageGenerationServiceBase with Echo and server-streaming GenerateImage. Subclass that base and serve through grpclib.server.Server on a fixture-owned ephemeral loopback port. Use grpclib RecvRequest events to inspect authorization/deadline metadata; return scripted responses/errors without loading models. No duplicate protocol-generation toolchain is required.
- Generate and track clearly test-only TLS certificate/key fixtures using developer OpenSSL, with subject alternative names for localhost, 127.0.0.1 and ::1. Record generation/renewal commands and validity; test setup fails clearly for an expired positive-path certificate. No OpenSSL or fixture key ships in the production executable. Inject test CA/SSLContext while preserving production trust/hostname validation; include bad-host/untrusted-certificate cases.
- Install the network guard before test collection can trigger network work, and keep a per-test registry of exact fixture addresses/ports. Guard DNS resolution plus outbound socket connect/connect_ex and datagram sends; only registered destinations are allowed, not every loopback port. Register listeners after binding before client access and unregister on teardown. Block redirects/proxy bypass and outbound subprocess network use in ordinary tests; this is an accidental-call guard, not a general OS sandbox.
- Use HTTPX MockTransport for deterministic token/auth schemas and a registered local HTTPS server where real HTTP/TLS behavior matters. Inject API/gRPC origins and time sources; a missing mock must fail at the guard, never reach production or an unrelated real local server.
- Register planned pytest markers live and live_generation. Live tests require both explicit live-marker selection and DTFLOW_LIVE_TESTS=1; otherwise skip before creating network clients. Generation cases additionally require explicit live_generation selection, DTFLOW_LIVE_GENERATION=1 and separate task/operator authorization for the named workload. Neither marker/env alone nor a connectivity task authorizes generation.
- Even live mode allowlists only the explicitly configured backend targets; do not disable the entire guard. Default test runs execute no live network calls. Cover incorrect/missing opt-ins and refusal before network setup.

Phase 2A implementation validation uses the canonical pytest/Ruff commands. Fake service, fresh-process recovery and guard tests are exercised offline; no real adapter, live generation or billable action is part of this acceptance. Run-specific environments and results belong to the PR description.

## Source-level gaps to validate

- Verify supported sampler/enum values, requested-to-wire dimensions, defaults and effective parameters. Reject unsupported options clearly before submission; never silently ignore them.
- Preserve a requested seed of zero; the inspected client routes it through its seed-provider path.
- Keep chunked output disabled initially or implement validated chunk assembly before enabling it.
- Preserve terminal gRPC errors, including statuses not explicitly raised by the client's error helper.
- Reject empty final output, truncated artifacts and partial-output-plus-error as completed results.
- Keep client media-count/audio-rate heuristics out of authoritative model metadata.
- Make image resizing, mask polarity and any normalization explicit when broader inputs are added.

## Validation and exit criteria

- Phase 2A: specified immutable contract and async ownership implemented; fake supports lifecycle, connectivity, inventory and generation; import-boundary/headless checks enforce core/service separation.
- Phase 2A thin slice: a headless fake command goes through shared services and SQLite attempts to saved PNG/metadata; persistence failures prevent submission, interrupted attempts remain unknown and restart never replays them. Include schema/runtime checks and focused failure-boundary tests.
- Phase 2B/2C: real adapters implement those same operations with bounded deadlines, distinct transport/auth/inventory status, refresh/provenance and fixture coverage.
- Fake and protocol-fixture tests make no real generation requests and fail on unexpected external connections.
- Real adapters are exercised through local gRPC transport, including terminal status, deadlines, cancellation/disconnects and cloud authorization metadata.
- Request conversion tests cover seed fidelity, explicit parameters and output metadata.
- Stream tests cover terminal failures, no final output, partial outputs and preview labeling.
- Cloud tests cover credential precedence (including both sources, empty environment values and failed selected credentials), absence of silent fallback, token expiry, concurrent refresh, per-generation authentication, sanitized errors and disabled PAYG opt-in.
- Phase 2D: record actual target-Windows packaging findings or explicit pending evidence/blocker status; unresolved distribution decisions stay explicit. CI/Linux results are not target-machine acceptance.
- Complete the version-pinned upstream feature inventory in the parity matrix, with gaps/exclusions and acceptance milestones; no full-parity claim without that deliverable.
- Test endpoint registration/external-network refusal, planned live opt-in gates and async/TLS fixture cleanup. Newly added implementation dependencies follow uv; documentation changes alone do not alter the lock.
- Inventory/preflight fixtures cover structured model/LoRA versions, missing metadata, architecture matches/mismatches and applicable exception/refiner rules; keep metadata checks distinct from successful application.
- Record the source-audited cloud upload contract and remaining converted-format/generation-reference questions before Phase 3B implementation.
- Record unsupported/unverified cancellation, recovery and compatibility behavior rather than inventing it.

Use `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` for implementation validation. Any live generation is separately gated and requires task authorization; do not use it merely to validate plumbing.

The [TanqueStudio parity matrix](tanquestudio-parity.md) tracks later feature milestones without making full parity a prerequisite for Qt.

## Handoff to the next implementation PR

PR #3 contains the merged specifications; Phase 1 is closed as a bounded feasibility proof. PR #5 implements the Phase 2A thin fake/service/SQLite/artifact path and offline acceptance infrastructure. See the [implemented scope and limits](../architecture/backend-contract.md#phase-2a-implementation-evidence-and-limits) and [source-run commands](../../README.md#getting-started). Phase 2A does not establish real transport, cloud, packaging or full CLI acceptance. Run-specific verification belongs to its PR description.

Use separate PRs for 2A, 2B and 2C: 2A establishes the tested foundation; 2B adds the local gRPC adapter/server fixtures; 2C adds direct-cloud authentication/adapter fixtures. Split a slice further when needed for coherent review rather than combining all adapters into one PR. Run 2D alongside them and publish its packaging/licensing/replacement and feature-inventory findings in separate documentation PRs as evidence becomes available. Pending owner-machine or licensing evidence remains explicit. Real-adapter fixtures precede any separately authorized live checks. Prove the Qt worker-thread approach before Phase 4; functional Phase 3B rolling dispatch and the agreed LoRA delivery gates still precede Qt.

## Deferred work

[Phase 3](phase-3-cli.md) delivers the headless CLI: 3A expands the 2A foundation into real-backend single-generation CLI/services, durable attempts and output saving; 3B adds durable queues, rolling dispatch, restart reconciliation and custom cloud LoRA upload/reuse before Qt. Phase 4 adds the Qt shell and connection management; Phase 5 wires GUI generation through those same services; Phase 6 adds batch planning; Phase 7 extends advanced scheduling; Phase 9 verifies advanced image operations; Phase 11 broadens direct-cloud capabilities. MediaGenerationKit remains a protocol reference, with a Swift bridge only if a concrete future capability requires it.


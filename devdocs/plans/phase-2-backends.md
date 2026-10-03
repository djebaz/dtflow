# Phase 2 — Backend contract and initial adapters

**Status:** Planned; this document introduces no runtime implementation.  
**Baseline:** main commit `595abf9161a1d9672b055a66cb3e4c659617675f`.  
**References:** [initial roadmap](initial.md), [ecosystem research](../research/dtflow-ecosystem-research-report.md), [recorded gRPC proof](../research/drawthings-grpc-proof.md).

## Goal and boundaries

Establish one backend-neutral generation contract, exercised by a deterministic fake, Draw Things gRPC and narrow direct DT+ Cloud Compute adapters. Keep Qt, client/protobuf types and cloud authentication inside their proper boundaries.

Start real adapters with controlled text-to-image requests. Image-to-image, inpainting, control/LoRA combinations, video, account login, PAYG opt-in and advanced scheduling are not Phase 2 promises. Request expressibility is not verified compatibility.

Durable job orchestration belongs to Phase 6. Phase 2 must expose enough outcome information to support it safely later.

## Implementation order

1. Define immutable resolved requests, results/artifact descriptions, progress events, errors and capability information in core. Record explicit seeds, identifiers and effective parameters.
2. Investigate packaging and distribution on the target Windows machine: Python 3.13, pinned dependency stack, native codecs and TLS/certificate resources. Decide how the GPL-3.0-only dependency affects distribution; do not silently change licenses or replace dependencies.
3. Implement the fake backend through the same contract.
4. Wrap the recorded Draw Things gRPC path and exercise the actual adapter against a local gRPC test server, reusing existing serialization/codecs where appropriate.
5. Implement direct DT+ authentication and transport behind its own adapter, with HTTP authentication mocks and cloud authorization checks on the shared local gRPC fixture.
6. Add shared contract and mocked transport coverage; document limitations before the headless CLI consumes the adapters.

## Required semantics

### Completion and artifacts

Require successful terminal RPC status and complete, decodable required final output. Progress, previews and an artifact's last chunk are insufficient to establish request completion. Phase 3 introduces durable output and metadata saving; those writes must finish before reporting application success. Phase 6 adds durable job lifecycle state.

Preserve incomplete outputs as partial evidence. A preview saved after failed generation must remain labeled as a preview or diagnostic artifact in metadata and the UI; never promote it to a completed result or use it to imply success.

### Cancellation and uncertainty

Distinguish cancellation requested, local waiting stopped and confirmed remote cancellation. The reference server has cooperative cancellation hooks; production iPhone/Bridge/cloud stopping behavior and latency remain unverified.

Record whether a failure occurred before submission or after remote execution became possible. Do not automatically retry ambiguous generation submissions. The inspected protocol has no job lookup or stream reattachment: a local UUID is not a recoverable remote handle.

### Progress and capabilities

Map actual signposts to backend-neutral events. Do not advertise estimated client progress as authoritative server completion.

Represent verified, unsupported and unknown capabilities distinctly. Keep model inventory separate from evidence that a specific model/input/adapter combination works. Start with one effective app/Bridge generation lane; direct-cloud scheduling policy remains separately configurable and must not assume six entitled slots.

### Direct-cloud authentication and billing

Read the initial API key from the environment and keep credentials/tokens out of logs and ordinary persisted metadata. Implement expiry-aware short-term-token caching and concurrency-safe refresh using the inspected official token contract, plus per-generation authentication and normal TLS hostname validation.

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
| DT+ HTTP mocks | Token acquisition, expiry, concurrent refresh, per-generation authentication and subscription-only request policy | Alongside the cloud adapter |

Reuse the local gRPC server for cloud generation where the inspected wire contract is shared. Assert cloud-specific authorization metadata on the gRPC side, in addition to checking HTTP token/authentication requests. Exercise the actual adapters, not only isolated payload decoders.

Make HTTP/gRPC endpoints and test TLS trust injectable so fixtures stay local without weakening production TLS hostname/certificate validation. Ordinary tests must fail on unexpected external connections; a missing mock must never reach a real app/cloud endpoint.

Keep simulators small and scenario-driven, returning defined protocol fixtures without loading models or recreating Draw Things internals. Simulated cancellation verifies DTFlow's handling, not that production compute stops. Fabricated quota errors verify presentation/application behavior, not actual billing semantics; production classifications require documented responses or sanitized captured fixtures.

Phase 2 covers the contract and adapters. Extend fake-backed UI, durable job, queue and restart-reconciliation tests as those features are introduced in later phases.

## Source-level gaps to validate

- Preserve a requested seed of zero; the inspected client routes it through its seed-provider path.
- Keep chunked output disabled initially or implement validated chunk assembly before enabling it.
- Preserve terminal gRPC errors, including statuses not explicitly raised by the client's error helper.
- Reject empty final output, truncated artifacts and partial-output-plus-error as completed results.
- Keep client media-count/audio-rate heuristics out of authoritative model metadata.
- Make image resizing, mask polarity and any normalization explicit when broader inputs are added.

## Validation and exit criteria

- All three adapters implement the same core contract; importing core does not require Qt or provider protocol types.
- Fake and protocol-fixture tests make no real generation requests and fail on unexpected external connections.
- Real adapters are exercised through local gRPC transport, including terminal status, deadlines, cancellation/disconnects and cloud authorization metadata.
- Request conversion tests cover seed fidelity, explicit parameters and output metadata.
- Stream tests cover terminal failures, no final output, partial outputs and preview labeling.
- Cloud tests cover token expiry, concurrent refresh, per-generation authentication, sanitized errors and disabled PAYG opt-in.
- Document the actual target-Windows packaging outcome; unresolved distribution or packaging decisions stay explicit.
- Record unsupported/unverified cancellation, recovery and compatibility behavior rather than inventing it.

Use `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` for implementation validation. Any live generation is separately gated and requires task authorization; do not use it merely to validate plumbing.

## Deferred work

[Phase 3](phase-3-cli.md) adds the headless CLI, shared application services and durable output/metadata saving; Phase 4 adds the Qt shell and connection management; Phase 5 wires GUI generation through those same services; Phase 6 adds durable jobs/reconciliation; Phase 7 establishes scheduling; Phase 9 verifies advanced image operations; Phase 11 broadens direct-cloud capabilities. MediaGenerationKit remains a protocol reference, with a Swift bridge only if a concrete future capability requires it.

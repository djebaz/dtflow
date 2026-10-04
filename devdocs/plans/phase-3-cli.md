# Phase 3 — Headless CLI and shared application services

**Status:** Planned; no CLI implementation is introduced by this documentation change.  
**Prerequisite:** [Phase 2 backends and offline tests](phase-2-backends.md).  
**Roadmap:** [initial plan](initial.md).

## Goal

Deliver a working `dtflow` command-line application before Qt. The CLI must exercise the real Python backend adapters through application services and provide supported text-to-image generation plus functional, tested durable queues and rolling dispatch before Qt.

Dependency direction: CLI and Qt call shared services; services call core/backend contracts; adapters own provider protocols. CLI parsing, terminal formatting and Qt widgets must not own generation orchestration.

## Delivery sequence

Phase 2A supplies the minimal fake/service/SQLite/artifact slice. Phase 3A expands it into usable real-backend single-generation CLI/services, bounded LoRA selection/preflight, durable attempts, artifacts, logging and diagnostics. Phase 3B extends those same services with durable queues, rolling scheduling, restart reconciliation and custom cloud LoRA upload/reuse. Both gates must pass before Phase 4; full TanqueStudio parity remains later work.

## Release gates and schema compatibility

Follow [the release roadmap](releases.md): Phase 3A targets the private source release 0.1.0; Phase 3B targets 0.2.0. These are proposed versions, not existing releases. Support versioned credential-free generation configuration import/export and metadata reuse in 3A so Qt can add basic save/load without inventing another contract.

Before the 0.2.0 durable release, version saved-state schemas separately from the application version. Preserve records/artifacts through supported atomic migrations, reject unsupported/newer schemas without destructive reset, restore queues paused and never make ambiguous attempts eligible during upgrade. Validate migration failure and previously released supported schemas as they become available.

## Required operations

| Operation | Required behavior |
| --- | --- |
| Backend selection | Explicit fake, Draw Things gRPC or direct DT+ selection; no silent fallback |
| Connectivity | Bounded non-generative transport/authentication check with useful sanitized errors |
| Model inventory | Explicit refresh; retain backend/server provenance and observation time; classify models, LoRAs and controls only from reliable server metadata, leaving unknown files unclassified; distinguish failure, empty inventory and compatibility uncertainty |
| LoRA selection/preflight | Select exact identifiers and explicit weights; inspect metadata and availability; return structured architecture match/mismatch/unknown diagnostics before generation |
| Cloud LoRA assets | Upload supported converted files, handle server deduplication, save durable receipts and reuse identifiers across requests/restarts; local known-upload registry is not remote listing |
| Request resolution | Resolve configuration/preset plus explicit overrides into a reproducible request; validate before submitting |
| Generation | Submit supported text-to-image requests, expose actual progress and collect validated final outputs |
| Output saving | Save all required final images plus reproducibility metadata without silently overwriting useful results |
| Interruption | Stop local waiting/transport as supported, preserve evidence and report remote uncertainty without automatic resubmission |
| Queue control | Persist independent resolved items; list/inspect/start, Pause/Resume/Cancel through shared services; restore paused after restart |
| Rolling dispatch | Refill released slots in eligible queue order; enforce cloud maximum (default four) and separate local one-lane policy; retain uncertain reservations |
| Cache policy | Support `-nc` / `--no-cache`; disable both cache reads and writes when selected |

Use environment-supplied cloud credentials for the initial CLI path. Keep credential resolution Qt-independent so desktop-secured Windows Credential Manager credentials can also be consumed headlessly when that storage path is introduced. Never accept an API key in routine logged request metadata or display credentials/tokens. Environment presence takes precedence over a saved key; an empty/invalid selected credential fails without silent fallback. Use the shared Phase 2 selection rule and log only its source. Keep PAYG opt-in disabled.

## CLI contract

Establish concise command names and stable lowercase short/long option pairs consistent with AGENTS.md. Determine exact subcommands/options during implementation; do not document proposed flags as already available.

Provide accurate help, parameter validation, useful nonzero exit codes and separation of machine-readable results from progress/diagnostic output. Define exit behavior for success, validation failure, remote rejection, interruption and unknown outcomes.

Expose prompt, negative prompt, model, seed, dimensions, steps and guidance where actually supported, plus backend connection selection, output destination and configuration/preset input. Preserve canonical defaults and record the effective request rather than only the entered arguments.

The CLI is initially a source-run development and automation interface; packaging targets the Qt application. A separate packaged CLI is outside the initial scope. Source-run CLI logs use repository-root `logs/`.

The development entry point runs through `uv run dtflow` once implemented. It must not import or initialize Qt, and must remain usable after the desktop interface is added.

## Version and build identity

Use one build-time identity across CLI version output, a discreet Qt About/status area and artifact filenames: application version plus a short Git commit ID. Bundle the full source commit and dirty-worktree status so the executable needs no Git installation at runtime. Release builds must come from a clean checkout; development builds disclose local modifications and missing revision metadata instead of inventing an ID. Persist version/full commit in generation metadata where available.

Manual release downloads are the initial update strategy. Include version and short commit in distributed executable names; any later installer uses the same convention. The version-output option must follow repository CLI conventions and appear accurately in help once implemented. Verify matching identity across packaged filename, CLI and Qt when each delivery path exists.

## Cloud priority and concurrency configuration

Treat direct DT+ as the primary production acceptance path alongside the supported local gRPC path. Offline cloud authentication/transport coverage and separately authorized deployed-cloud checks remain distinct from frequent local gRPC smoke tests.

Expose a validated positive-integer cloud concurrency maximum in the CLI from this phase, default **4**, and persist it in shared configuration for Qt. Select the actual option spelling during implementation and keep help accurate. This is a user-selected maximum, not verified cloud entitlement. Phase 3B must execute and test rolling concurrent queues through the CLI before Qt. Phase 7 adds optional advanced scheduling. Keep local gRPC scheduling policy separate.

Qt exposes the same setting and uses a subtle color change above **2**, without a prominent warning or confirmation dialog.

## Shared logging and development mode

Implement the repository logging contract through one shared standard-library Python logging configuration. Use repository-root `logs/` for development and `%LOCALAPPDATA%\DTFlow\logs\` for packaged Windows builds; support an explicit destination override. Neither packaged path resolution nor logging setup may require Git or Qt.

Normal mode logs important events/errors; explicit development mode adds sanitized diagnostic detail and tracebacks. Determine canonical CLI spellings during implementation and keep help accurate. Development mode changes logging only; no generation, retry, network or billing semantics change.

Use rotating UTF-8 text files with bounded retention, timestamps, severity/component, version/commit/session and job/attempt correlation where available. Handle simultaneous-process file naming/rotation safely, keep Qt responsive and report write failures without leaking secrets. Exclude credentials, tokens, headers, raw image payloads and full prompts by default; sanitize exception paths/messages too.

Logs remain separate from durable execution records and disposable cache; `--no-cache` does not disable logging. Extend offline acceptance with destination selection, rotation bounds, level selection, correlation and secret-redaction checks, including simulated write failure. Do not submit generation merely to test logging.

## Shared services and persistence boundary

Services own request resolution/preflight, backend selection, submission, events, result validation and artifact saving. Keep services callable from Python as well as the CLI and future Qt UI.

Persist resolved prompts/seeds, model/checkpoint and relevant adapter identifiers, effective generation parameters, backend identity, dependency/version information available at runtime, output references and completion evidence. Require atomic execution-record updates and preserve partial evidence after failures.

A successful terminal RPC and valid outputs establish backend success; successful durable output/metadata saving establishes application success. Saved previews after failed generation remain labeled preview/diagnostic artifacts and never count as completed results.

### Minimal durable execution record

Before any generation submission, atomically persist an immutable resolved request plus a unique attempt ID, backend identity and credential-free configuration/version metadata. Before crossing the submission boundary, durably mark the attempt as potentially executing remotely. If either write fails, do not submit.

Use the standard-library SQLite store selected in the [backend contract](../architecture/backend-contract.md), with WAL, synchronous=FULL, short transactions, independent schema versions and a verified patched runtime. Canonical execution records are transactional; JSON is for credential-free configuration/metadata interchange, not dispatch ownership. Save image/sidecar files through same-directory temporary-file replacement with appropriate flush/fsync behavior before committing successful artifact references. Database and filesystem writes are not jointly atomic; reconcile orphaned/partial files conservatively. Define and test durability on the target filesystem.

Update outcomes and artifact references atomically as evidence arrives. Distinguish definitely not submitted, potentially executing/outcome unknown, confirmed failure and successful output persistence. Never persist credentials or bearer tokens.

On startup/record inspection, unfinished attempts that crossed the boundary remain unknown and cannot be automatically resubmitted. A crash between recording potential execution and sending the RPC may conservatively leave an unknown outcome; this is intentional. No remote job ID exists in the inspected contract to resolve it.

Test crashes/failures before recording, after marking potential execution, during generation and between artifact saving and outcome commit. Previously saved artifacts remain evidence, not automatic proof of full completion.

Phase 3A completes the initial Phase 2A durable attempt/artifact foundation for the supported real backends. Phase 3B extends them with transactional queue/job state, rolling scheduling and local restart reconciliation. Do not claim remote recovery: the inspected protocol has no generation status/reattachment operation.

## Phase 3B — Durable queues, rolling execution and cloud assets

Persist queue identity, stable ordered item IDs, immutable resolved requests, item/attempt states, backend selection and concurrency configuration. Use one dispatch owner per application state directory so two CLI processes cannot independently dispatch the same queue or bypass its cap. The CLI presents queue control; shared services own persistence, eligibility, scheduling and reconciliation. Store control requests transactionally for the dispatcher to poll, so basic Pause/Resume/Cancel needs no separate IPC server. SQLite transactions alone do not establish dispatch ownership: implement and test exclusive ownership, stale-owner recovery and protection against an old owner continuing dispatch. Never hold a database transaction while generating.

For ten independent eligible cloud items and a maximum of four, submit items 1–4. As any attempt reaches a known terminal outcome and releases its reservation, start the next eligible item immediately, without a group-completion barrier. Preserve original item identity and queue order in result presentation even when completion order differs. Reserve slots before remote submission and bound active attempts plus unresolved reservations. The tested local app/Bridge policy is one lane.

Pause stops new dispatch and lets active attempts finish. Resume starts only eligible never-submitted items, subject to any backend uncertainty block. Cancel prevents submission of pending selected items; for running attempts it requests interruption only as supported and must not imply confirmed remote cancellation. Completed artifacts remain preserved.

A definitely unsubmitted failure releases its slot. If remote execution remains possible after disconnect/local cancellation, retain an unresolved reservation and pause new dispatch for that backend while other active requests finish. An explicit acknowledged override may resume eligible work but cannot replay or mark the uncertain attempt resolved. Record the override; actual remote concurrency is no longer guaranteed after overriding unknown work.

After restart, restore the queue paused, retain completed results and unresolved reservations, and exclude ambiguous attempts from replay. Ordinary Resume cannot silently clear an uncertainty block. Changing the maximum downward does not cancel active requests; withhold new dispatch until reservation count is below the new cap.

Phase 6 adds pair/cross-product batch planning; Phase 7 adds advanced scheduling. Neither is required for this basic rolling execution path.

## LoRA scope before Qt

Follow [LoRA compatibility and cloud asset plan](lora-assets.md). Phase 3A implements structured local model/LoRA discovery, selection/weights and shared preflight for a bounded verified text-to-image scope. Phase 3B adds the separate direct-cloud upload/receipt/reuse path and validates using an uploaded custom LoRA in supported generation. Both are pre-Qt gates; do not leave cloud upload in Phase 11 by default.

Accept already converted Draw Things LoRA files initially. Audit required metadata and cloud generation references before committing the supported subset. Arbitrary safetensors conversion and a LoRA editor are separate work. Reuse server-acknowledged uploads without transferring weights for every generation or queue item; keep immutable asset references in resolved requests and revalidate metadata before dispatch.

Expose receipt/known-upload inspection without pretending it lists the complete cloud account. The inspected SDK has no usable remote list operation; upload responses supply no delete key. Remote listing/deletion and authoritative storage balance remain unavailable until verified. Unknown upload outcomes require explicit reconciliation, not automatic transfer retries or success claims.

## Testing and acceptance

- Headless imports and command startup work without Qt.
- Help and every documented option match the implementation.
- Fake-backed CLI commands cover success, failure, interruption and uncertain outcomes.
- Real adapters execute against local gRPC/HTTP fixtures through the CLI/service path, including connectivity, inventory, authentication and generation.
- Tests assert saved output metadata, seed-zero fidelity, supported sampler/enum and pixel-to-wire dimension mappings, defaults and the effective encoded parameters; unsupported options fail validation before submission.
- Tests assert multiple final outputs where supported, empty-output rejection and preview labeling.
- Crash-boundary tests prove the request/attempt record precedes submission, failed persistence prevents submission and unfinished potentially submitted attempts cannot be automatically retried.
- Phase 3B CLI acceptance covers durable queue creation/inspection and ten independent items with a four-slot cap: controllable out-of-order completion proves item 5 starts before all initial four finish, continuous refill stays bounded and results retain item identity.
- Cover Pause/Resume, pending cancellation, running interruption, pre-submission rejection, uncertain post-submission loss, blocked replacement dispatch and explicit override through shared services/CLI.
- Restart tests restore paused queues, completed outputs and unresolved reservations, never replay ambiguous attempts, and require explicit action before eligible work resumes.
- Cover one local lane, changing concurrency downward and exclusive dispatch ownership. Use deterministic fake timing and focused real-adapter protocol fixtures, with no external generation.
- Discovery tests cover explicit refresh, backend provenance, reliable model/LoRA/control classification and unclassified files.
- Phase 3A LoRA fixtures cover metadata, exact identifiers/weights, match/mismatch/unknown diagnostics, official exception/refiner rules in supported scope, config round trips and rejection before generation of confirmed incompatible selections.
- Phase 3B upload fixtures exercise actual shared services/CLI: metadata POST, signed PUT, already-existing response without PUT, checksum/size/identifier fidelity, storage/auth failures, secret/signed-URL redaction, ambiguous transfer and failed durable receipt writes.
- Cover repeated use and restart without repeated weight transfers, changed file content, backend/account separation, stale receipts, queue references and absent remote listing/delete support. A receipt is not proof of perpetual remote availability or LoRA application.
- Record separately authorized deployed-cloud upload plus generation evidence for the advertised subset; mocks and successful plain text-to-image runs do not establish custom LoRA use.
- An unexpected external connection fails ordinary tests; no subscription credits are consumed by fixture coverage.
- Cloud subscription-only policy, token refresh and sanitized failures remain covered.
- Validate syntax/imports, parsing/help, `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .`.
- Record any actual Windows smoke-test result without inferring packaging success from source execution.

Explicitly authorized live checks can verify deployed behavior after offline coverage; they are not required merely to test CLI plumbing.

## Scope and later expansion

“Complete CLI” means an end-to-end application for supported operations, not all TanqueStudio features in one phase. Pair/cross-product queue matrix expansion, advanced scheduling, workflows, advanced image/control combinations, video, arbitrary LoRA conversion and broader asset management remain later work. Bounded LoRA selection and custom cloud LoRA upload/reuse are required in this CLI phase after contract verification.

Track the intended port in the [TanqueStudio parity matrix](tanquestudio-parity.md); later work requires an explicit milestone rather than indefinite deferral. This matrix is not a requirement to expose every feature before Qt.

Model discovery and bounded LoRA selection/weights arrive in Phase 3A. Custom cloud upload/reuse arrives in Phase 3B through a separate asset service with the same credential policy. Phase 5A exposes those existing services in Qt. If format or deployed API evidence blocks the agreed subset, record the blocker explicitly; do not silently move it behind Qt or advertise it as usable.

Phase 4 adds the minimal Qt shell and connection management after both the single-generation and rolling-queue CLI gates pass. Phase 5A adds GUI generation/configuration reuse and Phase 5B exposes the existing queue controls, both through these services and both required for the first desktop release. Later features should expose the same underlying services through the CLI wherever practical so Qt remains presentation rather than a second execution engine.

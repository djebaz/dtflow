# DTFlow Initial Development Plan

**Status:** Planning — backend and CLI-first sequence  
**Product:** DTFlow (DTF)  
**Repository:** `djebaz/dtflow`

## 1. Product vision

DTFlow is a Windows desktop application for workflows, generation, and automation around Draw Things.

Direct DT+ Cloud Compute is the primary expected production backend. Local Draw Things gRPC remains a supported runtime backend and the frequent development and smoke-test path. The application should provide a durable desktop layer for connections, generation jobs, queues, presets, galleries, and eventually multi-step workflows without coupling the user interface to Draw Things protocol details.

Phase 2 will introduce Draw Things gRPC, a narrow direct DT+ Cloud Compute backend, and an explicitly selected fake backend behind one application contract. Broader cloud features remain later work.

## 2. Engineering principles

- Keep UI, orchestration, generation models, persistence, and backend integrations separate.
- Keep the core backend-neutral.
- Do not expose protobuf or gRPC types outside the Draw Things backend boundary.
- Preserve reproducibility: prompts, seeds, model/checkpoint identifiers, LoRA settings, generation parameters, outputs, and job state should be persisted where relevant.
- Prefer resumable and inspectable long-running workflows.
- Do not assume model, LoRA, quantization, scheduler, or runtime compatibility without evidence.
- Do not run expensive or billable generation merely to validate application plumbing.
- Keep the project Python-first. Node.js is not currently required.
- Use Astral `uv` as the canonical project/environment/dependency workflow from the beginning; do not maintain a parallel `requirements.txt` unless a concrete compatibility requirement appears.

## 3. Technology decisions

### Desktop UI

- Python
- PySide6
- Qt Widgets
- Qt Designer for editable presentation/layout sources
- Canonical Designer files are `.ui` files.
- Business logic and backend access must not be embedded in generated Designer Python code.
- Complex components such as the gallery and workflow editor can be custom PySide6 widgets inserted into Designer-managed layouts.

### Initial generation backends

The initial real backends are Draw Things over gRPC and direct DT+ Cloud Compute. A deterministic fake backend supports offline development and tests.

The Python implementation may use `drawthings-py` where it is useful, while the public Draw Things gRPC/protobuf contract remains the backend boundary.

Before implementation, verify the exact current APIs and compatibility against the canonical Draw Things interfaces and the selected Python dependency.

### Direct cloud direction and future backends

The architecture must allow additional generation backends without changing UI-facing generation models.

Phase 1 diagnostic work has now proven that DT+ Cloud Compute is reachable directly from Python without a MediaGenerationKit runtime dependency. The validated path uses the official API-key flow to obtain a short-term token, performs per-generation authentication, connects directly to the DT+ gRPC endpoint over TLS, and receives normal generation results.

Backend direction is therefore:

- DT+ Cloud Compute through a native Python backend built from the validated direct-cloud path
- MediaGenerationKit as an architectural and protocol reference rather than a required runtime dependency
- a lightweight Swift/MediaGenerationKit bridge only as a fallback if a future DT+ capability cannot be reproduced reliably in Python
- a separate experimental native Windows Draw Things gRPC server

Phase 2 brings a narrow text-to-image direct DT+ adapter forward from Phase 11 and defines inventory/LoRA preflight plus separate asset-storage contracts. Phase 3A delivers bounded LoRA selection; Phase 3B delivers custom cloud upload/reuse before Qt under the [LoRA compatibility and cloud asset plan](lora-assets.md). The existing probe remains diagnostic tooling; the adapter must explicitly handle token expiry, authenticated transport, subscription-only policy, sanitized failures and uncertain outcomes. Unsupported cancellation, remote recovery and input combinations must remain unavailable or unknown. Phase 11 broadens cloud capabilities and hardening.

## 4. Architectural boundaries

The intended dependency direction is:

```text
PySide6 / Qt Designer
        |
        v
     Services
        |
        v
   DTFlow Core
        |
        +----------------------+
        |                      |
        v                      v
Draw Things gRPC          DT+ direct
    backend                 backend
        |                      |
        v                      v
   Draw Things        DT+ Cloud Compute
```

### Core

The core owns backend-neutral application concepts such as:

- generation requests
- generation results
- progress
- jobs
- workflows
- presets and reusable generation configuration
- errors and state transitions

The core must not depend on Qt, protobuf, gRPC, or a specific remote service.

### Backend abstraction

A `GenerationBackend`-style contract will isolate generation providers.

The first working slice uses an explicitly selected fake backend; real Draw Things gRPC and direct-cloud adapters follow behind the same tested contract. Protocol conversion, generated protobuf types, connection details, and Draw Things-specific mappings remain inside that backend package.

The Phase 2 DT+ backend will use the validated Python direct-cloud path, but the diagnostic probe must not simply become production code by relocation. Authentication/token lifecycle, credential storage, entitlement/PAYG rules, retries, cancellation, throttling, error mapping, persistence, model/file compatibility, and safe concurrency must be made explicit behind the backend boundary first. MediaGenerationKit remains a reference for protocol behavior, not a required DTFlow runtime dependency for the path already proven.

### Services

Services orchestrate application use cases, including:

- connection management
- model/LoRA inventory and compatibility preflight
- separate cloud LoRA upload, durable receipts and reuse
- generation submission
- job lifecycle
- queue execution
- presets
- later workflow execution

The main window should not become the application controller.

### Persistence

Persistence should eventually cover:

- application settings
- saved connections
- jobs and durable job state
- presets
- workflow definitions
- generation metadata
- output references
- versioned cloud asset receipts and their backend/account scope, without credentials or signed URLs

Use standard-library SQLite for canonical durable execution state, starting with the Phase 2A attempt slice and extending it in Phase 3. WAL, synchronous=FULL, a patched bundled SQLite runtime and local-filesystem storage follow the [backend contract](../architecture/backend-contract.md). Keep file artifacts separate and reconcile crash boundaries; JSON remains an interchange format rather than the execution store. Schema, migration and dispatch-ownership details are validated during implementation.

## 5. Repository direction

The repository starts with the main architectural directories present up front so the intended ownership boundaries are visible from the beginning. Empty placeholder directories are tracked with `.gitkeep` until their first real files are added.

Initial repository areas:

```text
devdocs/
  plans/
  architecture/
  research/
  decisions/
  releases/

src/dtflow/
  core/
  backends/
  services/
  persistence/
  ui/
  resources/

tests/
  unit/
  integration/

tools/
  drawthings/
```

Existing or future Draw Things helper scripts belong under tooling rather than the Python runtime core.

## 6. Repository conventions

- `devdocs/plans/initial.md` is the canonical initial roadmap.
- `devdocs/releases/unreleased.md` tracks pending changes for the next release; a release moves those entries to `devdocs/releases/v<version>.md` and resets `unreleased.md`.
- The initial architectural directories are created up front and may use `.gitkeep` only while otherwise empty.
- LF is the default repository line ending.
- Native Windows scripts may use CRLF.
- `tmp/` is an intentionally ignored local scratch workspace.
- Local IDE and coding-agent state is ignored.
- Qt Designer `.ui` and protobuf `.proto` sources are tracked.
- Generated Qt Python and generated protobuf Python are ignored initially.
- Model/checkpoint artifacts are not committed.
- Git LFS is not required initially.
- `pyproject.toml` is the canonical Python project/dependency configuration and `uv.lock` is tracked for reproducible development and builds.
- Use `uv sync` for environment setup/synchronization and `uv run` for project commands and tools.
- Do not maintain a parallel hand-edited `requirements.txt`; add an exported compatibility file only if a concrete external integration requires it.
- Development runtime state lives under repository-root `.dtflow/`, which is ignored by Git.
- Development diagnostic logs live under repository-root `logs/`, already ignored; `tmp/` remains disposable scratch. Packaged Windows logs default to `%LOCALAPPDATA%\DTFlow\logs\`, with an explicit location override when implemented.
- Packaged Windows builds use the platform-local application data location by default rather than writing beside the executable; an explicit DTFlow home override may be added for development/portable use.
- Cache is disposable and must remain separate from durable application state. Cache storage should be split into type-specific subdirectories so categories can be measured and purged independently.
- Cache size visibility and manual/automatic cleanup should be implemented early because generated images and derived assets can become large.

## 7. Implementation phases

Use the [release roadmap](releases.md) alongside these engineering phases. It defines usable version milestones, evidence gates and saved-state compatibility; proposed versions are not existing releases.

Track the intended full TanqueStudio port in the [parity matrix](tanquestudio-parity.md). It assigns feature evidence, shared services, CLI/Qt exposure, backend support, milestones and acceptance. Complete the upstream feature inventory during Phase 2; unknown or deferred items remain explicitly tracked. Full parity is not a prerequisite for starting Qt.

### Phase 1 — gRPC connectivity proof

**Handoff status:** Core connectivity feasibility is established by the [recorded probe evidence](../research/drawthings-grpc-proof.md): the tested Windows stack connects/discovers files, generates, decodes and saves images; user-reported controlled runs also exercise direct-cloud authentication/generation and concurrency. These are existing observations, not tests run by this documentation PR. The probe remains diagnostic tooling, not an application adapter.

The original checklist below is not fully verified: confirmed remote cancellation, detailed progress/error cases and wider compatibility remain open. Carry them into Phase 2 source/fixture work and separately authorized live validation where needed; unsupported behavior stays explicit. These gaps do not block starting 2A, and no new generation is required merely to repeat the feasibility proof.

Prove the smallest useful Draw Things connection outside the GUI.

Validate:

- connection configuration
- protocol/API compatibility
- one generation request
- progress/result behavior
- error behavior
- cancellation capabilities, if exposed
- concurrency behavior

Keep prompts, seeds, dimensions, and parameters controlled when comparing behavior.

### Phase 2 — Backend contract and initial adapters

Follow [the Phase 2 implementation plan](phase-2-backends.md), [backend/async contract](../architecture/backend-contract.md), [pinned cloud protocol](../architecture/cloud-protocol.md) and [source-backed ecosystem research](../research/dtflow-ecosystem-research-report.md).

Deliver coherent slices: 2A contract/async decisions, fake, foundational fixtures and a thin headless service/SQLite-attempt/saved-image path; 2B local gRPC adapter with connectivity/inventory and transport fixtures; 2C direct-cloud adapter against the pinned auth contract; 2D packaging/licensing and version-pinned upstream feature inventory alongside implementation. Phase 2D does not block starting 2A; licensing informs dependency investment and target-Windows verification remains a packaged-release gate.

Introduce backend-neutral request, result, progress, error and capability types. Implement a deterministic fake backend, wrap the validated Draw Things gRPC path, and add narrow direct DT+ text-to-image support through native Python. Define structured model/LoRA metadata, shared compatibility preflight and a separate cloud asset contract for Phase 3; audit converted-format and generation-reference requirements.

Define completion and uncertain-outcome semantics before wiring the UI. Preserve previews and incomplete outputs as explicitly labeled diagnostic/partial artifacts, never completed results. Ordinary tests must stay offline.

Phase 2 implements the credential interface, environment provider and fake saved provider only; real Windows Credential Manager storage remains Phase 4. Async adapters/services own no event loops; CLI and later Qt worker own execution and bounded cancellation.

Investigate portable Windows packaging and dependency distribution in the parallel Phase 2D track on the owner's Windows 10 / Intel Pentium E6500 machine; keep an installer option if required. Source execution is recorded as working; a single-executable build is not yet verified. Basic durable queues and rolling orchestration are required in Phase 3B before Qt.

### Phase 3 — Headless CLI and shared application services

Deliver a usable source-run command-line application before introducing Qt. Packaging initially targets the Qt application; a separate packaged CLI is outside scope. Follow [the Phase 3 CLI plan](phase-3-cli.md).

Introduce shared logging with rotating UTF-8 files, normal and explicit development detail levels, build/session/job correlation and secret sanitization. Development logs use repository-root `logs/`; packaged Windows builds use per-user application data. Logging is independent of cache bypass and durable execution records.

Expose the `dtflow` entry point for backend selection, non-generative connectivity checks, model inventory with explicit refresh and backend provenance, and complete text-to-image generation through fake, Draw Things gRPC and direct DT+ adapters.

Implement application services for request resolution, submission, progress/error handling, a durable atomic request/attempt record before submission, and output/reproducibility metadata saving. Persist potential remote execution before crossing the submission boundary; incomplete attempts cannot be automatically resubmitted. The CLI owns argument parsing and presentation only. Neither services nor the headless command path may require Qt.

Handle interruption and uncertain outcomes honestly; do not automatically resubmit ambiguous requests. Preserve previews as diagnostic artifacts. Keep subscription-only cloud behavior and the `-nc` / `--no-cache` contract.

Validate command parsing, help, exit behavior, saved metadata and end-to-end fixture-backed commands offline. Real generation remains explicit and separately authorized. A complete CLI means a usable path for the supported scope, not every advanced backend operation.

#### Phase 3A — Single-generation foundation

Complete backend selection, connectivity, structured model/LoRA inventory, bounded LoRA selection with explicit weights and preflight, request resolution, durable attempts, output saving, logging and truthful interruption through the headless CLI. Preserve architecture-match, mismatch and unknown states separately from availability or confirmed application; use official rules and no filename-based compatibility guesses.

#### Phase 3B — Durable queue, rolling dispatch and cloud assets

Extend the same services with persistent queue items and lifecycle state, queue inspection, Pause/Resume/Cancel, and rolling scheduling. Also deliver a separate custom cloud LoRA upload/reuse service through the CLI: supported converted files, SHA-256/size-based negotiation, signed transfer when needed, durable receipts and reuse across requests/restarts. The inspected API has no usable remote listing; local receipt inspection is not complete account inventory. Validate the upload-to-generation reference path for the advertised subset before Qt. Cloud concurrency defaults to four and is configurable; the tested app/Bridge path remains one effective lane. Restore queues paused after restart, never replay ambiguous attempts, and test the ten-item/four-slot scenario through CLI commands before Qt.

Current Phase 1 evidence supports treating one tested Draw Things gRPC server as one effective generation lane: concurrent client RPCs can be in flight together, but the tested iPhone/Bridge path completes work in an effectively serialized staircase and is slower than the matching serial baseline.

The direct DT+ diagnostic path is a separate result. It has completed six controlled cloud requests concurrently within one broad generation-time window, but that does not establish a fixed cloud concurrency limit or production scheduling policy. Do not transfer direct-cloud concurrency assumptions to the Draw Things backend.

Use the shared configurable cloud maximum, default four, in both CLI and Qt scheduling. Values above two change the Qt number's color subtly, without a prominent warning or confirmation dialog. Preserve a separate app/Bridge policy. Test bounded dispatch and throttling without automatically retrying ambiguous submissions.

Do not infer safe concurrency from client-side capability alone.

#### Rolling queue dispatch

Use a rolling scheduler rather than groups separated by a completion barrier. For ten independent eligible cloud items and a maximum of four, start items 1–4. As soon as any attempt reaches a known terminal outcome and releases its reservation, dispatch item 5 without waiting for the other three; refill each available slot with the next eligible item in queue order until the queue is drained.

Completion order may differ from submission order. Keep outputs, errors and metadata attached to stable original item IDs and preserve queue order in presentation. Respect dependencies and queue pause/cancel state; a freed slot does not bypass eligibility or a backend dispatch block. Reserve slots before submission so simultaneous completions cannot oversubscribe the configured maximum.

For normal cloud scheduling, active attempts plus unresolved reservations must not exceed the configured maximum. The tested local Draw Things/Bridge backend uses its separate one-generation policy, regardless of the cloud maximum. A disconnect or local cancellation with possible remote execution does not free a slot; apply the uncertainty rules below.

Phase 3B fixture acceptance includes a ten-item/four-slot scenario with deliberately out-of-order completions: item 5 starts while other initial items remain active, rolling refill stays bounded, results retain item identity, and an uncertain attempt blocks replacement dispatch.

#### Uncertain attempts and slot accounting

If submission definitely never occurred, release the scheduler slot. If a disconnect, local cancellation or other failure leaves remote execution possible, retain a durable unresolved reservation and pause new dispatch for that backend; already-running requests may finish. Release a reservation only on evidence that remote execution is no longer possible or an explicit user override.

An explicit override may resume eligible never-submitted work after the user acknowledges the unresolved attempt; it must not resubmit that attempt or mark it resolved. Record the override and preserve its uncertainty. The configured maximum bounds DTFlow scheduling, not guaranteed remote execution after an override: no inspected remote status API can establish how much unknown work remains active.

After restart, restore the queue paused with unresolved reservations intact. Ordinary Resume does not clear those reservations or bypass their dispatch block; the user must explicitly acknowledge and override that block. Test pre-submission slot release, post-submission disconnect/local cancellation, completion of other running requests, blocked replacement dispatch, explicit override and restart restoration in Phase 3B. Phase 3A supplies durable attempt evidence; Phase 3B adds the scheduler before Qt.

### Phase 4 — Minimal Qt shell and connection management

Create the PySide6 application shell and canonical Qt Designer sources after both Phase 3A and Phase 3B CLI/service acceptance gates pass.

Wire startup, main window, UI/resources, background execution and clean shutdown. Add Draw Things/cloud connection selection and useful status/failure presentation through the same services exercised by the CLI.

Add secure API-key entry and Windows Credential Manager storage through shared credential services, retaining environment input for headless use. Show version and short commit discreetly in About/status using the same bundled identity as the CLI and packaged filename.

Keep business logic and backend protocol code outside UI classes. Network and generation work must not block the Qt event loop. CLI execution must continue working without importing Qt.

### Phase 5 — Desktop generation and queue controls

Connect the desktop UI to the generation services already exercised by the headless CLI for a complete single-generation flow. Reuse request resolution, progress/error handling and output persistence rather than creating a second execution path. Expose the bounded LoRA selection/preflight and custom cloud upload/reuse services already exercised by the Phase 3 CLI; Qt adds presentation rather than the first implementation of those behaviors.

#### Phase 5A — Generation and configuration reuse

Expose the generation form, actual progress, outputs/errors, bounded LoRA selection/weights, compatibility diagnostics and cloud upload/known-receipt reuse through existing shared services. Save/load credential-free generation configurations through the versioned shared contract from Phase 3A; the full preset browser remains Phase 8.

#### Phase 5B — Desktop queue controls

Expose queue creation/inspection, stable item status, concurrency, Pause/Resume/Cancel, paused restart and explicit uncertainty handling through the existing Phase 3B services. The first desktop release requires both 5A and 5B; queue controls are not deferred to batch planning or advanced scheduling.

Persist enough metadata to reproduce the request. Mark a job completed only after successful terminal RPC status, validated final artifacts and durable output/metadata persistence. Saved previews must remain labeled as previews or diagnostics when final generation fails.

### Phase 6 — Queue planning and batch expansion

Extend the Phase 3B durable queue with bounded pair/cross-product planning, dry-run inspection and resolved request expansion. Reuse the existing scheduler and reconciliation; basic queue execution is already required before Qt.

### Phase 7 — Advanced scheduling

Extend the Phase 3B rolling scheduler only where product needs justify priorities, fairness across queues or evidence-based adaptive concurrency/throttling. Preserve existing caps, unresolved reservations and no-replay semantics. This phase is not the introduction of concurrent execution.

### Phase 8 — Gallery and presets

Add output browsing, generation metadata inspection, reusable presets, and regeneration/reuse flows.

### Phase 9 — Advanced image operations

Deliver bounded slices: 9A verified image-to-image, 9B inpainting with existing mask files, and 9C interactive mask painting plus selected controls/reference combinations. Release useful slices independently; do not wait for every model/control combination.

### Phase 10 — Workflows

Build multi-step workflow composition/execution on top of the durable job and backend layers. Phase 10A delivers versioned workflow-file validation, plan inspection and execution for a bounded supported step set. Phase 10B adds visual authoring and richer verified semantics incrementally.

Workflow execution should remain resumable and inspectable within documented remote recovery limits. File-based execution does not wait for the full visual editor.

### Phase 11 — Broader DT+ capabilities and hardening

Phase 1 diagnostic work has already established that a direct Python DT+ implementation is viable. The live-tested probe has exercised the API-key short-term-token flow, per-generation authentication, direct TLS gRPC generation against DT+ Cloud Compute, image-result decoding, and concurrent submissions without requiring MediaGenerationKit as a runtime component.

Phase 2 introduces the narrow direct Python backend; Phase 3 already delivers bounded LoRA selection and custom cloud upload/reuse. Phase 11 broadens its verified operations and production behavior rather than re-evaluating Python feasibility or delaying basic cloud integration.

### Foundations required by the first adapter

Phase 2 already requires expiry-aware token acquisition/refresh, secret handling, authenticated TLS transport, subscription-only billing policy, sanitized error mapping and safe ambiguous-outcome behavior. Phase 3 requires durable pre-submission attempt records. These protections are prerequisites for initial cloud use, not deferred Phase 11 work.

### Expansion of established foundations

Phase 11 extends and revalidates those foundations for additional verified operations, credentials/account flows, entitlement cases and scheduling behavior. It does not postpone their initial implementation.

For broader cloud support:

- preserve the established backend boundary while adding cloud capabilities; never import diagnostic tooling into application orchestration;
- extend the established token lifecycle and credential handling for any additional account/credential flows;
- preserve the rule that credentials, bearer tokens, authorization headers, and sensitive payloads are never logged or persisted accidentally;
- define entitlement, subscription quota, PAYG opt-in, and billing-failure behavior so DTFlow never silently enables paid usage;
- verify model/checkpoint, LoRA, input-image, and other request compatibility needed by the product;
- map progress, cancellation, retry, timeout, throttle, and remote error semantics into backend-neutral DTFlow states;
- make jobs durable and recovery-safe across process restart where the remote protocol permits it;
- establish a production concurrency policy from measured behavior and explicit server responses rather than assuming the diagnostic six-job result is a fixed entitlement or safe ceiling;
- add mocked protocol coverage and separately gated live integration checks that cannot submit billable generation accidentally.

Keep MediaGenerationKit as a protocol/reference implementation. Introduce a Swift/MediaGenerationKit bridge only if a required future capability cannot be implemented reliably through the validated Python path.

## 8. Validation strategy

Early validation should favor cheap, focused checks:

- syntax/import checks
- configuration parsing
- CLI/help checks where applicable
- unit tests for backend-neutral logic using `pytest`
- Qt-focused tests with `pytest-qt` when they provide useful coverage
- local gRPC transport tests through the actual adapters, including terminal status, deadlines and cancellation/disconnects
- DT+ HTTP authentication mocks plus cloud authorization metadata assertions on the local gRPC server
- an offline network guard that fails on unexpected external connections
- focused integration tests against Draw Things when required

Real generation should only be run when it validates behavior that cannot be established without inference. Ordinary unit/test-suite execution must never submit a real generation accidentally; live Draw Things integration tests must be explicit and separately gated.

## 9. Product decisions and remaining open questions

### Confirmed product decisions

- Initial audience: personal use by the owner only. Additional testers and public releases may follow later.
- Target machine: the packaged application must run on the owner's legacy Windows 10 machine with an Intel Pentium E6500. Packaged compatibility remains to be verified on that machine.
- Distribution: private initially; the repository may be opened and public releases offered much later if the application works well. Resolve dependency licensing implications before public distribution.
- Packaging preference: portable executable first; retain an installer option if required. Portable packaging does not imply writing durable state beside the executable.
- Restart behavior: restore the queue in a paused state. Nothing resumes automatically; the user can resume eligible queued jobs or cancel them. Interrupted attempts that may already have been submitted remain outcome unknown and cannot be resubmitted by resuming the queue.
- Cloud concurrency: expose a configurable maximum in the CLI and Qt, with a default of **4**. In Qt, values above **2** receive a subtle color change rather than a prominent warning or confirmation dialog. This is a user-selected scheduling policy, not a verified service entitlement or guaranteed capacity.
- Backend priority: direct DT+ cloud is the primary expected production backend. Local Draw Things gRPC remains a supported runtime backend and the frequent development/smoke-test path. Local smoke tests do not establish cloud readiness.

Apply the Windows packaging requirement during Phase 2/3 investigation. Phase 3B implements paused restart and bounded rolling queue execution through the CLI; Qt exposes those same services. Phase 7 is optional advanced scheduling, not a prerequisite for basic concurrency.

### Additional confirmed product decisions

- Cloud credentials: enter the API key in Qt and save it securely through Windows Credential Manager. When an environment key is present it takes precedence, including failure for an empty/invalid value; otherwise use the saved key. Never silently switch sources after authentication failure. Keep environment-supplied credentials available for CLI use. Credential resolution belongs in shared services and must not require Qt; keys/tokens never enter ordinary settings, output metadata or logs. Secure storage failure must not silently fall back to plaintext.
- LoRA delivery: bounded identifier/weight selection and compatibility preflight must work in Phase 3A; supported custom cloud LoRA upload/reuse must work in Phase 3B before Qt. Prefer upload once then reuse when the server acknowledges existing content, without assuming indefinite retention. Start with converted Draw Things files; broad conversion/editing and unverified remote listing/deletion are later scope.
- PAYG: unavailable in the first release; defer opt-in support to a later version. Initial requests remain explicitly subscription-only regardless of account PAYG settings.
- Updates: manual downloads initially, with no integrated updater or Microsoft Store requirement. A future release-page check is optional later work.
- Build identity: show the application version plus a short Git commit ID in CLI version output and a discreet Qt About/status area, and include both in distributed build filenames. Bundle the full source commit and dirty-worktree status so the executable needs no Git installation at runtime. Release builds must come from a clean checkout; development builds disclose local modifications and missing revision metadata instead of inventing an ID. Persist version/full commit in generation metadata where available.

Illustrative naming only (not an existing release): `dtflow-0.1.0-gabcdef1-windows-x64.exe`. Use the actual target architecture and source revision; an eventual installer follows the same identity convention.

### Remaining engineering investigations

Source-inspected answers and remaining evidence requirements are recorded in [research Section 10](../research/dtflow-ecosystem-research-report.md#10-follow-up-findings-on-the-eight-backend-questions). See the [Phase 2 plan](phase-2-backends.md) for implementation gates.

- Exact `drawthings-py` version/API and how much of the gRPC client it should own.
- Whether DTFlow should generate protobuf bindings itself or consume them through a dependency.
- Exact Draw Things progress and cancellation semantics, plus whether any server/app configuration can expose parallel backend execution beyond the effective single lane observed in the tested iPhone/Bridge path.
- SQLite schema, migrations, busy/ownership policy and crash reconciliation, including durable upload receipts, uncertain upload reconciliation and backend/account-scoped asset references; the storage technology is selected.
- Custom cloud LoRA converted-format/metadata requirements, deduplication identity, generation references, retention/availability evidence and deployed upload/use behavior; remote listing/delete-key discovery is absent in the inspected SDK.
- Windows packaging feasibility on the target machine, native-module and certificate resources, and the distribution implications of the GPL-3.0-only drawthings-py dependency in the currently MIT-declared project.
- Production DT+ authentication lifecycle, failure behavior, entitlement/PAYG semantics, persistence requirements, model/file compatibility, cancellation/retry semantics, throttling and behavior at the selected concurrency. The diagnostic API-key token/auth/gRPC path is proven separately; the default of four is a product choice.
- Whether any future DT+ capability actually requires MediaGenerationKit or a Swift bridge. No runtime dependency on MediaGenerationKit has been demonstrated for the currently proven direct generation path.

## 10. Initial repository setup

The first setup PR is intentionally small:

- update `.gitignore`
- add `.gitattributes`
- add this initial plan
- add `AGENTS.md` with the repository contract
- add `devdocs/releases/unreleased.md`
- create the initial architectural directory skeleton with tracked placeholders
- establish `uv` as the canonical Python project/environment/dependency workflow and add the initial `pyproject.toml`; generate and commit `uv.lock` when the environment is first resolved with `uv`

The skeleton establishes ownership boundaries only; it does not add speculative runtime implementation.

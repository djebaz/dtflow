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

Phase 2 brings a narrow text-to-image direct DT+ adapter forward from Phase 11. The existing probe remains diagnostic tooling; the adapter must explicitly handle token expiry, authenticated transport, subscription-only policy, sanitized failures and uncertain outcomes. Unsupported cancellation, remote recovery and input combinations must remain unavailable or unknown. Phase 11 broadens cloud capabilities and hardening.

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

The first implementation is a Draw Things gRPC backend. Protocol conversion, generated protobuf types, connection details, and Draw Things-specific mappings remain inside that backend package.

The Phase 2 DT+ backend will use the validated Python direct-cloud path, but the diagnostic probe must not simply become production code by relocation. Authentication/token lifecycle, credential storage, entitlement/PAYG rules, retries, cancellation, throttling, error mapping, persistence, model/file compatibility, and safe concurrency must be made explicit behind the backend boundary first. MediaGenerationKit remains a reference for protocol behavior, not a required DTFlow runtime dependency for the path already proven.

### Services

Services orchestrate application use cases, including:

- connection management
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

The exact storage format/database is intentionally undecided until the first persistence requirements are implemented.

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

Track the intended full TanqueStudio port in the [parity matrix](tanquestudio-parity.md). It assigns feature evidence, shared services, CLI/Qt exposure, backend support, milestones and acceptance. Complete the upstream feature inventory during Phase 2; unknown or deferred items remain explicitly tracked. Full parity is not a prerequisite for starting Qt.

### Phase 1 — gRPC connectivity proof

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

Follow [the Phase 2 implementation plan](phase-2-backends.md) and [the source-backed ecosystem research](../research/dtflow-ecosystem-research-report.md).

Introduce backend-neutral request, result, progress, error and capability types. Implement a deterministic fake backend, wrap the validated Draw Things gRPC path, and add narrow direct DT+ text-to-image support through native Python.

Define completion and uncertain-outcome semantics before wiring the UI. Preserve previews and incomplete outputs as explicitly labeled diagnostic/partial artifacts, never completed results. Ordinary tests must stay offline.

Investigate portable Windows packaging and dependency distribution early on the owner's Windows 10 / Intel Pentium E6500 machine; keep an installer option if required. Source execution is recorded as working; a single-executable build is not yet verified. Durable orchestration remains Phase 6.

### Phase 3 — Headless CLI and shared application services

Deliver a usable source-run command-line application before introducing Qt. Packaging initially targets the Qt application; a separate packaged CLI is outside scope. Follow [the Phase 3 CLI plan](phase-3-cli.md).

Introduce shared logging with rotating UTF-8 files, normal and explicit development detail levels, build/session/job correlation and secret sanitization. Development logs use repository-root `logs/`; packaged Windows builds use per-user application data. Logging is independent of cache bypass and durable execution records.

Expose the `dtflow` entry point for backend selection, non-generative connectivity checks, model inventory with explicit refresh and backend provenance, and complete text-to-image generation through fake, Draw Things gRPC and direct DT+ adapters.

Implement application services for request resolution, submission, progress/error handling, a durable atomic request/attempt record before submission, and output/reproducibility metadata saving. Persist potential remote execution before crossing the submission boundary; incomplete attempts cannot be automatically resubmitted. The CLI owns argument parsing and presentation only. Neither services nor the headless command path may require Qt.

Handle interruption and uncertain outcomes honestly; do not automatically resubmit ambiguous requests. Preserve previews as diagnostic artifacts. Keep subscription-only cloud behavior and the `-nc` / `--no-cache` contract.

Validate command parsing, help, exit behavior, saved metadata and end-to-end fixture-backed commands offline. Real generation remains explicit and separately authorized. A complete CLI means a usable path for the supported scope, not every advanced backend operation.

Define the configurable cloud concurrency maximum in the CLI with a default of four; it is a user-selected cap, not a verified entitlement. Persist it for shared-service use; Phase 7 adds concurrent queue scheduling. Do not imply parallel execution before that scheduler exists.

### Phase 4 — Minimal Qt shell and connection management

Create the PySide6 application shell and canonical Qt Designer sources after the Phase 3 CLI and services are usable.

Wire startup, main window, UI/resources, background execution and clean shutdown. Add Draw Things/cloud connection selection and useful status/failure presentation through the same services exercised by the CLI.

Add secure API-key entry and Windows Credential Manager storage through shared credential services, retaining environment input for headless use. Show version and short commit discreetly in About/status using the same bundled identity as the CLI and packaged filename.

Keep business logic and backend protocol code outside UI classes. Network and generation work must not block the Qt event loop. CLI execution must continue working without importing Qt.

### Phase 5 — Single generation

Connect the desktop UI to the generation services already exercised by the headless CLI for a complete single-generation flow. Reuse request resolution, progress/error handling and output persistence rather than creating a second execution path. Add basic LoRA selection with explicit weights through shared services and CLI/Qt exposure, gated by verified backend/model compatibility.

Persist enough metadata to reproduce the request. Mark a job completed only after successful terminal RPC status, validated final artifacts and durable output/metadata persistence. Saved previews must remain labeled as previews or diagnostics when final generation fails.

### Phase 6 — Durable jobs and queue

Extend Phase 3's durable request/attempt records with persistent jobs, queues and explicit lifecycle states.

Design for recovery after application restart before adding advanced workflow behavior. The inspected remote protocol exposes no generation job lookup or stream reattachment; recover local queued/completed state and reconcile interrupted in-flight jobs to an unknown outcome without automatic resubmission.

Restore the queue paused after restart. Require explicit Resume or Cancel; Resume selects only eligible work known never to have crossed the submission boundary. Interrupted ambiguous attempts remain unknown and are excluded from queue replay.

### Phase 7 — Concurrency

Add controlled concurrent generation after actual Draw Things concurrency behavior has been measured.

Current Phase 1 evidence supports treating one tested Draw Things gRPC server as one effective generation lane: concurrent client RPCs can be in flight together, but the tested iPhone/Bridge path completes work in an effectively serialized staircase and is slower than the matching serial baseline.

The direct DT+ diagnostic path is a separate result. It has completed six controlled cloud requests concurrently within one broad generation-time window, but that does not establish a fixed cloud concurrency limit or production scheduling policy. Do not transfer direct-cloud concurrency assumptions to the Draw Things backend.

Use the shared configurable cloud maximum, default four, in both CLI and Qt scheduling. Values above two change the Qt number's color subtly, without a prominent warning or confirmation dialog. Preserve a separate app/Bridge policy. Test bounded dispatch and throttling without automatically retrying ambiguous submissions.

Do not infer safe concurrency from client-side capability alone.

### Uncertain attempts and slot accounting

If submission definitely never occurred, release the scheduler slot. If a disconnect, local cancellation or other failure leaves remote execution possible, retain a durable unresolved reservation and pause new dispatch for that backend; already-running requests may finish. Release a reservation only on evidence that remote execution is no longer possible or an explicit user override.

An explicit override may resume eligible never-submitted work after the user acknowledges the unresolved attempt; it must not resubmit that attempt or mark it resolved. Record the override and preserve its uncertainty. The configured maximum bounds DTFlow scheduling, not guaranteed remote execution after an override: no inspected remote status API can establish how much unknown work remains active.

After restart, restore the queue paused with unresolved reservations intact. Ordinary Resume does not clear those reservations or bypass their dispatch block; the user must explicitly acknowledge and override that block. Test pre-submission slot release, post-submission disconnect/local cancellation, completion of other running requests, blocked replacement dispatch, explicit override and restart restoration in Phase 7. Phase 3 supplies durable attempt evidence, without adding a scheduler.

### Phase 8 — Gallery and presets

Add output browsing, generation metadata inspection, reusable presets, and regeneration/reuse flows.

### Phase 9 — Advanced image operations

Add image-to-image, inpainting, masks, controls, and related Draw Things features after the basic request model is stable.

### Phase 10 — Workflows

Build multi-step workflow composition/execution on top of the durable job and backend layers.

Workflow execution should remain resumable and inspectable.

### Phase 11 — Broader DT+ capabilities and hardening

Phase 1 diagnostic work has already established that a direct Python DT+ implementation is viable. The live-tested probe has exercised the API-key short-term-token flow, per-generation authentication, direct TLS gRPC generation against DT+ Cloud Compute, image-result decoding, and concurrent submissions without requiring MediaGenerationKit as a runtime component.

Phase 2 introduces the narrow direct Python backend. Phase 11 broadens its verified operations and production behavior rather than re-evaluating Python feasibility or delaying basic cloud integration.

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

Apply the Windows packaging requirement during Phase 2/3 investigation and the restart policy in Phase 6. Define the cloud concurrency setting in Phase 3 and expose it in Qt; Phase 7 implements bounded concurrent queue scheduling. Before scheduling exists, accepting the setting must not imply parallel execution.

### Additional confirmed product decisions

- Cloud credentials: enter the API key in Qt and save it securely through Windows Credential Manager. When an environment key is present it takes precedence, including failure for an empty/invalid value; otherwise use the saved key. Never silently switch sources after authentication failure. Keep environment-supplied credentials available for CLI use. Credential resolution belongs in shared services and must not require Qt; keys/tokens never enter ordinary settings, output metadata or logs. Secure storage failure must not silently fall back to plaintext.
- PAYG: unavailable in the first release; defer opt-in support to a later version. Initial requests remain explicitly subscription-only regardless of account PAYG settings.
- Updates: manual downloads initially, with no integrated updater or Microsoft Store requirement. A future release-page check is optional later work.
- Build identity: show the application version plus a short Git commit ID in CLI version output and a discreet Qt About/status area, and include both in distributed build filenames. Bundle the full source commit and dirty-worktree status so the executable needs no Git installation at runtime. Release builds must come from a clean checkout; development builds disclose local modifications and missing revision metadata instead of inventing an ID. Persist version/full commit in generation metadata where available.

Illustrative naming only (not an existing release): `dtflow-0.1.0-gabcdef1-windows-x64.exe`. Use the actual target architecture and source revision; an eventual installer follows the same identity convention.

### Remaining engineering investigations

Source-inspected answers and remaining evidence requirements are recorded in [research Section 10](../research/dtflow-ecosystem-research-report.md#10-follow-up-findings-on-the-eight-backend-questions). See the [Phase 2 plan](phase-2-backends.md) for implementation gates.

- Exact `drawthings-py` version/API and how much of the gRPC client it should own.
- Whether DTFlow should generate protobuf bindings itself or consume them through a dependency.
- Exact Draw Things progress and cancellation semantics, plus whether any server/app configuration can expose parallel backend execution beyond the effective single lane observed in the tested iPhone/Bridge path.
- Persistence technology and schema.
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

# DTFlow Initial Development Plan

**Status:** Initial planning  
**Product:** DTFlow (DTF)  
**Repository:** `djebaz/dtflow`

## 1. Product vision

DTFlow is a Windows desktop application for workflows, generation, and automation around Draw Things.

The first implementation targets Draw Things through its gRPC interface. The application should provide a durable desktop layer for connections, generation jobs, queues, presets, galleries, and eventually multi-step workflows without coupling the user interface to Draw Things protocol details.

DT+ Cloud Compute is a future backend, not part of the initial implementation.

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

### Initial generation backend

The initial real backend is Draw Things over gRPC.

The Python implementation may use `drawthings-py` where it is useful, while the public Draw Things gRPC/protobuf contract remains the backend boundary.

Before implementation, verify the exact current APIs and compatibility against the canonical Draw Things interfaces and the selected Python dependency.

### Future backends

The architecture must allow additional generation backends without changing UI-facing generation models.

Phase 1 diagnostic work has now proven that DT+ Cloud Compute is reachable directly from Python without a MediaGenerationKit runtime dependency. The validated path uses the official API-key flow to obtain a short-term token, performs per-generation authentication, connects directly to the DT+ gRPC endpoint over TLS, and receives normal generation results.

Future-backend direction is therefore:

- DT+ Cloud Compute through a native Python backend built from the validated direct-cloud path
- MediaGenerationKit as an architectural and protocol reference rather than a required runtime dependency
- a lightweight Swift/MediaGenerationKit bridge only as a fallback if a future DT+ capability cannot be reproduced reliably in Python
- a separate experimental native Windows Draw Things gRPC server

The production DT+ backend is still future work and is not required for the initial DTFlow client. The current direct-cloud implementation remains diagnostic tooling until authentication lifecycle, cancellation, retries, throttling, entitlement/PAYG behavior, persistence, progress/error mapping, model compatibility, and production concurrency policy are hardened.

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

A future DT+ backend may use the validated Python direct-cloud path, but the diagnostic probe must not simply become production code by relocation. Authentication/token lifecycle, credential storage, entitlement/PAYG rules, retries, cancellation, throttling, error mapping, persistence, model/file compatibility, and safe concurrency must be made explicit behind the backend boundary first. MediaGenerationKit remains a reference for protocol behavior, not a required DTFlow runtime dependency for the path already proven.

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
- Packaged Windows builds use the platform-local application data location by default rather than writing beside the executable; an explicit DTFlow home override may be added for development/portable use.
- Cache is disposable and must remain separate from durable application state. Cache storage should be split into type-specific subdirectories so categories can be measured and purged independently.
- Cache size visibility and manual/automatic cleanup should be implemented early because generated images and derived assets can become large.

## 7. Implementation phases

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

### Phase 2 — Backend abstraction

Introduce backend-neutral request/result/progress types and the generation backend contract.

Wrap the validated gRPC path behind the Draw Things backend implementation.

### Phase 3 — Minimal desktop shell

Create the PySide6 application shell and initial Qt Designer sources.

Expose a package/console entry point so an editable development install can be launched with `dtflow` without typing a source-file path. Keep startup logic outside the main-window implementation.

Establish a small CLI contract using descriptive long options plus stable, unique short aliases for commonly used development options. The initial cache bypass should support `-nc` / `--no-cache` and disable both cache reads and cache writes for that run.

Establish:

- application startup
- main window
- UI/resource loading
- service wiring
- clean shutdown

No generation protocol logic should live in UI classes.

### Phase 4 — Connection management

Add Draw Things server configuration and connection status.

The UI should expose useful connection failures without leaking low-level implementation details unnecessarily.

### Phase 5 — Single generation

Connect the desktop UI to the backend abstraction for a complete single-generation flow.

Persist enough metadata to reproduce the request.

### Phase 6 — Durable jobs and queue

Introduce persistent jobs and explicit lifecycle states.

Design for recovery after application restart before adding advanced workflow behavior.

### Phase 7 — Concurrency

Add controlled concurrent generation after actual Draw Things concurrency behavior has been measured.

Current Phase 1 evidence supports treating one tested Draw Things gRPC server as one effective generation lane: concurrent client RPCs can be in flight together, but the tested iPhone/Bridge path completes work in an effectively serialized staircase and is slower than the matching serial baseline.

The direct DT+ diagnostic path is a separate result. It has completed six controlled cloud requests concurrently within one broad generation-time window, but that does not establish a fixed cloud concurrency limit or production scheduling policy. Do not transfer direct-cloud concurrency assumptions to the Draw Things backend.

Do not infer safe concurrency from client-side capability alone.

### Phase 8 — Gallery and presets

Add output browsing, generation metadata inspection, reusable presets, and regeneration/reuse flows.

### Phase 9 — Advanced image operations

Add image-to-image, inpainting, masks, controls, and related Draw Things features after the basic request model is stable.

### Phase 10 — Workflows

Build multi-step workflow composition/execution on top of the durable job and backend layers.

Workflow execution should remain resumable and inspectable.

### Phase 11 — DT+ backend hardening and integration

Phase 1 diagnostic work has already established that a direct Python DT+ implementation is viable. The live-tested probe has exercised the API-key short-term-token flow, per-generation authentication, direct TLS gRPC generation against DT+ Cloud Compute, image-result decoding, and concurrent submissions without requiring MediaGenerationKit as a runtime component.

Phase 11 should therefore promote the proven protocol path into a production backend rather than re-evaluate Python feasibility.

Before product integration:

- move cloud authentication and transport behind the backend abstraction rather than importing tooling code into the application layer;
- define short-term-token caching, expiry, refresh, and credential-storage behavior;
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
- mocked protocol tests where appropriate
- focused integration tests against Draw Things when required

Real generation should only be run when it validates behavior that cannot be established without inference. Ordinary unit/test-suite execution must never submit a real generation accidentally; live Draw Things integration tests must be explicit and separately gated.

## 9. Open questions

- Exact `drawthings-py` version/API and how much of the gRPC client it should own.
- Whether DTFlow should generate protobuf bindings itself or consume them through a dependency.
- Exact Draw Things progress and cancellation semantics, plus whether any server/app configuration can expose parallel backend execution beyond the effective single lane observed in the tested iPhone/Bridge path.
- Persistence technology and schema.
- Packaging strategy for Windows.
- Update/distribution strategy.
- Production DT+ authentication lifecycle, failure behavior, entitlement/PAYG semantics, persistence requirements, model/file compatibility, cancellation/retry semantics, and a safe concurrency policy. The diagnostic API-key token/auth/gRPC path is now proven separately.
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

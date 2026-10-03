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

Planned research includes:

- DT+ Cloud Compute
- MediaGenerationKit as an architectural/reference implementation
- a possible lightweight Swift bridge if a Python DT+ implementation is not practical
- a separate experimental native Windows Draw Things gRPC server

None of these future paths is required for the initial DTFlow client.

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
Draw Things gRPC          Future DT+
    backend                 backend
        |
        v
   Draw Things
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

A DT+ package should not contain simulated behavior before the real protocol and requirements are understood.

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

The repository should grow incrementally rather than creating a large empty skeleton.

Expected areas, created only when needed:

```text
devdocs/
  plans/
  architecture/
  research/
  decisions/

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
- LF is the default repository line ending.
- Native Windows scripts may use CRLF.
- `tmp/` is an intentionally ignored local scratch workspace.
- Local IDE and coding-agent state is ignored.
- Qt Designer `.ui` and protobuf `.proto` sources are tracked.
- Generated Qt Python and generated protobuf Python are ignored initially.
- Model/checkpoint artifacts are not committed.
- Git LFS is not required initially.
- If `uv` is selected, `uv.lock` should be tracked for reproducible application builds.

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

Do not infer safe concurrency from client-side capability alone.

### Phase 8 — Gallery and presets

Add output browsing, generation metadata inspection, reusable presets, and regeneration/reuse flows.

### Phase 9 — Advanced image operations

Add image-to-image, inpainting, masks, controls, and related Draw Things features after the basic request model is stable.

### Phase 10 — Workflows

Build multi-step workflow composition/execution on top of the durable job and backend layers.

Workflow execution should remain resumable and inspectable.

### Phase 11 — DT+ research and backend

Map the current DT+ Cloud Compute contract and evaluate implementation options.

Only implement a DT+ backend once authentication, request semantics, compatibility, persistence requirements, and failure behavior are understood.

## 8. Validation strategy

Early validation should favor cheap, focused checks:

- syntax/import checks
- configuration parsing
- CLI/help checks where applicable
- unit tests for backend-neutral logic
- mocked protocol tests where appropriate
- focused integration tests against Draw Things when required

Real generation should only be run when it validates behavior that cannot be established without inference.

## 9. Open questions

- Exact `drawthings-py` version/API and how much of the gRPC client it should own.
- Whether DTFlow should generate protobuf bindings itself or consume them through a dependency.
- Exact Draw Things progress, cancellation, and concurrent request semantics.
- Persistence technology and schema.
- Packaging strategy for Windows.
- Update/distribution strategy.
- DT+ authentication and protocol details.
- Whether any MediaGenerationKit components can or should be reused directly on Windows.

## 10. Initial repository setup

The first setup PR is intentionally small:

- update `.gitignore`
- add `.gitattributes`
- add this initial plan

No application package skeleton is created by this PR. The implementation tree should be introduced as the corresponding phases begin.

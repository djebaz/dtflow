# Phase 3 — Headless CLI and shared application services

**Status:** Planned; no CLI implementation is introduced by this documentation change.  
**Prerequisite:** [Phase 2 backends and offline tests](phase-2-backends.md).  
**Roadmap:** [initial plan](initial.md).

## Goal

Deliver a working `dtflow` command-line application before Qt. The CLI must exercise the real Python backend adapters through application services and provide a complete usable text-to-image path for the supported scope.

Dependency direction: CLI and Qt call shared services; services call core/backend contracts; adapters own provider protocols. CLI parsing, terminal formatting and Qt widgets must not own generation orchestration.

## Required operations

| Operation | Required behavior |
| --- | --- |
| Backend selection | Explicit fake, Draw Things gRPC or direct DT+ selection; no silent fallback |
| Connectivity | Bounded non-generative transport/authentication check with useful sanitized errors |
| Model inventory | Retrieve and display backend-advertised models/files; distinguish failed discovery, empty inventory and compatibility uncertainty |
| Request resolution | Resolve configuration/preset plus explicit overrides into a reproducible request; validate before submitting |
| Generation | Submit supported text-to-image requests, expose actual progress and collect validated final outputs |
| Output saving | Save all required final images plus reproducibility metadata without silently overwriting useful results |
| Interruption | Stop local waiting/transport as supported, preserve evidence and report remote uncertainty without automatic resubmission |
| Cache policy | Support `-nc` / `--no-cache`; disable both cache reads and writes when selected |

Use environment-supplied cloud credentials initially. Never accept an API key in routine logged request metadata or display credentials/tokens. Keep PAYG opt-in disabled.

## CLI contract

Establish concise command names and stable lowercase short/long option pairs consistent with AGENTS.md. Determine exact subcommands/options during implementation; do not document proposed flags as already available.

Provide accurate help, parameter validation, useful nonzero exit codes and separation of machine-readable results from progress/diagnostic output. Define exit behavior for success, validation failure, remote rejection, interruption and unknown outcomes.

Expose prompt, negative prompt, model, seed, dimensions, steps and guidance where actually supported, plus backend connection selection, output destination and configuration/preset input. Preserve canonical defaults and record the effective request rather than only the entered arguments.

The development entry point runs through `uv run dtflow` once implemented. It must not import or initialize Qt, and must remain usable after the desktop interface is added.

## Shared services and persistence boundary

Services own request resolution/preflight, backend selection, submission, events, result validation and artifact saving. Keep services callable from Python as well as the CLI and future Qt UI.

Persist resolved prompts/seeds, model/checkpoint and relevant adapter identifiers, effective generation parameters, backend identity, dependency/version information available at runtime, output references and completion evidence. Use atomic metadata writes where practical and preserve partial evidence after failures.

A successful terminal RPC and valid outputs establish backend success; successful durable output/metadata saving establishes application success. Saved previews after failed generation remain labeled preview/diagnostic artifacts and never count as completed results.

This phase saves artifacts and reproducibility metadata, not a durable queue or full restartable job scheduler. Phase 6 introduces transactional job state and reconciliation. Do not claim remote recovery: the inspected protocol has no generation status/reattachment operation.

## Testing and acceptance

- Headless imports and command startup work without Qt.
- Help and every documented option match the implementation.
- Fake-backed CLI commands cover success, failure, interruption and uncertain outcomes.
- Real adapters execute against local gRPC/HTTP fixtures through the CLI/service path, including connectivity, inventory, authentication and generation.
- Tests assert saved output metadata, seed-zero fidelity, multiple final outputs where supported, empty-output rejection and preview labeling.
- An unexpected external connection fails ordinary tests; no subscription credits are consumed by fixture coverage.
- Cloud subscription-only policy, token refresh and sanitized failures remain covered.
- Validate syntax/imports, parsing/help, `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .`.
- Record any actual Windows smoke-test result without inferring packaging success from source execution.

Explicitly authorized live checks can verify deployed behavior after offline coverage; they are not required merely to test CLI plumbing.

## Scope and later expansion

“Complete CLI” means an end-to-end application for supported operations, not all TanqueStudio features in one phase. Queue matrix expansion, durable jobs, workflows, advanced image/control combinations, video and cloud asset/LoRA upload remain separate work until their contracts and compatibility are verified.

Model discovery is included now. Cloud LoRA upload is distinct from generation and must not be assumed to use the same endpoint, format or authorization contract.

Phase 4 adds the minimal Qt shell and connection management. Phase 5 adds GUI generation by calling these existing services. Later features should expose the same underlying services through the CLI wherever practical so Qt remains presentation rather than a second execution engine.

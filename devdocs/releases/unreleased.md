# Unreleased

Pending changes for the next DTFlow release.

## Added

- Planned shared logging contract in `AGENTS.md` and Phase 3: repository-root ignored `logs/` in development, per-user packaged Windows logs, rotating UTF-8 diagnostics, explicit dev detail, build/job correlation and secret sanitization. Clarify the existing ignore rule without changing ignore behavior.

- Repository-wide version/build identity rules in `AGENTS.md`: shared version/revision metadata, consistent CLI/Qt/build naming, bundled revision without runtime Git, honest development status and identifiable smoke-test reports.

- Tracked TanqueStudio parity matrix with upstream evidence, service ownership, CLI/Qt exposure, backend support, phase assignments and acceptance criteria.

- Phase 3 headless CLI plan covering shared services, backend selection, connectivity, model inventory, text-to-image generation, durable outputs/metadata and interruption handling.

- Explicit offline testing boundaries: deterministic fake first, local gRPC server tests through actual adapters, DT+ HTTP authentication mocks, cloud gRPC authorization assertions and rejection of unexpected external connections.

- Source-backed ecosystem research covering Python transport, queue/workflow references, backend completion/cancellation/recovery semantics, input compatibility, and Windows dependency constraints.
- Phase 2 implementation plan for a shared backend contract, deterministic fake backend, Draw Things gRPC adapter and narrow native Python direct DT+ adapter.

- Initial repository conventions and development plan.
- LF-first line-ending policy.
- DTFlow local-state, cache, generated-output, model-artifact, IDE, and coding-agent ignore rules.
- Initial architectural directory skeleton.
- Repository contract in `AGENTS.md`.
- Release-note workflow based on this unreleased file.
- Initial `pyproject.toml` with the uv-native project metadata, pytest development dependencies, and Ruff lint/format configuration.
- Repository rules requiring dependency changes to keep `pyproject.toml` and `uv.lock` synchronized through uv.
- Python 3.13 development baseline via `.python-version`.
- Project-level uv prerelease policy so normal `uv lock`, `uv sync`, and `uv run` commands use the same resolver mode required by the current dependency graph.
- Project-level uv NumPy compatibility override for the bounded `drawthings-py>=0.4.0,<0.5.0` dependency line.
- Draw Things gRPC probe support for configurable 1-8 job runs, serial baselines, and a full-preset workload mode.
- Experimental direct DT+ Cloud Compute probe mode using `DRAWTHINGS_API_KEY`, official token/auth endpoints, independent gRPC clients, and no PAYG opt-in. User-reported live runs validate the path end to end from Python through cloud authentication, direct TLS gRPC generation, result decoding, and concurrent submissions without a MediaGenerationKit runtime dependency.

## Changed

- Resolve the remaining product choices: secure Qt API-key entry via Windows Credential Manager with headless/environment support; PAYG deferred beyond the first release; manual updates initially.
- Plan consistent version plus short source-commit identity in CLI output, discreet Qt presentation and build filenames, with bundled full revision and honest development-build status. These remain documentation requirements, not shipped features.

- Record confirmed initial personal use, Windows 10 / Pentium E6500 support target, portable-first packaging and possible later public distribution.
- Prioritize direct DT+ production use while retaining local gRPC development/smoke coverage; cloud readiness requires separate evidence.
- Plan paused queue restoration with explicit Resume/Cancel and no replay of ambiguous attempts.
- Plan cloud concurrency configuration from Phase 3, default four, shared with Qt; use a subtle color above two and introduce bounded concurrent queue execution in Phase 7. These are planned policies, not implemented runtime features.

- Require a minimal atomic request/attempt record before Phase 3 remote submission and prevent automatic retries of unfinished potentially submitted attempts.
- Separate initial cloud safety/authentication foundations from Phase 11 expansion; clarify inventory refresh/provenance/classification, Phase 5 basic LoRA selection and request-fidelity validation.

- Deliver a working CLI in Phase 3 before the Phase 4 Qt shell; GUI generation reuses CLI-exercised services. Durable jobs and advanced operations retain separate later phases.

- Bring basic direct DT+ text-to-image support forward into Phase 2; retain broader cloud capabilities and hardening in Phase 11. This is a roadmap change, not a shipped runtime feature.
- Make preview/diagnostic artifact labeling, uncertain remote outcomes, subscription-only defaults and offline test boundaries explicit in the implementation plan.

- Use Python 3.13 as the current DTFlow development/runtime baseline.
- Python 3.14 is currently excluded because the `drawthings-py==0.4.0` dependency chain fails while building `fpzip==1.2.5` in the tested Windows environment.
- Use NumPy 2.3.5 for the current Draw Things Python client path on the tested legacy Windows Pentium E6500 machine. NumPy 2.4.4 and 2.5.3 fail while loading `_multiarray_umath` there; NumPy 2.3.5 imports successfully, `drawthings-py==0.4.0` imports successfully, and the real gRPC/model-browser connectivity and generation paths succeed.
- Make the Draw Things probe default to a lightweight `z_image_turbo` workload requested at 300 x 300, 4 steps, batch size/count 1, with high-resolution fix disabled.
- Store Draw Things probe images and timing reports under repository-local ignored `tmp/drawthings_concurrency/` by default.
- Use compact lowercase probe CLI pairs (`-a/--host`, `-p/--port`, `-d/--cloud`, `-r/--preset`, `-j/--jobs`, `-f/--full`, `-s/--serial`, `-o/--out`, `-c/--conn`, `-k/--skip-conn`) while silently retaining the previous probe spellings as compatibility aliases.
- Record live concurrency evidence: the tested iPhone Draw Things Bridge path behaves effectively as a single generation lane, while direct DT+ Cloud Compute completed six concurrent controlled jobs in 13.84 s versus 54.42 s of summed serial `generate()` time, about a 3.93x generation-path throughput improvement.
- Reframe the DT+ roadmap around hardening the already-proven native Python direct-cloud path into a production backend. MediaGenerationKit remains a protocol/reference implementation, with a Swift bridge reserved as a fallback for future capabilities that cannot be reproduced reliably in Python.

## Fixed

- Configure Hatchling to package the `src/dtflow` application package so editable `uv sync` installs can build DTFlow.

# Unreleased

Pending changes for the next DTFlow release.

## Added

- Phase 1 closeout criterion/evidence matrix with bounded feasibility acceptance, explicit recorded/user-reported/offline evidence and Phase 2 owners for remaining progress/error/cancellation gaps.
- Offline probe checks for connection readiness, pre-release failure, interruption cleanup, empty outputs, malformed authentication responses, HTTP errors, connection-only flow and CLI validation.

- Phase 2A implementation handoff: build a thin headless fake-generation/service/SQLite-attempt/saved-PNG path in a separate implementation PR, enforce import boundaries and refine the existing specs from working code.
- Planned standard-library SQLite execution store, WAL/FULL durability policy, patched-runtime check, file/database reconciliation and versioned schemas; frozen slotted dataclasses/Protocols avoid a new compiled validation stack. No runtime implementation or dependency change is introduced.

- Phase 2 backend architecture specification: async loop ownership, lifecycle/health/inventory/generation operations, immutable requests/artifacts/events/errors, independent submission evidence, concrete deadlines/resource limits, replaceable dependency integration and credential-provider scope.
- Pinned direct-cloud protocol specification with official/probe sources, exact token/auth fields and blob construction, expiry/coalesced refresh, subscription-only policy, async HTTP/cancellation choices and required offline fixtures.

- Source-backed LoRA compatibility/cloud asset plan: structured metadata, shared preflight diagnostics, official exception/refiner rules, separate availability/application evidence, custom upload negotiation/deduplication, durable receipts and honest remote-listing limitations.
- Research follow-up with pinned TanqueStudio, DrawHeadlessThings, drawthings-py and official LoRA importer/store/engine sources. Findings are source inspection, not deployed upload/use verification.

- Planned release roadmap with private source CLI milestones, the first complete Qt executable, incremental image/workflow releases, release evidence and a scoped 1.0 reliability gate. No release/tag/version bump is introduced.
- Saved-state compatibility requirements from the durable queue release: independent schema versions, recoverable atomic migration, clear unsupported-schema rejection and no ambiguous replay.

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

- Expand README with current development status, setup/validation, diagnostic help, repository layout, design requirements and dependency-distribution references. Distinguish planned application features from existing probe tooling.

- Start Phase 2A maintenance: rename the diagnostic tool to tools/drawthings/drawthings_probe.py and relocate its existing checks to tests/unit/tools/ with explicit retirement scope. Preserve historical pinned URLs. Add pytest-asyncio through uv, remove pytest-qt from the default dev group until Qt arrives, and remove the now-unneeded plugin-disable configuration. No new probe tests or application backend implementation is included in this opening commit.

- Stabilize Phase 1 closeout wording and keep run-specific counts/patch versions/commit identity in the PR description. Plan probe retirement, pytest-asyncio/Qt dependency cleanup and explicit loop-before-guard ownership in the first 2A PR; specify per-instance TLS channels rather than mutation of the dependency's global context.

- Clarify the Phase 1 handoff: recorded connectivity/generation evidence permits starting Phase 2A; remaining cancellation/progress/error evidence stays explicit and is not claimed complete or newly tested.

- Bring the minimal Phase 3A persistence/service/entry-point foundation into Phase 2A without claiming the full CLI or a release. Retain tested rolling queues and custom cloud LoRA upload/reuse in Phase 3B before Qt; add a pre-Phase-4 worker-thread spike and a drawthings-py replacement-risk estimate. Align the initial and CLI plans with the selected SQLite store and database-polled queue controls.

- Split Phase 2 into 2A contract/fake, 2B local gRPC, 2C direct cloud and parallel 2D packaging/licensing/upstream feature inventory. Add connectivity/inventory acceptance, installed-stub fixture design, planned asyncio/TLS/network/live-test infrastructure and explicit Phase 4 saved-credential storage. Documentation only; no runtime/dependency changes.

- Move bounded LoRA selection/weights/preflight to Phase 3A and custom direct-cloud upload/reuse to Phase 3B, both exercised by the CLI before Qt. Update proposed 0.1/0.2/0.3 scope and Phase 5A presentation accordingly; broader conversion/editor and remote asset management remain later work.

- Split Phase 5 into generation/configuration reuse (5A) and desktop queue controls (5B), both required for the first executable; plan basic configuration import/export in 3A and Qt save/load in 5A before the richer preset library.
- Define incremental Phase 9 image and Phase 10 workflow delivery gates, and link release planning from README, AGENTS.md, roadmap and parity matrix.

- Split Phase 3 into single-generation foundation (3A) and functional durable queues/rolling dispatch (3B), both tested through the CLI before Qt. Move essential queue/restart/concurrency work forward; reserve Phase 6 for batch planning and Phase 7 for advanced scheduling.

- Require Phase 3B rolling queue dispatch before Qt: refill each released slot with the next eligible item, preserve item/result identity across out-of-order completion, and retain unresolved reservations without replacing potentially running requests.

- Define deterministic environment-over-saved cloud credential precedence with no silent authentication fallback; plan fixture coverage for missing, empty and failed selected credentials.
- Define Phase 3B unresolved concurrency reservations, backend dispatch pause, explicit user override and paused restart behavior without replaying uncertain attempts.
- Clarify that the initial CLI runs from source while packaging targets Qt; correct Windows path rendering and remove repeated build-identity wording.

- Resolve the remaining product choices: secure Qt API-key entry via Windows Credential Manager with headless/environment support; PAYG deferred beyond the first release; manual updates initially.
- Plan consistent version plus short source-commit identity in CLI output, discreet Qt presentation and build filenames, with bundled full revision and honest development-build status. These remain documentation requirements, not shipped features.

- Record confirmed initial personal use, Windows 10 / Pentium E6500 support target, portable-first packaging and possible later public distribution.
- Prioritize direct DT+ production use while retaining local gRPC development/smoke coverage; cloud readiness requires separate evidence.
- Plan paused queue restoration with explicit Resume/Cancel and no replay of ambiguous attempts.
- Plan cloud concurrency configuration from Phase 3, default four, shared with Qt; use a subtle color above two and implement and test bounded concurrent queue execution in Phase 3B before Qt. These are planned policies, not implemented runtime features.

- Require a minimal atomic request/attempt record before Phase 3 remote submission and prevent automatic retries of unfinished potentially submitted attempts.
- Separate initial cloud safety/authentication foundations from Phase 11 expansion; clarify inventory refresh/provenance/classification, Phase 3A basic LoRA selection and request-fidelity validation, with Phase 5A Qt presentation.

- Deliver a working CLI in Phase 3 before the Phase 4 Qt shell; GUI generation reuses CLI-exercised services. Phase 3B includes durable jobs/queues and rolling dispatch; batch expansion and advanced operations retain later phases.

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

- Initialize the test asyncio runner before installing the socket/DNS guard, allowing Windows' private event-loop wakeup socket without allowing test scenarios to dial loopback. Keep run-specific failure/rerun evidence in the closeout PR description.

- Make concurrent probe release wait for all actual connections; a failed connection aborts before group submission and interruption cancels/awaits outstanding jobs. Correct the historical timing caveat without altering recorded measurements.
- Disable automatic pytest-qt plugin loading until the Qt runtime is introduced, so headless uv run pytest works with the current dependency set; re-enable during Qt implementation.

- Configure Hatchling to package the `src/dtflow` application package so editable `uv sync` installs can build DTFlow.

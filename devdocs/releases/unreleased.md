# Unreleased

Pending changes for the next DTFlow release.

## Added

- Phase 3A source-run CLI: explicit `-b fake|local|cloud` backend selection through a composition root (no fallback), `check`/`models`/`generate` against real adapters, `-e HOST:PORT` for local Draw Things, `models -k` kind filter, documented exit codes (0/2/3/4/5/130) and JSON-only stdout. Offline-validated against fixtures; no live request, generation or billable action was run.
- Versioned credential-free generation configuration (`dtflow.generation-config` v1): `-c/--config` import (also from a prior `metadata.json`), `-u/--export`, default < file < override precedence with per-field resolution evidence, and rejection of newer versions and credential-like keys.
- LoRA selection (`-l ID:WEIGHT`), Draw Things whole-model LoRA wire serialization verified against the decoded configuration, shared preflight diagnostics that stop confirmed architecture mismatches and absent LoRAs before any attempt is recorded, and explicit `-r/--prefix` trigger-prefix application. Official exception/refiner rules are not pinned and application on real servers is unverified.
- Shared standard-library logging (`-x/--dev`, `-q/--logs`): queue-backed rotating `dtflow-<session>.log` files with session/attempt correlation, bounded retention, sanitized messages and exceptions, and non-fatal write-failure reporting.
- Persisted cloud concurrency setting (`dtflow settings -j`, 1-4, default 4; `settings.json` with its own schema version). It is a client policy, not verified entitlement.

- `dtflow test -local/--local` and `-cloud/--cloud` limit live connectivity checks to the local Draw Things server or DT+ (each implies `-live` when given alone); each scope needs only its own environment settings, a cloud-only run omits the local LoRA comparison, and `-local` with `-gen` is rejected. No live request was run for this change.

- Resolve `platform.uname()` before the test network guard blocks subprocesses, so an intermittent Windows WMI fallback during collection no longer fails with "Subprocesses need explicit guarded-test registration".

- Live local checks now separate "Draw Things not running" from real failures: a refused connection or connect deadline warns and skips (the cloud inventory check keeps its passed cloud evidence and omits only local LoRA enrichment), other local connect errors still fail the local check with a short message (a reset during connect hints to check TLS in the app) but only warn in the cloud check, and an Echo with no model files warns that "Enable Model Browsing" is probably off. Warnings and their fixes are retained per check in `results.json` and printed as a colored summary at the end of the run; `check_connection` diagnostics now include `transport_kind`. No live request or generation was run for this change.

- Expose non-generative backend `get_file_identities()` with backend-neutral `FileIdentity` existence/optional SHA-256, and direct DT+ client concurrency 1–4 (default 4). The cap is a client policy, not a service entitlement or rolling scheduler.

- Add production LoRA availability enrichment across local Draw Things gRPC and direct DT+: preserve Echo trigger prefix/default weight, selectively resolve converted SHA-256 with `FilesExist`, classify DT+ direct listing or metadata-only BYOM reuse/upload-required evidence, and atomically write the rebuildable `.dtflow/cache/inventory/lora_inventory.json`. The source path never follows signed upload URLs and no generation/upload is performed by offline tests. The existing non-generative live inventory group now also retains `inventory/lora_inventory.json`; its DT+ BYOM lookup is bounded to `DTFLOW_LIVE_LORA` when provided, otherwise the longest local LoRA filename, while other non-cloud-listed LoRAs remain unresolved rather than being mass-hashed. Rename local-only integration tests from `test_drawthings_*` to `test_local_grpc_*` while retaining `drawthings` for shared provider protocol modules.

- Add `tools/drawthings/grpc_capabilities_probe.py`, a non-generative local Draw Things gRPC capability diagnostic covering the complete current read-only surface (`Echo`, `FilesExist`, `Pubkey`, and `Hours`) while explicitly excluding generation and upload. It preserves full Echo metadata fields, checks local registry-file visibility, requests SHA-256 only for small registry candidates plus explicit `--hash-file` values, and can retain a sanitized JSON report. Add focused offline parsing/alignment tests and source-backed capability documentation; no live RPC is run by the test suite.

- Add `tools/drawthings/local_files_exist_probe.py`, a non-generative local Draw Things gRPC diagnostic that requests a named converted checkpoint through `FilesExist(filesWithHash=...)` and reports the server-computed SHA-256 when available. It uses the same bundled Draw Things CA policy as `drawthings-py`, supports an optional `DRAWTHINGS_SHARED_SECRET`, and transfers no checkpoint bytes.

- Add `tools/drawthings/get_byom_probe.py`, a non-uploading DT+ `/get_byom_urls` diagnostic for a known checkpoint fingerprint. Owner-run deployed evidence shows filename + SHA-256 reuses an existing asset without a size field, while omitting SHA-256 returns no usable match. The earlier full/omitted-size/omitted-SHA/filename-only request matrix remains available through `--matrix`. Current DT+ proxy `FilesExist` does not return hashes, so the probe no longer claims remote checksum discovery. It records sanitized responses, redacts signed URLs, and never downloads checkpoint bytes, performs a PUT upload, or submits generation.

- Add `tools/drawthings/ckpt_probe.py`, a standard-library checkpoint fingerprint helper that streams a local file and reports its exact byte size and SHA-256 without loading the whole checkpoint into memory. It performs no network request, model parsing, generation or upload.

- Optional DT+ live token/inventory check and one complete generation smoke, selected by the existing `-live` and `-gen` test flags. Require environment credentials and an explicit model for generation; preserve saved output/attempt evidence and allow only the two cloud targets in the network guard. Ordinary tests skip these checks; no cloud live request was executed during implementation.

- Phase 2C direct DT+ adapter with async HTTPX API-key authentication, immutable auth scope, expiry-aware shared refresh, per-generation authorization and subscription-only fields. Reuse the actual 2B transport/wire/codec path; add focused cloud fixtures and acceptance ownership notes. Cloud TLS stays on the working probe baseline. Real-backend CLI generation selection remains Phase 3A; the owner reports Windows offline and bounded deployed DT+ smoke success; latest evidence-path changes still need a Windows repeat.

- Source-run dtflow test wrapper over pytest: -all/--all for offline tests, -live/--live for live connectivity, and -gen/--generate for generation smoke selection. No flags runs offline; explicit live groups can run alone or alongside offline tests. Wrapper selection needs no separate marker/env opt-in; configured endpoints and exact network allowlists remain required. Phase 2C adds one optional DT+ generation smoke; it requires environment credentials and an exact model identifier, and refuses unconfigured selection before network setup. The live group includes local Draw Things and DT+ token/inventory checks; the owner reports successful deployed token/inventory and bounded generation checks; those establish only the tested workload. Existing direct pytest gates remain supported.

- Phase 2B narrow local Draw Things gRPC adapter (`dtflow.backends.drawthings`): bundled local-CA compatibility with optional strict system/custom trust, txt2img with seed-zero and wire-configuration fidelity, inventory with provenance, one-lane generation with shared deadline, received-tensor checks, honest failure evidence, and a scripted TLS/gRPC fixture server with test-only certificates. Adds the direct `grpclib` dependency. Offline adapter/fixture validation is recorded, and the owner reported successful Windows non-generative live connectivity/inventory after the TLS fix, including wrapper live-only and offline-plus-live runs. Live generation, production cancellation behavior and packaged execution remain unverified, and the full CLI does not expose the real adapter yet.

- Phase 2A async backend-neutral contracts and deterministic fake backend with lifecycle, connectivity, inventory,
  typed failures and explicit fixture provenance; environment/fake-saved credential providers preserve precedence.
- Minimal `dtflow` CLI for explicit fake generation, connectivity, inventory and attempt inspection through shared
  services. Save validated fixture PNGs and reproducibility metadata before application success; preserve seed zero.
- SQLite attempt schema, exclusive state ownership, runtime/schema rejection, backup and restart reconciliation
  without replay. Failed or interrupted output saving preserves known backend success separately from local success.
- Shared offline endpoint/network/subprocess guard, asyncio ownership and import-boundary checks, with focused fake,
  persistence, cancellation, deadline, memory/event admission and headless process coverage. Real adapter/TLS/cloud
  fixtures and packaged Windows acceptance remain future work; this introduces no release tag.

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
- Phase 2D investigation documents: an accepted GPL-3.0-only DTFlow licensing decision with the locked runtime license table, a replacement-cost estimate, a Windows packaging checklist with an all-"Not run" findings table, a non-generative `tools/packaging/smoke.py` environment check, and a TanqueStudio feature inventory pinned to commit `6474f1e`. Windows results are recorded only from owner-run checks.

## Changed

- Use fixed `local_inventory.json`, `cloud_inventory.json` and `lora_inventory.json` names across inventory cache, development application layout, operation-owned scratch snapshots and retained live evidence. Keep endpoint/server observations in JSON instead of filename hashes; no stable account/device identity is claimed. Update live evidence writing and README examples. Raw application inventory disk-cache integration remains planned; no old-tree migration, cache reader or live request is introduced.

- Separate retained live-test evidence into ignored `test-runs/<run-id>/`, with flat local/cloud inventory JSON, shared state/outputs and one build/check summary. Keep normal application outputs backend-neutral; document planned flat cache paths and `CACHE_VERSION` invalidation. No old-tree migration/fallback, dependency changes or live requests during this layout change.

- Extend the authoritative-layer testing model beyond Phase 3 through Phases 4–11: Qt tests own presentation/wiring rather than backend semantics, planners/compilers use cheap deterministic tests, advanced image/cloud phases test only operation-specific deltas, and release gates consume rather than duplicate earlier evidence. Add bounded individually selectable live-workload intent for desktop, image, workflow and later cloud compatibility without advertising an unimplemented selector command. No runtime, dependency or test implementation changes are introduced by this planning amendment.

- Replace overlapping Phase 3 acceptance checklists with authoritative-layer ownership, flexible single-generation/queue/asset scenario groups and separately selected live workflows. Align LoRA and release acceptance to consume the same evidence; retain behavioral requirements and deterministic failure/recovery coverage. Plan individual live-workload selection without claiming it exists. No runtime, dependency or test implementation changes are introduced by this amendment.

- Specify lean Phase 2C acceptance through five cloud-specific scenario groups and focused parser/HTTP tests. Reuse shared/2B coverage only for the same implementation path; use the existing TLS/gRPC fixtures and measure added suite runtime. Authentication scope is immutable per client, with replacement rather than runtime scope switching. These are specification changes; no cloud adapter implementation, cloud test execution or dependency change is claimed.

- Adopt GPL-3.0-only for DTFlow before its first public release, replacing the unreleased MIT declaration in `LICENSE` and `pyproject.toml`. No DTFlow version was previously published under MIT; update the decision, roadmap and packaging documentation to match. Runtime behavior and dependency versions are unchanged.

- Expand README with current development status, setup/validation, diagnostic help, repository layout, design requirements and dependency-distribution references. Distinguish planned application features from existing probe tooling.

- Start Phase 2A maintenance: rename the diagnostic tool to tools/drawthings/drawthings_probe.py and relocate its existing checks to tests/unit/tools/ with explicit retirement scope. Preserve historical pinned URLs. Add pytest-asyncio through uv, remove pytest-qt from the default dev group until Qt arrives, and remove the now-unneeded plugin-disable configuration. No new probe tests were added; the following implementation commits add the core/fake application path.

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

- Preserve wire validation as a typed pre-submission validation failure; close the cloud HTTP client even when shared-refresh cancellation exceeds its separate cleanup budget. Extend existing validation/shutdown scenarios without adding a new matrix.
- Validate BYOM upload-entry/checksum consistency; preserve LoRA source endpoint/server observations, original timestamps and stale diagnostics separately from resolution time. Fresh negative file-existence evidence reports absence. Use unique temporary cache files; increment the disposable inventory format to version 2 without migration.
- Treat automatic live LoRA selection as discovery, allowing upload-required/unknown evidence and empty local LoRA inventories. An explicitly configured `DTFLOW_LIVE_LORA` asserts known cloud reuse. Add missing lowercase short aliases to metadata probes and refresh deployed-evidence wording to distinguish owner-reported earlier-checkpoint success from pending final-head Windows acceptance.

- Use an explicit local Draw Things bundled-CA TLS policy by default, matching the working probe's hostname behavior while retaining certificate-chain verification. Clear Python 3.13's strict X.509 flag only for this CA compatibility path; owner-reported live diagnostics found a missing key-usage extension and IP mismatch. The non-generative live check uses this policy without manual CA setup. System/custom-CA policies remain verified; planned direct DT+ TLS follows the probe baseline without added certificate requirements. The owner reported a successful Windows non-generative local connection/inventory check after this change; live generation and production cancellation behavior remain unverified.

- Preserve validated local gRPC backend completion and final artifacts when terminal-event delivery fails; return a sanitized warning so the service can proceed with durable saving rather than recording the remote outcome as unknown.

- Initialize the test asyncio runner before installing the socket/DNS guard, allowing Windows' private event-loop wakeup socket without allowing test scenarios to dial loopback. Keep run-specific failure/rerun evidence in the closeout PR description.

- Make concurrent probe release wait for all actual connections; a failed connection aborts before group submission and interruption cancels/awaits outstanding jobs. Correct the historical timing caveat without altering recorded measurements.
- Disable automatic pytest-qt plugin loading until the Qt runtime is introduced, so headless uv run pytest works with the current dependency set; re-enable during Qt implementation.

- Configure Hatchling to package the `src/dtflow` application package so editable `uv sync` installs can build DTFlow.

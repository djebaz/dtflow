# Phase 3 — Headless CLI and shared application services

**Status:** Phase 3A is implemented and offline-validated in the source-run CLI (see [implemented scope](#phase-3a-implemented-scope-and-limits)); Phase 3B's durable queue and rolling dispatch half is implemented and offline-validated (see [implemented queue scope](#phase-3b-queue-half-implemented-scope-and-limits)); the cloud asset half (3B-O6 to O9) is implemented and offline-validated per the [detailed Phase 3B completion plan](2026-10-04-phase-3b-cloud-assets.md); live `3-LG4` is not run.
**Prerequisite:** [Phase 2 backends and offline tests](phase-2-backends.md).  
**Roadmap:** [initial plan](initial.md).

## Goal

Deliver a working `dtflow` command-line application before Qt. The CLI must exercise the real Python backend adapters through application services and provide supported text-to-image generation plus functional, tested durable queues and rolling dispatch before Qt.

Dependency direction: CLI and Qt call shared services; services call core/backend contracts; adapters own provider protocols. CLI parsing, terminal formatting and Qt widgets must not own generation orchestration.

## Delivery sequence

Phase 2A supplies the minimal fake/service/SQLite/artifact slice. Phase 3A expands it into usable real-backend single-generation CLI/services, bounded LoRA selection/preflight, durable attempts, artifacts, logging and diagnostics. Phase 3B extends those same services with durable queues, rolling scheduling, restart reconciliation and custom cloud LoRA upload/reuse. Both gates must pass before Phase 4; full TanqueStudio parity remains later work.

## Phase 3A implemented scope and limits

Implemented: explicit `fake`/`local`/`cloud` selection through a composition root (no fallback; cloud credentials from `DRAWTHINGS_API_KEY`, local shared secret from `DRAWTHINGS_SHARED_SECRET`); bounded `check`, `models` (kind filter, provenance and unknown entries preserved) and `generate`; versioned credential-free configuration/metadata import and export with default < file < override precedence and per-field resolution evidence; exact LoRA selection with explicit weights, serialized by the Draw Things adapter as whole-model (`All`) LoRAs and verified against the decoded wire configuration; shared LoRA preflight (match/mismatch/unknown architecture, availability, fixed `application_unconfirmed` evidence) that blocks confirmed mismatches and absent LoRAs before any attempt exists; explicit `--prefix` trigger-prefix application; persisted cloud concurrency setting (`settings -j`, 1-4, default 4, not entitlement); the shared logging setup; documented exit codes. Defaults chosen where the plan left room: one seed default (`0`, matching the 2A slice), `-e HOST:PORT` for the local endpoint, `settings` as the concurrency command, mismatch blocks while unknown architecture proceeds with a diagnostic, and `-nc` accepted although no inventory disk cache exists yet.

Offline gate (3A-O1 to 3A-O8): covered by `tests/unit/test_cli.py` (O1, O2 composition, O4 filtering, O3 selection), `tests/unit/test_config_preflight.py` (O2, O5, O6), `tests/unit/test_logging_setup.py` (O7, O8), `tests/integration/test_cli_local_composition.py` (O3/O6 smoke) and the wire test for LoRA serialization. Owner-reported Windows result on the PR head: `uv run dtflow test` 237 passed, 3 deselected.

Owner-run live evidence (2026-10-04, Windows 10, Pentium E6500, reported by the owner, source-run unless noted):
- 3-LG1 local Draw Things: `generate -b local -e 192.168.1.142:7859 -m z_image_turbo_1.0_q6p.ckpt -s 42 -w 256 -y 256 -i 4 -g 1` reached terminal OK and saved a PNG plus `metadata.json` (attempt `1badda28…`).
- 3-LG2 direct DT+: the same workload with `-b cloud` reached terminal OK and saved the result (attempt `01b676f8…`); one small subscription generation.
- `uv run dtflow test -gen -live` passed (3 tests: DT+ token/inventory, DT+ generation smoke, local connectivity).
- Frozen PyInstaller CLI: fake workflow, settings, invalid input and per-user logs passed (needs `--collect-data drawthings_py`); `check -b local` against the real server returned transport ok and 56 inventory entries ([windows-packaging W20](../research/windows-packaging.md)).
- Frozen exe: invalid input exited 2 with no attempt recorded; a real DT+ generation passed (attempt `e9c09baa…`). The owner also reports a frozen local generation and a clean-`PATH` rerun passed (output not pasted).
- Local LoRA through the frozen exe (3A LoRA path): an unknown identifier was rejected by preflight with nothing dispatched; `zimage_turbo_lora_rank64_512_000006000_lora_f16.ckpt:0.8` with `-r` (preflight `match`) generated successfully on the second try (attempt `b61bfe9a…`). The first try lost its stream after one sampling step (`connection_lost`, recorded as an unknown outcome, never retried automatically; the owner reports the cause was a freeze of the Draw Things iOS app). The owner reports the LoRA result validated; whether the LoRA visibly changed the image was judged by the owner, not the agent. The agent did not inspect any output image.

Not done or unverified: the official base/refiner and Kandinsky/SD-v1 exception rules are not pinned, so no exception logic exists; LoRA application is still not confirmed by backend evidence; some server-reported LoRA architecture labels look wrong, and preflight blocks on a reported mismatch with no override yet; no queue, cancellation or cloud LoRA behavior was run live; raw inventory disk-cache read/write integration was planned here and landed afterwards with the desktop (see the runtime layout); the saved Windows credential provider is Phase 4, so cloud credentials are environment-only; the frozen exe was not tried on a second machine or stamped with a commit. Billing behavior remains unverified.

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

## Runtime paths

Use the [shared runtime layout](../architecture/runtime-layout.md): backend-neutral outputs grouped by attempt, durable state separate from disposable cache, and flat `cache/inventory/local_inventory.json` / `cloud_inventory.json` names, alongside `lora_inventory.json`. Keep endpoint/server observations inside JSON, not filenames; invalidate account-dependent evidence when credentials change. The derived LoRA cache writer exists and the raw inventory cache is written by `dtflow models` and the desktop refresh and read only as a desktop startup prefill. When persistent inventory caching is introduced, use an integer `CACHE_VERSION` and rebuild missing/mismatched formats; do not confuse cache format with durable-state schema. Live evidence stays under `test-runs/<run-id>/`, outside the application root. Create optional directories on demand.

## Shared logging and development mode

Implement the repository logging contract through one shared standard-library Python logging configuration. Use repository-root `logs/` for development and `%LOCALAPPDATA%\DTFlow\logs\` for packaged Windows builds; support an explicit destination override. Neither packaged path resolution nor logging setup may require Git or Qt.

Normal mode logs important events/errors; explicit development mode adds sanitized diagnostic detail and tracebacks. Determine canonical CLI spellings during implementation and keep help accurate. Development mode changes logging only; no generation, retry, network or billing semantics change.

Use rotating UTF-8 text files with bounded retention, timestamps, severity/component, version/commit/session and job/attempt correlation where available. Handle simultaneous-process file naming/rotation safely, keep Qt responsive and report write failures without leaking secrets. Exclude credentials, tokens, headers, raw image payloads and full prompts by default; sanitize exception paths/messages too.

Logs remain separate from durable execution records and disposable cache; `--no-cache` does not disable logging. The 3A-O7/3A-O8 logging groups below own destination/rotation/level/correlation, sanitization and simulated write-failure evidence; do not add a parallel CLI matrix. Do not submit generation merely to test logging.

## Shared services and persistence boundary

Services own request resolution/preflight, backend selection, submission, events, result validation and artifact saving. Keep services callable from Python as well as the CLI and future Qt UI.

Persist resolved prompts/seeds, model/checkpoint and relevant adapter identifiers, effective generation parameters, backend identity, dependency/version information available at runtime, output references and completion evidence. Require atomic execution-record updates and preserve partial evidence after failures.

A successful terminal RPC and valid outputs establish backend success; successful durable output/metadata saving establishes application success. Saved previews after failed generation remain labeled preview/diagnostic artifacts and never count as completed results.

### Minimal durable execution record

Before any generation submission, atomically persist an immutable resolved request plus a unique attempt ID, backend identity and credential-free configuration/version metadata. Before crossing the submission boundary, durably mark the attempt as potentially executing remotely. If either write fails, do not submit.

Use the standard-library SQLite store selected in the [backend contract](../architecture/backend-contract.md), with WAL, synchronous=FULL, short transactions, independent schema versions and a verified patched runtime. Canonical execution records are transactional; JSON is for credential-free configuration/metadata interchange, not dispatch ownership. Save image/sidecar files through same-directory temporary-file replacement with appropriate flush/fsync behavior before committing successful artifact references. Database and filesystem writes are not jointly atomic; reconcile orphaned/partial files conservatively. Define and test durability on the target filesystem.

Update outcomes and artifact references atomically as evidence arrives. Distinguish definitely not submitted, potentially executing/outcome unknown, confirmed failure and successful output persistence. Never persist credentials or bearer tokens.

On startup/record inspection, unfinished attempts that crossed the boundary remain unknown and cannot be automatically resubmitted. A crash between recording potential execution and sending the RPC may conservatively leave an unknown outcome; this is intentional. No remote job ID exists in the inspected contract to resolve it.

Reuse Phase 2A crash/write-boundary tests when the same service/store executes; test new Phase 3 queue state and composition boundaries in the mapped groups below. Previously saved artifacts remain evidence, not automatic proof of full completion.

Phase 3A completes the initial Phase 2A durable attempt/artifact foundation for the supported real backends. Phase 3B extends them with transactional queue/job state, rolling scheduling and local restart reconciliation. Do not claim remote recovery: the inspected protocol has no generation status/reattachment operation.

## Phase 3B — Durable queues, rolling execution and cloud assets

### Phase 3B queue half: implemented scope and limits

Implemented (3B-O1 to O5, O10, in `tests/integration/test_queue.py`): `dtflow queue add|list|show|start|pause|resume|cancel|cap` (`override` was removed by #132 and remains a hidden no-op); queue/item/control/event tables in `attempts.sqlite3` (user_version 2; a version 1 database migrates in one transaction after a `attempts.pre-v2.sqlite3` copy, newer versions are refused unchanged); exclusive dispatch through the existing 2A ownership lock (the OS lock dies with its process, so stale-owner recovery is the next open and a closed owner cannot dispatch; no lease was added); controls posted over a short separate connection and polled by the owner; reservation before attempt creation; rolling refill without a group barrier; accounting by backend across queues; restart reconciliation inside the open transaction; `unknown` blocking and recorded override (both removed by #132: unknown items now release their reservation and block nothing). Defaults chosen where the plan left room: `queue add` takes the generation options of `generate` plus `-k/--count` (seeds increment) and `-j/--jobs`, resolves items without contacting a backend and does not support `--prefix`; a new queue starts paused and `queue start` is the explicit resume; `start` runs until finished, cancelled or blocked and keeps polling while paused and idle; an override acknowledges every unresolved unknown reservation on the same backend and then stops counting them toward the cap; a reservation that crashed before its attempt row existed returns to pending, any other ambiguous attempt is never eligible again; an item whose attempt was interrupted before submission is `failed`, not retried, because attempt request identities are unique. Not done in the queue half: a live queue run, and any answer to what the maximum means beyond the client policy. Finding: `GenerationService` admits requests through a 256 MiB buffer budget while each real request reserves about 100 MiB (3 x the 32 MiB artifact limit plus previews), so real cloud or local runs currently admit about two concurrent generations whatever the queue maximum; the budget was left unchanged because it is a memory-safety decision.

### Phase 3B cloud half: implemented scope and limits

Implemented (3B-O6 to O9, in `tests/integration/test_cloud_assets.py` and `test_cloud_assets_cli.py`, in-memory DT+ and signed-storage fixtures): `dtflow assets prepare -f FILE [-n NAME]` (the only command that may upload), `assets list`, `assets show ID` (local registry views, no network) and `assets reconcile ID` (metadata-only, never uploads); `-z/--asset ID` on `generate` and `queue add` binds a *confirmed* local receipt to the `--lora` of the same identifier and fails before any attempt or queue exists otherwise; `GenerationService` (hence queue dispatch) revalidates each prepared reference with a metadata-only check and blocks a stale/inaccessible/unknown one before submission, with no upload. Prepared references are immutable values in the resolved request, attempt metadata and queue items, are never exported to portable configuration, and are rejected by local/fake backends. Identical concurrent prepares coalesce per API origin and auth client; a cancelled waiter does not cancel the shared operation.

Decisions: a fifth registry state `failed` records a definitely-not-transferred outcome next to `intent`, `transferring`, `confirmed` and `unknown`; a confirmed receipt requires the server's own reference, obtained by a fresh metadata check after a successful PUT (a PUT success without that confirmation stays `unknown`, never re-uploaded); one writer per state directory holds `assets.lock` so an unresolved operation seen at open belongs to a dead process; the CLI option is `-z` because every nearer mnemonic is taken; the LoRA keeps its exact identifier on the generation wire and a prepared LoRA missing from the Echo inventory passes preflight as `prepared` with architecture `unknown`.

Not done: live `3-LG4` (uploads and generation need explicit owner authorization), the exact remote identifier the generation wire expects for a custom LoRA, evidence-based size/refusal semantics and any non-secret content headers the signed endpoint requires beyond `Content-Type`/`Content-Length`, Windows-specific lock/durability runs, and the LoRA `--force` override (dropped by the owner).

Persist queue identity, stable ordered item IDs, immutable resolved requests, item/attempt states, backend selection and concurrency configuration. Use one dispatch owner per application state directory so two CLI processes cannot independently dispatch the same queue or bypass its cap. The CLI presents queue control; shared services own persistence, eligibility, scheduling and reconciliation. Store control requests transactionally for the dispatcher to poll, so basic Pause/Resume/Cancel needs no separate IPC server. SQLite transactions alone do not establish dispatch ownership: implement and test exclusive ownership, stale-owner recovery and protection against an old owner continuing dispatch. Never hold a database transaction while generating.

For ten independent eligible cloud items and a maximum of four, submit items 1–4. As any attempt reaches a known terminal outcome and releases its reservation, start the next eligible item immediately, without a group-completion barrier. Preserve original item identity and queue order in result presentation even when completion order differs. Reserve slots before remote submission and bound active attempts plus unresolved reservations. The tested local app/Bridge policy is one lane.

Pause stops new dispatch and lets active attempts finish. Resume starts only eligible never-submitted items, subject to any backend uncertainty block. Cancel prevents submission of pending selected items; for running attempts it requests interruption only as supported and must not imply confirmed remote cancellation. Completed artifacts remain preserved.

Since #132, an `unknown` outcome releases its reservation and blocks nothing, there is no override, and it is still never replayed; the next two paragraphs record the original Phase 3B design.

A definitely unsubmitted failure releases its slot. If remote execution remains possible after disconnect/local cancellation, retain an unresolved reservation and pause new dispatch for that backend while other active requests finish. An explicit acknowledged override may resume eligible work but cannot replay or mark the uncertain attempt resolved. Record the override; actual remote concurrency is no longer guaranteed after overriding unknown work.

After restart, restore the queue paused, retain completed results and unresolved reservations, and exclude ambiguous attempts from replay. Ordinary Resume cannot silently clear an uncertainty block. Changing the maximum downward does not cancel active requests; withhold new dispatch until reservation count is below the new cap.

Phase 6 adds pair/cross-product batch planning; Phase 7 adds advanced scheduling. Neither is required for this basic rolling execution path.

## LoRA scope before Qt

Follow [LoRA compatibility and cloud asset plan](lora-assets.md). Phase 3A implements structured local model/LoRA discovery, selection/weights and shared preflight for a bounded verified text-to-image scope. Phase 3B adds the separate direct-cloud upload/receipt/reuse path and validates using an uploaded custom LoRA in supported generation. Both are pre-Qt gates; do not leave cloud upload in Phase 11 by default.

Accept already converted Draw Things LoRA files initially. Audit required metadata and cloud generation references before committing the supported subset. Arbitrary safetensors conversion and a LoRA editor are separate work. Reuse server-acknowledged uploads without transferring weights for every generation or queue item; keep immutable asset references in resolved requests and revalidate metadata before dispatch.

Expose receipt/known-upload inspection without pretending it lists the complete cloud account. The inspected SDK has no usable remote list operation; upload responses supply no delete key. Remote listing/deletion and authoritative storage balance remain unavailable until verified. Unknown upload outcomes require explicit reconciliation, not automatic transfer retries or success claims.

## Testing and acceptance

### Ownership and existing execution tiers

Each behavior has one authoritative automated test layer. Higher layers test only behavior they uniquely add or necessary composition between tested layers. Reuse counts only when the same implementation executes; a new override or execution path needs focused coverage. Acceptance requirements do not imply one test function each. Prefer extending an existing scenario or parametrized test, while allowing a focused regression for a genuinely distinct boundary.

The source-run `dtflow test` wrapper already exists in the repository (introduced by PR #8); do not build another runner. `-all/--all` selects offline tests, `-live/--live` selects non-generative live checks, and `-gen/--generate` selects generation smoke tests. Groups combine additively; no flags means offline. Ordinary `uv run pytest` keeps live tests gated. Requested unconfigured live checks fail clearly; the generation group implements one bounded DT+ authorization/generation/saving smoke requiring DRAWTHINGS_API_KEY and an exact DTFLOW_LIVE_MODEL. The live group includes local Draw Things and DT+ token/inventory checks. The owner reports Windows offline/live runs and a bounded deployed DT+ generation at an earlier checkpoint using `z_image_turbo_1.0_q6p.ckpt`; final-head Windows repetition and broader compatibility remain pending.

Phase 2A owns unchanged credential selection, core output validation, base attempt-before-submission persistence and unfinished-attempt recovery. Phase 2B owns provider wire mapping (including seed zero, samplers and dimensions), codecs, TLS policies, generic stream/terminal behavior, preview/final validation and inventory parsing/classification. Phase 2C owns implemented offline coverage for cloud token lifecycle, authorization, response parsing, subscription-only fields and cloud-specific sanitization; optional cloud live checks exist with owner-reported success for the earlier tested workload, not final-head or broad compatibility acceptance. Shared infrastructure owns unexpected-network refusal and test selection. Do not duplicate these matrices in Phase 3. A CLI configuration scenario may assert a saved resolved seed of zero without decoding the provider wire again.

Occasional live workflows can replace duplicated high-level fixture happy paths, but cannot replace controlled tests for queue/persistence correctness, uncertainty, failed writes or cancellation boundaries. A small fixture composition smoke remains useful where it proves new wiring cheaply; do not require both fixture and live scenarios to repeat the same assertion matrix.

### Phase 3A scenario groups

| ID | Scenario group and authoritative layer | New evidence |
| --- | --- | --- |
| 3A-O1 | CLI surface/presentation; compact CLI tests | Headless startup/help/version using the shared application/build identity, implemented options, stdout/stderr and parametrized mapping for success, validation, rejection, interruption and unknown outcomes. Invalid CLI input prevents dispatch. |
| 3A-O2 | Configuration resolution and persistence; service plus one CLI composition | Import/defaults/overrides → immutable effective request → output metadata, including application version/full source commit where available; distinctive parameter values, round trips and no-cache bypass. Reuse base attempt/output durability tests. |
| 3A-O3 | Real-backend application composition; narrow local/cloud fixture smoke where needed | Backend selection reaches the intended adapter through CLI/services and saves the application result. No repeated protocol assertions; live evidence can replace duplicated high-level happy paths. |
| 3A-O4 | Application inventory presentation/filtering | Only new application behavior: selection/display of supplied provenance and unknown entries, without inventing compatibility. Existing adapter tests own refresh/parsing/classification when unchanged. |
| 3A-O5 | LoRA compatibility/preflight; parametrized pure/service tests | Match/mismatch/unknown and supported official exception/refiner rules; confirmed incompatibility prevents dispatch. Keep availability distinct from compatibility. |
| 3A-O6 | LoRA/config fidelity; service and one CLI smoke | Exact identifier/weight, explicit trigger-prefix handling, config round trip and immutable resolved request; preflight before dispatch without silently replacing selections. |
| 3A-O7 | Logging lifecycle; focused logging tests | Destination, levels, rotation and session/job/attempt correlation together; bounded retention and process-safe naming. |
| 3A-O8 | Logging failure/sanitization | Conspicuous fake secrets absent from output/logs; write failure leaves generation/retry/billing semantics unchanged. |

### Phase 3B scenario groups

| ID | Scenario group and authoritative layer | New evidence |
| --- | --- | --- |
| 3B-O1 | Canonical 10-item / 4-slot rolling queue; fake backend + events + SQLite | Items 1–4 start, out-of-order completion admits item 5 before the initial group completes, reservation count stays bounded, refill continues and stable item/result identity survives completion order. These assertions belong in this central scenario, not separate integration tests. |
| 3B-O2 | Queue controls; small controlled service/CLI scenario | Pause allows active completion but prevents refill; Resume admits eligible work; pending Cancel prevents dispatch; running interruption preserves honest evidence and completed outputs. |
| 3B-O3 | Uncertainty/restart/override; durable service scenario | Uncertain reservation blocks replacement, survives paused restart, never replays, and ordinary Resume cannot clear it; explicit override is recorded without resolving or replaying the uncertain attempt. Since #132: unknown items block nothing and never replay; override is gone. Cover new queue write/crash boundaries beyond base 2A attempts. |
| 3B-O4 | Scheduler policy; cheap arithmetic plus controlled dispatcher scenario | Local one-lane execution, cap reduction while work is active and eligible ordering. Prove the dispatcher obeys its policy, not arithmetic alone. |
| 3B-O5 | Dispatch ownership; focused real process-boundary evidence where needed | Second dispatcher excluded, recovery after owner exit, and old owner cannot continue dispatch. Reuse 2A ownership where applicable; do not introduce leases solely to satisfy a test plan. |
| 3B-O6 | Asset prepare/upload/reuse; asset-service HTTP fixtures | POST → signed PUT → durable receipt → restart → validated reuse without another transfer; already-existing branch performs no PUT. Check checksum, size, identifier and bounded transfer behavior together. |
| 3B-O7 | Asset dedup/content/scope identity | Concurrent identical preparation coalesces; changed content and backend/account scope stay distinct. Use cheap parametrized identity/parser checks where sufficient. |
| 3B-O8 | Asset uncertainty/persistence/sanitization | Both uncertain transfer and completed transfer followed by failed receipt write preserve evidence and prevent blind re-upload. Cover explicit reconciliation, representative storage/auth refusals, malformed/inconsistent responses and token/signed-URL redaction at their lowest new authoritative layer. |
| 3B-O9 | Queue/CLI asset references | Immutable prepared identifier/checksum reaches queued resolved requests without hidden upload; stale/inaccessible receipt fails clearly. Local known-upload inspection does not claim remote listing/deletion. |
| 3B-O10 | Saved-state compatibility; canonical historical fixtures | Unsupported-newer refusal and supported migration failure preserve original state. Add real released upgrade fixtures only when schemas exist and require migration, including completed outputs, stable IDs, uncertainty and paused restoration. Do not invent migrations to meet a test count. |

These eighteen groups are an outline, not a function-count quota or ceiling. A group may use several small tests or parametrized cases; combining unrelated failures into a giant test is not required. Every additional test should identify a distinct uncovered boundary, irrespective of the existing count. The feature requirements elsewhere in this plan remain intact.

### Separately selected live workflows

Reuse the existing local non-generative check and the existing 2C cloud check; do not add another connection test merely because Phase 3 exposes a CLI. Add composition assertions only if the application path differs materially.

| ID | Planned live-generation workflow | Evidence |
| --- | --- | --- |
| 3-LG1 | Local Draw Things generation | Actual application/CLI → local adapter → generation → durable final image and metadata. |
| 3-LG2 | Direct DT+ single generation | Actual release credential/auth/application path → cloud generation → durable result. |
| 3-LG3 | Small cloud rolling queue | For example four items/two slots through actual application/backend composition; deterministic 10/4 remains the scheduler authority. |
| 3-LG4 | Supported custom cloud LoRA preparation/reuse/use | Upload or server acknowledgement → supported generation → durable receipt → reuse without another transfer. Record exact model/LoRA and trustworthy application evidence; a receipt or plain txt2img success alone is insufficient. |

These workflows are planned, not executed or implemented. Make each individually selectable when introduced so checking one backend does not require both endpoints or a custom LoRA, or implicitly run every paid workload. Determine a canonical short/long selector pair then; the current wrapper has no individual-workflow selector. Do not advertise an unimplemented command. The `-gen` group remains an explicit generation opt-in; non-generative `-live` never submits generation. Agent-executed generation/upload still requires task authorization for the named bounded workload. Use small explicit workloads, no ambiguous retry/fallback, and record actual model/backend/build identity and checks. No real generation, upload or download is needed to edit this plan.

Live workflows establish deployed compatibility, not deterministic no-replay, slot accounting, Pause/Cancel, ownership, migration/write failure, redaction or controlled cancellation evidence. Keep those offline. Release gates consume this evidence rather than defining another suite.

### Fixtures, runtime and handoff

Use pure functions for parsing/policy and fake backends with controlled `asyncio.Event`s/futures, injected clocks and tiny payloads for orchestration. Use real SQLite for transactional queue/restart behavior. Reuse HTTP/gRPC/TLS fixtures only where testing new adapter composition or asset protocol behavior needs them. No sleeps for expiry, queue polling or artificial generation delay. Subprocesses are for actual CLI/process ownership/crash boundaries, not every logical state.

Measure default offline wall-clock time and slowest tests before Phase 3, after 3A and after 3B using `uv run pytest --durations=10`. The wrapper does not currently forward `--durations`. Record the environment and added runtime in implementation PRs; review dominating tests for a cheaper authoritative layer. Test counts and test/source LOC ratios are not release gates. Avoid rigid runtime limits before comparable measurements exist.

Run canonical syntax/import/help, pytest and Ruff checks for implementation changes. Preserve exact endpoint guards and offline defaults. Record owner-run Windows and separately authorized deployed evidence distinctly; source success does not prove Windows packaging. Retire superseded assertions when an authoritative scenario replaces them rather than accumulating the old checklist underneath this plan.

## Scope and later expansion

“Complete CLI” means an end-to-end application for supported operations, not all TanqueStudio features in one phase. Pair/cross-product queue matrix expansion, advanced scheduling, workflows, advanced image/control combinations, video, arbitrary LoRA conversion and broader asset management remain later work. Bounded LoRA selection and custom cloud LoRA upload/reuse are required in this CLI phase after contract verification.

Track the intended port in the [TanqueStudio parity matrix](tanquestudio-parity.md); later work requires an explicit milestone rather than indefinite deferral. This matrix is not a requirement to expose every feature before Qt.

Model discovery and bounded LoRA selection/weights arrive in Phase 3A. Custom cloud upload/reuse arrives in Phase 3B through a separate asset service with the same credential policy. Phase 5A exposes those existing services in Qt. If format or deployed API evidence blocks the agreed subset, record the blocker explicitly; do not silently move it behind Qt or advertise it as usable.

Phase 4 adds the minimal Qt shell and connection management after both the single-generation and rolling-queue CLI gates pass. Phase 5A adds GUI generation/configuration reuse and Phase 5B exposes the existing queue controls, both through these services and both required for the first desktop release. Later features should expose the same underlying services through the CLI wherever practical so Qt remains presentation rather than a second execution engine.

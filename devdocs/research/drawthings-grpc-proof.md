# Draw Things gRPC connectivity proof

## Goal

Prove the smallest useful Draw Things connection outside the DTFlow GUI before committing the runtime architecture to a specific Python client implementation.

## Scope

Validate the current Draw Things gRPC interface and the Python-side integration needed for DTFlow:

- connection configuration
- protocol/API compatibility
- one controlled generation request
- progress and result behavior
- error behavior
- cancellation capabilities, if exposed
- concurrency behavior

## Constraints

- Keep the proof outside the PySide6 UI.
- Keep Draw Things/protobuf-specific types behind the backend boundary.
- Verify current APIs before adding gRPC, protobuf, or Draw Things client dependencies.
- Persist or print enough request metadata to reproduce any generation that is intentionally run.
- Use controlled prompts, seeds, dimensions, and parameters when comparing behavior.
- Do not run inference merely to validate imports, configuration, or transport plumbing.
- Any live generation must be explicit and separately gated.

## Initial implementation sequence

1. Identify and verify the current canonical Draw Things gRPC/protobuf contract and the current Python client options.
2. Decide whether DTFlow should consume generated bindings from a dependency or generate/own them.
3. Add only the dependencies required by the verified approach through uv.
4. Implement a minimal command-line connectivity/protocol probe under `tools/drawthings/`.
5. Validate non-generative connection and error behavior first.
6. Run a single controlled generation only when required to establish generation/progress/result semantics.
7. Record observed cancellation and concurrency behavior without assuming support.

## Phase 1 closeout decision

**Proposed acceptance:** Close Phase 1 as a bounded connectivity and generation feasibility proof when this closeout PR is merged. It establishes that the selected Python stack can reach the tested Draw Things path and receive/save generation outputs, with separately attributed direct-cloud evidence. It does not certify production adapters, every original checklist item, remote cancellation, packaging or cloud entitlement.

**Evidence baseline:** [main after PR #3](https://github.com/djebaz/dtflow/commit/e4e6538c7f32c80d1e6c0c4a01dc505bfaa8b6b7). Historical live observations and timings below are retained; this closeout does not repeat them. “Recorded” means existing repository evidence, “user-reported” means prior operator runs, “source-inspected” means code behavior without execution, and “offline verified” means this PR's stubbed checks.

| Phase 1 criterion | Evidence and status | Remaining gap / owner |
| --- | --- | --- |
| Connection configuration | Recorded Windows gRPC/model-browser success; source probe accepts host/port and explicit cloud mode. Offline checks verify argument validation/legacy aliases and non-generative connection-only control flow. | Actual bad endpoint/TLS/shared-secret behavior: Phase 2B transport fixtures; cloud transport/auth distinctions: 2C. |
| Protocol/API compatibility | Recorded successful discovery and decoded outputs using Python 3.13, drawthings-py 0.4.0 and NumPy 2.3.5. PR #3 separately records installed generated-service inspection. User-reported cloud runs exercise the token/auth/TLS-gRPC path. | Wire fidelity, terminal statuses and future-version compatibility: 2B/2C fixtures. No universal protocol/model compatibility claim. |
| One controlled generation request | Recorded local generation/saved images; user-reported direct-cloud controlled runs. Workload/preset/prompts/seeds are defined in the probe. | Effective wire dimensions/seed-zero and complete request provenance: 2B/2C; application persistence: 2A/3A. No new live run is needed for feasibility closure. |
| Progress behavior | Source-inspected callback support in the dependency; the current probe disables its progress bar and has no captured per-signpost/preview trace. Detailed production progress is unverified. | Signpost/preview ordering and bounded event handling: 2A fake and 2B/2C protocol fixtures. Capture real progress separately only with an authorized workload. |
| Result behavior | Recorded image decoding/saving; offline checks verify empty output becomes a failed job. The probe saves only the first returned image, consistent with its one-image lightweight scope. | All required artifacts, previews, partial/chunked output and successful terminal completion: 2B/2C fixtures and 3A saving. Full-preset/multiple-output completeness is not accepted here. |
| Error behavior | Source-inspected reporting paths; offline checks verify malformed JSON/non-object responses, sanitized HTTP status reporting, invalid arguments, empty output and connection failure before group release. Real transport/status/billing taxonomy remains unverified. | Actual terminal gRPC errors, TLS failures, partial-output-plus-error and auth expiry/refusals: 2B/2C fixtures. Documented or authorized sanitized live evidence is required for production billing classifications. |
| Cancellation capabilities, if exposed | Offline verified: cancelling the probe group propagates cancellation, cancels outstanding client tasks and exits connected service contexts before or after release. Confirmed remote cancellation is unverified. A separate cancel-generation RPC is unsupported by the inspected protocol. | Real stream closure/cleanup and uncertain outcome evidence: 2B/2C. Controlled authorized observations are needed per production backend to claim remote effect; otherwise keep outcome unknown. |
| Concurrency behavior | Historical user-reported timings below establish overlapping client windows and support different local/cloud scheduling policies. Offline checks verify the corrected readiness barrier and no submission if a connection fails. | Exact remote workers/slots/entitlement remain unknown. Phase 3B tests rolling dispatch/reservations separately; production concurrency requires separately authorized evidence. |

### Probe hardening and verification in this closeout

The earlier concurrent harness waited 0.25 seconds before release, which did not guarantee all channels were ready. It now waits for explicit readiness from every connected job, aborts the group if a job fails before release, and cancels/awaits remaining jobs on failure or interruption. Wall timing starts at release after readiness; per-job timing still includes generation authentication/stream work and excludes file saving. Historical measurements were produced by the earlier harness and are not recalculated or promoted into controlled connection-readiness evidence.

Headless pytest startup previously failed because the declared pytest-qt plugin loaded without any declared Qt runtime. Disable its automatic loading in pytest configuration until Qt implementation adds the runtime and re-enables the plugin. No dependencies or lock entries change.

Verification on Linux CPython 3.13.15 in a reconstructed checkout using the repository's locked project inputs:

- Initial uv run pytest: 10 offline checks passed on Linux; after the Windows test-guard correction, 11 passed on Linux.
- uv run ruff check . and uv run ruff format --check .: passed.
- Probe --help: executed successfully without network or generation.
- Markdown relative targets, LF/final newlines and paired fences: checked before publishing.

The supplied Windows run (Python 3.13.13) reported six failures and four passes: the guard blocked asyncio's internal loopback wakeup socket during event-loop construction. The subsequent _ssock/unawaited-coroutine warnings followed failed initialization. Tests now create an asyncio.Runner/event loop before installing the guard and execute scenarios through that runner under the guard. An additional check proves test code still cannot dial loopback or resolve DNS; no blanket loopback exception or production event-loop-policy change is introduced. A fresh Windows rerun of this correction is still required before claiming Windows acceptance.

Checks replace services/HTTP responses and refuse socket connections/DNS in test execution. They do not run an actual gRPC fixture server or validate production cancellation, detailed progress, Windows packaging or quotas. Phase 2's full external-network guard remains a separate deliverable. No training, inference, checkpoint download or potentially billable request was performed; GitHub requests read/publish this PR.

### Handoff and closure limits

After accepting this closeout, open the separate Phase 2A implementation PR. Its fake/service/SQLite/artifact slice is already specified in [Phase 2](../plans/phase-2-backends.md). Phase 2B and 2C own the real-adapter fixture gaps above. Missing evidence stays unverified; no gap may be silently changed into a supported feature. Use explicit authorization for any later real generation, including Bridge mode.

## Observed compatibility evidence

The current tested Windows development machine uses a legacy Intel Pentium E6500 and Python 3.13.

Observed locally on that machine:

- NumPy 2.5.3 fails to import because `_multiarray_umath` cannot initialize.
- NumPy 2.4.4 fails with the same import error.
- NumPy 2.3.5 imports successfully.
- `drawthings-py==0.4.0` imports successfully when NumPy 2.3.5 is installed despite its declared `numpy>=2.4.4` requirement.
- A normal `uv sync` with the DTFlow NumPy override reproduces the working environment.
- The non-generative Draw Things gRPC/model-browser probe succeeds with Python 3.13, NumPy 2.3.5, and `drawthings-py==0.4.0`.
- Live generation succeeds through the same stack: both returned images were decoded and written successfully.
- The concurrent probe has observed multiple client `generate()` calls outstanding at the same time.
- User-reported iPhone-server timings show concurrent requests completing in a pronounced staircase, while matching serial baselines are faster overall. This is consistent with an effective single generation lane in the tested Draw Things gRPC/Bridge path.
- User-reported direct DT+ Cloud Compute timings show substantially different behavior: up to six controlled requests have completed within one broad generation-time window, with all request windows overlapping. This is strong evidence of non-serialized direct-cloud execution for the tested workload, without establishing a GPU count or fixed concurrency-slot count.

DTFlow therefore keeps `drawthings-py` bounded to the 0.4.x release line and uses a uv override for NumPy 2.3.5 in this compatibility path. A future `drawthings-py` 0.5.x release must be evaluated before changing that bound or carrying the override forward.

Generation, result decoding, image saving, and concurrent in-flight client requests are now exercised. Cancellation behavior and actual backend parallel-execution semantics remain unverified.

## Probe workload modes

The probe defaults to a deliberately lightweight local workload suitable for iPhone testing:

- `z_image_turbo` preset
- requested 300 x 300 image size
- 4 steps
- batch count 1
- batch size 1
- high-resolution fix disabled

With `drawthings-py==0.4.0`, the `z_image_turbo` preset targets `z_image_turbo_1.0_q6p.ckpt`. Draw Things may round requested image dimensions to a supported multiple.

The current compact CLI is `-a/--host`, `-p/--port`, `-r/--preset`, `-j/--jobs`, `-f/--full`, `-s/--serial`, `-o/--out`, `-c/--conn`, `-k/--skip-conn`, and `-d/--cloud`. The previous `-H`, `-P`, `--full-workload`, `--sequential`, `--output-dir`, `--connection-only`, and `--skip-connection-test` spellings remain accepted as hidden compatibility aliases.

Use `-j N` / `--jobs N` to select 1-8 controlled jobs (default: 2); each job uses a fixed mug-color prompt and deterministic seed. Use `-f` / `--full` to keep the selected preset's native generation settings. Use `-s` / `--serial` to run the same selected jobs one after another for a baseline against the default concurrent run.

### Direct DT+ cloud diagnostic mode

`-d` / `--cloud` bypasses the Draw Things/iPhone server and targets DT+ Cloud Compute directly for controlled diagnostics. It reads `DRAWTHINGS_API_KEY` from the environment and does not accept credentials on the command line. The probe uses the current public MediaGenerationKit contract as its reference: obtain a short-term token from `/sdk/token`, obtain a per-generation gRPC token from `/authenticate`, then call `compute.drawthings.ai:443` over TLS with the bearer token. Credentials and derived tokens must never be logged or persisted.

The direct-cloud probe deliberately leaves `consumableType` and `amount` unset, so it does not opt into PAYG billing. A successful live generation can still consume DT+ subscription quota/credits. This mode is diagnostic tooling only; it is not the planned production DT+ backend.

## Architectural outcome

The live direct-cloud tests establish an important feasibility result for DTFlow: the DT+ generation path can be exercised end to end from Python without running MediaGenerationKit or a Swift bridge.

The user-reported successful runs exercised:

- API-key authentication through `/sdk/token`;
- the DT+ short-term token;
- per-generation `/authenticate`;
- direct TLS gRPC connectivity to `compute.drawthings.ai:443`;
- authenticated `GenerateImage` requests;
- returned image decoding and file output through the existing Python client stack;
- multiple independent direct-cloud clients submitting concurrently.

MediaGenerationKit remains valuable as the public reference implementation used to understand and verify the protocol contract, but it is not a runtime dependency of the proven Python diagnostic path.

This changes the remaining DT+ question from "is a Python implementation practical?" to "how should the proven path be hardened into a production backend?" The current probe is not production-complete. Token refresh/expiry, credential storage, entitlement and PAYG edge cases, cancellation, retries, throttling, progress/error mapping, persistence/recovery, broader model/input compatibility, and a safe production concurrency policy remain to be designed and verified.

A Swift/MediaGenerationKit bridge should therefore be treated as a fallback for future protocol capabilities that cannot be implemented reliably in Python, not as the default DT+ integration plan.

## Concurrency findings

The measurements below are user-reported live runs from 2026-10-04 using the controlled lightweight `z_image_turbo` workload: requested 300 x 300, 4 steps, one image per job, fixed per-job prompts, and deterministic seeds.

| Target | Jobs | Mode | Per-job `generate()` durations | Wall time |
| --- | ---: | --- | --- | ---: |
| iPhone Draw Things with Bridge Mode | 3 | concurrent | 8.23 s, 17.63 s, 31.26 s | 31.30 s |
| iPhone Draw Things with Bridge Mode | 3 | serial | 7.28 s, 8.00 s, 9.64 s | 25.20 s |
| direct DT+ Cloud Compute | 2 | concurrent | 9.93 s, 9.43 s | 10.74 s |
| direct DT+ Cloud Compute | 3 | concurrent | 9.06 s, 9.23 s, 9.50 s | 10.22 s |
| direct DT+ Cloud Compute | 6 | concurrent | 7.17 s, 10.22 s, 13.00 s, 8.00 s, 12.52 s, 9.26 s | 13.84 s |
| direct DT+ Cloud Compute | 6 | serial | 8.06 s, 9.88 s, 8.80 s, 8.59 s, 10.03 s, 9.07 s | 59.46 s |

For the six-job direct-cloud comparison:

- the serial run reports 54.42 s of summed `generate()` duration, or 9.07 s per job on average;
- the concurrent run has 60.17 s of aggregate request duration inside 13.84 s of wall time, with 7.17 s where all six request windows overlap;
- comparing the serial summed `generate()` duration with concurrent wall time gives about 3.93x generation-path throughput improvement;
- average individual latency rises from 9.07 s serially to about 10.03 s concurrently, roughly 10.6%, with larger variance on some jobs.

The serial wall time is not used for that throughput ratio because the serial harness creates a fresh connection before each job and therefore includes repeated connection/setup time outside each measured `generate()` duration. In the historical harness, concurrent wall timing began when the release event was set after a fixed 0.25-second delay; it did not verify every connection was ready. The closeout hardening above adds that readiness guarantee for future runs. Historical per-job overlap measurements remain separately reported.

These timings do not identify the number of GPUs, workers, execution slots, or the DT+ scheduling algorithm. They do establish that the tested direct-cloud path is not behaving like a one- or two-slot serialized queue: all six requests were outstanding together and all six completed in 13.84 s, whereas the same six jobs required 54.42 s of summed generation time serially.

By contrast, the tested iPhone Draw Things Bridge path behaves effectively as a single generation lane for DTFlow scheduling purposes. The three-job Bridge run completed in a staircase at 8.23 s, 17.63 s, and 31.26 s, and its concurrent wall time was slower than the matching serial baseline.

### Source-level scheduling evidence

Current public Draw Things source inspection supports, but does not fully prove, the Bridge interpretation:

- `ImageGenerationServiceImpl` dispatches generation work onto an injected `DispatchQueue` before calling the image generator.
- The public reference gRPC server constructs that queue without concurrent attributes, so its generation dispatch is serial.
- The public service exposes a `bridgeMode` flag, but current public code does not use that flag to create a separate concurrent scheduling path.
- The exact production iOS app wiring that supplies the queue and swaps/configures the Bridge generator is not public in the inspected repository, so the iPhone implementation should not be claimed to be source-proven identical to the reference CLI.

For DTFlow, treat one tested Draw Things gRPC server as one effective generation lane unless a future server/app version is measured otherwise. Keep direct DT+ concurrency as a separate backend concern; the diagnostic results show substantial parallelism but do not define a safe production concurrency cap.

Generated probe images and reports are written under `tmp/drawthings_concurrency/` by default, which is repository-local and ignored by Git.

## Evidence to capture

Record the exact dependency versions, Draw Things version/environment used for live testing, connection settings excluding secrets, request parameters, observed status/progress/result behavior, and any protocol limitations discovered.

## Out of scope

- PySide6 UI integration
- durable jobs or queues
- workflow execution
- production DT+ backend integration
- speculative backend behavior

## Follow-up implementation direction

The [updated roadmap](../plans/initial.md) brings a narrow native Python direct DT+ adapter into Phase 2 alongside the Draw Things adapter and an offline fake. The [Phase 2 plan](../plans/phase-2-backends.md) defines completion, preview labeling, cancellation uncertainty, token lifecycle and subscription-only behavior. Broader cloud operations remain Phase 11 work.

The [ecosystem research](dtflow-ecosystem-research-report.md) records source-level findings and unresolved evidence needs. This follow-up changes planning only; it does not add runtime capabilities or repeat the recorded live tests above.

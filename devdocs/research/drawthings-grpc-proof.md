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

The serial wall time is not used for that throughput ratio because the serial harness creates a fresh connection before each job and therefore includes repeated connection/setup time outside each measured `generate()` duration. Concurrent wall timing begins only after the independent clients have reached the shared release barrier.

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

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
- The concurrent probe has observed both client `generate()` calls outstanding at the same time.
- User-reported timing from multiple runs shows the second request completing substantially later than the first, which is consistent with possible server-side queuing or serialization but does not prove the backend execution model.

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

Generated probe images and reports are written under `tmp/drawthings_concurrency/` by default, which is repository-local and ignored by Git.

## Evidence to capture

Record the exact dependency versions, Draw Things version/environment used for live testing, connection settings excluding secrets, request parameters, observed status/progress/result behavior, and any protocol limitations discovered.

## Out of scope

- PySide6 UI integration
- durable jobs or queues
- workflow execution
- production DT+ backend integration
- speculative backend behavior

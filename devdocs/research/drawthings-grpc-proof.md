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
- The non-generative Draw Things gRPC/model-browser probe succeeds with Python 3.13, NumPy 2.3.5, and `drawthings-py==0.4.0`.

DTFlow therefore keeps `drawthings-py` bounded to the 0.4.x release line and uses a uv override for NumPy 2.3.5 in this compatibility path. A future `drawthings-py` 0.5.x release must be evaluated before changing that bound or carrying the override forward.

This evidence does not yet establish that generation, result decoding, image saving, cancellation, or concurrent generation work with NumPy 2.3.5. Those remain explicit follow-up tests.

## Evidence to capture

Record the exact dependency versions, Draw Things version/environment used for live testing, connection settings excluding secrets, request parameters, observed status/progress/result behavior, and any protocol limitations discovered.

## Out of scope

- PySide6 UI integration
- durable jobs or queues
- workflow execution
- DT+ Cloud Compute
- speculative backend behavior

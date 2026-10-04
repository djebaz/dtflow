# drawthings-py replacement estimate

**Status:** Estimate for planning; nothing here authorizes replacing or copying code. See [decision 0001](../decisions/0001-drawthings-py-license.md).  
**Purpose:** Size the work to stop depending on `drawthings-py` 0.4.0 (GPL-3.0-only), so the distribution decision can be made with real numbers.  
**Method:** Line counts and imports were read from the installed 0.4.0 package (observed). Upstream sources were read from pinned checkouts (source-read). Effort sizes are DTFlow's rough estimates relative to each other (S = days, M = about a week or two, L = several weeks), not measurements.

## Evidence base

| Source | Pin | License | Used for |
| --- | --- | --- | --- |
| `drawthings-py` 0.4.0 (installed) | `uv.lock` at DTFlow `fb1615e`; runtime closure unchanged at `a357f1f` | GPL-3.0-only | Structure, sizes, imports |
| `drawthingsai/draw-things-community` | `e51089d5229b3147e13a21cfe0c379deff851715` (2026-10-04) | GPL-3.0 (`LICENSE.md`, CLA for contributions) | Wire schema `imageService.proto` (190 lines), configuration schema `config.fbs` (191 lines) |
| `skeptict/TanqueStudio` | `6474f1ea575afa50dea92ab56ef01b5bf50d1bd7` | README states MIT; no LICENSE file found in the checkout | Independent Swift implementation of the same protocol and tensor decoding (design reference only until its license is clarified) |

Other independent references already recorded in the [ecosystem report](dtflow-ecosystem-research-report.md): `dts-utils` (MIT) for Python streaming, chunk and codec algorithms.

## Components

Counts are Python lines in the installed package.

| Component | What it does in drawthings-py | Size (observed) | Third-party deps | Replacement work | Fixtures needed | Effort |
| --- | --- | --- | --- | --- | --- | --- |
| Protocol bindings | betterproto output for `ImageGenerationService` (`generated/dt_grpc/image_service/__init__.py`) | 502 lines | betterproto, grpclib | Regenerate from `imageService.proto`, or hand-write the used messages (`ImageGenerationRequest/Response`, `EchoRequest/Reply`, signposts). The schema file is GPL-3.0 in the official repository, so regeneration needs the license review in the decision record | Local gRPC fixture server (Phase 2B) exercising echo and generate round trips | M, license-gated |
| FlatBuffer configuration | flatc output (`generated/dt_grpc/config_generated.py`) plus the configuration model and presets (`configs/`: `gen_config`, `config_prop`, `enums`, `presets`, `types`, ~2,350 lines) | about 4,100 lines combined | flatbuffers | Implement only the txt2img subset first (model, size, steps, guidance, sampler, seed, batch, LoRA list). The full parameter set is much larger: TanqueStudio models about 39 fields and its notes cite 58 in an imported image's metadata. `config.fbs` is GPL-3.0 upstream | Wire-level assertions on the encoded configuration, including seed zero and size units (Phase 2B/2C) | S for the subset, L for broad parity |
| Tensor and image codec | Decode returned tensors (`image/image_buffer.py` 481 lines, `_preview_decoders.py` 657, `_png_writer.py` 262, `_metadata.py` 338, `grpc/audio.py` 145) | about 1,900 lines | numpy, Pillow, fpzip (BSD-3) | Reimplement header parsing and the codecs the server uses (fpzip, zip, ezm7 per TanqueStudio's `DTTensorDecoder.mm`), previews and PNG metadata writing. Protocol knowledge is available from several independent implementations; code reuse is license-limited | Captured real tensors (one image, one preview) as small fixtures, plus corrupt and truncated variants | M |
| Client and stream layer | `grpc/grpc_service.py` (393), `_dt_service.py` (111), `request_builder.py` (425), `image_generation_result.py` (165), `util/` helpers | about 1,400 lines | grpclib | DTFlow's own adapter will sit here anyway; the backend contract already requires explicit lifecycle, deadlines and cancellation, which the library only partly provides | The same Phase 2B fixture server | S to M |
| Direct DT+ authentication | Not provided by drawthings-py; the probe and the planned DTFlow cloud adapter implement it | not applicable | stdlib HTTP or selected async client | Unchanged by a replacement | Phase 2C HTTP mocks | none (already owned by DTFlow) |

### Observed behaviors a replacement must not copy blindly

- `util/seed_provider.py` treats a requested seed of `0` (and `-1`, and `None`) as "choose a random seed". DTFlow's contract requires seed zero to be preserved, so the adapter must handle this regardless of whether the library is replaced.
- The client assumes TLS with a CA file bundled in the package and `check_hostname = False`; DTFlow's plan is a per-instance channel and injected trust, not mutation of the library's module global.
- Chunked output and heuristics for video/audio metadata are noted in [research §10.3](dtflow-ecosystem-research-report.md) and stay out of authoritative metadata.

## What is already isolated

The [backend contract](../architecture/backend-contract.md) confines `drawthings-py` builders, codecs, bindings and transport to one integration module beneath `backends/`. Core, services and persistence never import provider types. A replacement therefore changes that module and its workaround ledger, not callers. The Phase 2B transport fixtures and the contract tests are the acceptance suite for any replacement.

## Overall sizing

- **Txt2img-only replacement:** protocol bindings, configuration subset, tensor decode, and an adapter: roughly M + S + M + S ≈ one to two months of focused work, dominated by fixtures and the license review. This is a planning estimate.
- **Replacement with broader parity** (img2img, control, LoRA, video): the configuration and codec components grow; treat it as a separate project after the txt2img slice.

## Open questions before committing to replacement

1. Does regenerating bindings from the GPL-3.0 `.proto`/`.fbs` files avoid the problem the replacement is meant to solve? Needs legal review; do not assume.
2. Does the official project publish the wire schema under a more permissive license or a documented client-use grant? Not found in the inspected checkout; unverified.
3. Is the independently implemented TanqueStudio protocol code reusable? Its license is unresolved (README claims MIT, no LICENSE file at the pinned commit).
4. Do other maintained Python clients exist with acceptable licenses? Not surveyed here.

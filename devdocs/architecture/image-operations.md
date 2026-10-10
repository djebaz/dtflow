# Image-operation preparation

## Current slice and boundaries

Draft PR #64 starts Phase 9A/9B prerequisites before the smart editor. `core/image_inputs.py` defines immutable provider-neutral content/fit/preparation records; `services/image_preparation.py` owns bounded Pillow operations. These are callable headless building blocks only: no GUI, CLI image-operation dispatch, request-schema change, provider call, file write, model inference or new dependency is introduced in this slice. Existing txt2img behavior is unchanged. No runtime/accuracy/CPU-speed acceptance is claimed.

The [implementation plan](../plans/2026-10-10-smart-mask-inpainting.md) records the approved interaction design and backend-first order. A first exact checkpoint/backend acceptance is a later owner gate before editor/analysis promotion, not an automatic run.

## Preparation contract (source implementation)

- File reads snapshot at most 64 MiB actual bytes. Decode rejects images over 16 million pixels and animated/multiframe inputs; MPO explicitly uses primary frame 0.
- EXIF orientation is applied before coordinates/dimension comparison. Source is RGB with transparency composited on white. Canonical PNG snapshots contain fresh pixels only, without inherited EXIF/ICC, prompts, paths or provider references. Encoded bytes are excluded from dataclass repr.
- Mask luminance strictly above 127 selects pixels; alpha is ignored. Source and mask must have identical EXIF-oriented dimensions. Do not resize an arbitrary mismatched mask silently.
- Masks are binary 0/255: white edits, black preserves. Empty mask after threshold/adjustments is rejected. Grow/shrink is an integer in -64..64 source pixels using Pillow square-kernel Max/Min filters and a black source boundary; not anatomical expansion.
- Feather is finite in 0..64 source pixels. Gaussian softness is multiplied by the adjusted binary support, so the blend alpha is exactly zero outside the selected region. Defaults are grow 0 and feather 0.
- Target dimensions remain multiples of 64 in 128..2048. Source/mask share centered aspect-preserving fitting with rounded integer dimensions. Source uses LANCZOS, binary mask nearest-neighbour; image padding is white and mask padding unselected. A region disappearing at target resolution refuses preparation.
- Immutable preparation includes original-grid source, adjusted source mask/blend alpha, fitted image/mask and exact fit crop. SHA-256 identifies canonical PNG bytes, not the untouched external file. Preparation schema v1 is independent of future request/attempt/DB versions.
- `composite_image` checks prepared content digests/grids, generated target dimensions and binary source-mask support. It unpads/resizes the raw result to source dimensions and blends against the prepared source. Outside alpha support the source RGB pixels are retained. This is not a promise of identical external compressed file bytes or a proof of backend success. The caller must retain raw output separately.
- CPU-intensive decode/filter/encode/composite must run on a worker. These helpers do not own cancellation, durable success or provider protocols. Large square-kernel adjustments may be slow on the owner's CPU; target-machine performance is unverified.

## Pending submission/persistence ownership

This section is the planned contract, not implemented behavior:

- Image-operation requests use schema v2; attempt metadata v2; execution database v3. Preserve historical txt2img records and uncertain reservations with consistent backup plus transactional migration. Unsupported newer schemas are refused by older writers.
- Before submission snapshot durable inputs under `<state>/inputs/<attempt-id>/`; register content identities/transform/settings without embedding raw bytes in request JSON. No reread of mutable user files during dispatch.
- Raw/final output evidence remains grouped under `outputs/<attempt-id>/`. Gallery/export/recycle see only explicitly registered final artifacts, not source inputs, masks or raw/compositing evidence. Derived cache purge never owns those directories. No persistent analysis cache is introduced.
- Keep generic CLI/config image dispatch and queues/plans/batches refused initially. Do not silently turn an image-operation request into txt2img by dropping its fields.
- DT+ requires an exact-file upstream description, including modifier/objective/companion fields, in `override.models`; no plain-family derived fallback for image operations. Missing description or exact operation evidence refuses before authentication/submission.
- Encode source/mask tensors and content-addressing before cloud authorization. Preserve resolved seed zero, existing authentication/uncertain-outcome/billing behavior and no automatic provider switch. Local runtime acceptance does not imply cloud acceptance.

## Verification boundary

No new tests, test execution, validation commands, builds, renders, inference or paid calls were performed under python-gui-fast-implement. The existing import-boundary allowlist is adapted for the new headless preparation service only; core and Qt remain Pillow-free. Owner/CI validation must cover limits, orientation/alpha, fitting/polarity, mask loss/empty adjustments, content corruption, compositing support, privacy and source preservation before acceptance. No new UI is available yet.

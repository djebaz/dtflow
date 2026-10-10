# Image-operation preparation

## Current slice and boundaries

Draft PR #64 starts Phase 9A/9B prerequisites before the smart editor. `core/image_inputs.py` defines immutable provider-neutral content/fit/preparation records; `services/image_preparation.py` owns bounded Pillow operations. These are callable headless building blocks only. Durable input snapshots and provider-local tensor encoding are now source-present, but not connected to generation dispatch. No GUI, CLI image-operation dispatch, request/DB-schema change, provider call, model inference or new dependency is introduced. Existing txt2img behavior is unchanged. No runtime/accuracy/CPU-speed acceptance is claimed.

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

## Durable snapshots and tensor preparation (source implementation)

- Worker-facing `services/image_inputs.snapshot_image_inputs(state, attempt_id, prepared, preserve_outside=True)` validates pixels and exclusively commits a prepared set. It does not create an attempt, authorize generation or submit work. The future generation orchestrator supplies a fresh lowercase UUID hex attempt ID while holding durable ownership. Img2img records preservation as false; inpainting defaults to true.
- `ImageInputSnapshot` schema v1 is independent of the unchanged execution database. It is JSON-safe: fixed content identities, byte counts, dimensions, channels, fit/grow/feather and preservation only. No encoded bytes or external source paths. Strict decoding requires every top-level field and rejects unknown fields.
- `persistence/image_inputs` writes only `<state>/inputs/<attempt-id>/source.png`, `image.png` and, for inpainting, `source_mask.png`, `mask.png`, `blend_mask.png`. `manifest.json` is the final commit marker. Existing sets, including incomplete sets, cannot be overwritten or reused. Failed partial directories remain evidence; no repair, deletion or dispatch replay occurs.
- Reads require the expected immutable snapshot, compare the committed manifest and verify each actual byte count/SHA-256. Per-file reads are bounded at 64 MiB, combined prepared PNGs at 128 MiB, and manifest at 16 KiB. Existing redirected ancestors/directories/files, Windows reparse points and non-regular files are refused. These are application-owned paths, not a security claim against a hostile process racing filesystem replacement.
- Content files and the marker are fsynced; POSIX additionally fsyncs the containing directory. Windows power-loss durability and concurrent filesystem behavior remain owner acceptance work. State/output DB migration is deliberately not performed by this standalone helper.
- `restore_image_inputs` verifies content mode/grid, canonical centered fit, fitted source pixels, exact nearest-neighbour mask/padding, nonempty binary support and inward feather pixels. Canonical PNGs with inherited metadata are rejected. Decode/filter/check work must stay on a worker with an orchestration resource reservation; no worker scheduling is introduced here.
- Provider-local `backends/drawthings/image_inputs.encode_image_inputs(prepared, limits)` produces fitted RGB float16 [-1,1] and optional single-plane uint8 0/2 mask tensors using the locked SDK. Header dtype/layout and exact size are checked; every mask payload byte is independently compared to prepared binary pixels. Preflight bounds tensor/content sizes before encoding. No provider type or image library enters core or Qt.
- Its immutable result has 32-byte SHA-256 tensor references plus the actual tensors in `contents`, with pixel payloads excluded from repr. PNG snapshot hashes and tensor hashes are distinct. This helper does not build/authorize/send a generation request or establish deployed compatibility. The existing txt2img builder is unchanged.
- Upstream reference: [service contract](https://raw.githubusercontent.com/drawthingsai/draw-things-comfyui/main/resources/imageService.proto), [server content-reference unwrapping](https://github.com/drawthingsai/draw-things-community/blob/main/Libraries/GRPC/Server/Sources/ImageGenerationServiceImpl.swift), and [mask 0/2 semantics](https://github.com/drawthingsai/draw-things-comfyui/blob/main/src/image_handlers.py). The installed drawthings-py 0.4.0 source supplies the matching encoder. Source inspection is not runtime acceptance.

## Pending submission/persistence ownership

This section is the planned contract, not implemented behavior:

- Image-operation requests use schema v2; attempt metadata v2; execution database v3. Preserve historical txt2img records and uncertain reservations with consistent backup plus transactional migration. Unsupported newer schemas are refused by older writers.
- Before submission snapshot durable inputs under `<state>/inputs/<attempt-id>/`; register content identities/transform/settings without embedding raw bytes in request JSON. No reread of mutable user files during dispatch.
- Raw/final output evidence remains grouped under `outputs/<attempt-id>/`. Gallery/export/recycle see only explicitly registered final artifacts, not source inputs, masks or raw/compositing evidence. Derived cache purge never owns those directories. No persistent analysis cache is introduced.
- Keep generic CLI/config image dispatch and queues/plans/batches refused initially. Do not silently turn an image-operation request into txt2img by dropping its fields.
- DT+ requires an exact-file upstream description, including modifier/objective/companion fields, in `override.models`; no plain-family derived fallback for image operations. Missing description or exact operation evidence refuses before authentication/submission.
- Encode source/mask tensors and content-addressing before cloud authorization. Preserve resolved seed zero, existing authentication/uncertain-outcome/billing behavior and no automatic provider switch. Local runtime acceptance does not imply cloud acceptance.

## UI handoff while dispatch is pending

The UI can depend on `PreparedImageInputs` and worker-facing `prepare_image_bytes` / `prepare_image_files` for source/mask previews and `composite_image` for offline result inspection. The immutable `fit` records source-to-target coordinates; use the original oriented source grid for edits. Helpers return no Qt/Pillow/provider objects. Load/edit/preview must not dispatch.

Keep persistence and the provider encoder behind the future generation orchestration boundary, not inside the editor/controller. Do not generate an attempt ID, write input sets or call a backend from a click-selection/preview action. Existing `ResolvedGenerationRequest`, queues, generic CLI/config and adapter capabilities are still txt2img-only; no image-operation Generate action can safely dispatch yet. Request v2 / execution DB v3, exact-description/evidence gating and saved raw/final registration are the next integration checkpoint, not implemented by these helpers.

## Verification boundary

No new tests, test execution, validation commands, builds, renders, inference or paid calls were performed under python-gui-fast-implement. The existing import-boundary allowlist is adapted for the new headless preparation service only; core and Qt remain Pillow-free. Owner/CI validation must cover limits, orientation/alpha, fitting/polarity, mask loss/empty adjustments, content corruption, compositing support, privacy and source preservation before acceptance. No new UI or image-operation dispatch is available from these helpers.

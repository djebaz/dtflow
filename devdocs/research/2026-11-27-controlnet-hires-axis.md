# ControlNet Depth and HiRes resize investigation (#637)

Date: 2026-11-27. DTFlow main inspected at [1c5ea66](https://github.com/djebaz/dtflow/commit/1c5ea661058424259776bf7ae2b76585482060c9); observed Windows revision cd5db92. No live generation, tests or renders executed by the agent.

## Finding

A width/height argument reversal is confirmed in public upstream source; its role in the reported DT+ failures is not confirmed. The current upstream main retrieved for this investigation is exactly the pinned [cb2d4c61](https://github.com/drawthingsai/draw-things-community/commit/cb2d4c61d1b58816ce1843481e74293a240e5bf4). No fix is present in that revision. The deployed DT+ revision is unknown.

- [First pass](https://github.com/drawthingsai/draw-things-community/blob/cb2d4c61d1b58816ce1843481e74293a240e5bf4/Libraries/LocalImageGenerator/Sources/LocalImageGenerator.swift#L4777-L4805): both depth and custom tensors use height ratio as widthScale and width ratio as heightScale. Second-pass resizing has the same discrepancy ([5152–5176](https://github.com/drawthingsai/draw-things-community/blob/cb2d4c61d1b58816ce1843481e74293a240e5bf4/Libraries/LocalImageGenerator/Sources/LocalImageGenerator.swift#L5152-L5176)). Additional depth/custom paths around 6140–6175 and depth paths around 7580 and 8620 require the same upstream audit.
- The upstream [Package.resolved](https://github.com/drawthingsai/draw-things-community/blob/cb2d4c61d1b58816ce1843481e74293a240e5bf4/Package.resolved) pins s4nnc 9c912b34 and ccv 72be6f75. [Swift Upsample](https://github.com/liuliu/s4nnc/blob/9c912b34f8242deeccd5d78fe28a140c0137b2ff/nnc/ModelAddons.swift#L1597-L1602) forwards width then height; [ccv model](https://github.com/liuliu/ccv/blob/72be6f75b62de7f504e38bf5fa6589952f7a68c4/lib/nnc/ccv_cnnp_model_addons.c#L4068-L4095) preserves them; [ccv output shape inference](https://github.com/liuliu/ccv/blob/72be6f75b62de7f504e38bf5fa6589952f7a68c4/lib/nnc/cmd/upsample/ccv_nnc_upsample.c#L25-L33) applies height to NHWC dimension 1 and width to dimension 2, rounding to nearest integer.
- The second pass starts from the original full-sized hint, not the first-pass resized hint; when the target matches the original, its resize guard bypasses Upsample. Pre-distorting or pre-resizing the sole submitted hint is not a general correction for both passes.

## DTFlow request review

Current wire.py uses native depth transport even with HiRes, with official Depth checkpoints using Unspecified input override (native modifier), and union Depth using Depth override. The retained prepared input is fitted at final size. image_inputs.py takes one grayscale channel from the RGB map and validates the 68-byte CCV header, dimensions, channel count and payload length; the SDK [v0.4.0 ImageBuffer.to_tensor](https://github.com/kcjerrell/drawthings-py/blob/v0.4.0/src/drawthings_py/image/image_buffer.py) normalizes uint8 to float16 [-1,1] in HWC order. The native upstream [Depth branch](https://github.com/drawthingsai/draw-things-community/blob/cb2d4c61d1b58816ce1843481e74293a240e5bf4/Libraries/LocalImageGenerator/Sources/LocalImageGenerator.swift#L2874-L2905) expects one channel and applies 0.5*(depth+1) when adjustRGB is true, then replicates it to RGB. The request references tensor bytes by SHA-256 and carries them in contents. The final width/height and effective first-pass fields are independently decoded and verified by wire.py; core/advanced.py snaps each axis to 64 pixels, halves up, unless explicitly supplied. No incorrect width/height exchange was found in these inspected DTFlow paths. This is static review, not captured actual wire bytes.

The three failing attempts in #637 are not available as retained local request records in the repository. The supplied issue summary reports 704×1024, Realistic Vision v5.1 Q6P/Q8P, 14 steps, DPM++ 2M Karras, no LoRAs; exact control checkpoint/type, seed, guidance range, map and effective first-pass fields for a specific failure remain unknown. Two no-final-output and one transport-inactive failure do not establish a provider crash or quota exhaustion. AC-1 remains incomplete until sanitized request metadata or owner reproduction is available.

## Geometry prediction (not runtime results)

For final W×H 704×1024 and factor 2, first pass is 384×512. Correct ratios are W 384/704 and H 512/1024; reversed produces 352×559 (ccv nearest rounding). Equal ratios conceal the issue. Custom RGB also has the reversed ratios, so this is not necessarily Depth-only.

| Final W×H | HiRes | Expected first pass | Predicted reversed output |
| --- | --- | --- | --- |
| 704×1024 | Off | — | No first-pass resize |
| 1024×1024 | 2× | 512×512 | 512×512 |
| 768×1024 | 2× | 384×512 | 384×512 |
| 704×1024 | 2× | 384×512 | 352×559 |
| 1024×704 | 2× | 512×384 | 559×352 |

Owner reproduction: use the same checkpoint, fixed seed/prompt, same source fitted consistently, control strength/range, sampler, steps and HiRes strength. Submit each case explicitly, record effective first-pass size, hint type and provider outcome; compare custom Canny/Pose/Segmentation or no control only if needed. Each submission may consume credits. No such reproduction was executed during this investigation.

## Resolution

Do not change DTFlow request construction or admission: no silent dimension adjustment, control substitution, retry or blocking. Offer the user the optional workaround of choosing proportional final/first-pass sizes (e.g. 768×1024 → 384×512 at factor 2), or turning HiRes off. Neither is guaranteed to resolve cloud failures. Existing exact saved settings and Retry stay intact.

## Draft upstream report (not submitted)

Title: HiRes depth and custom hint resizing swaps width and height scales for unequal axis ratios

At cb2d4c61, LocalImageGenerator.swift lines 4777–4805 and 5152–5176 compute widthScale from height and heightScale from width, whereas pinned s4nnc/ccv applies them to their named NHWC axes. A full-sized 704×1024 depth or custom map with first pass 384×512 should resize to 384×512 but shape inference predicts 352×559. Equal-axis ratios hide the discrepancy. Please check these and analogous resize sites; use widthScale = targetWidth/inputWidth and heightScale = targetHeight/inputHeight, retaining the existing mode and guards. A non-square unequal-ratio fixture would distinguish the correction. DT+ deployment and runtime causality remain unverified; observed client errors were missing final output and transport inactivity, not a confirmed server assertion. The source links and reproduction matrix above are the evidence. No private logs, credentials, prompts or image payloads are included.

Do not file externally without owner approval.

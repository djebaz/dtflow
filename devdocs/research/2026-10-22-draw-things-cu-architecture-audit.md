# Draw Things Compute Units: complete architecture audit (2026-10-22)

**Status:** source-only research for DTFlow [PR #153](https://github.com/djebaz/dtflow/pull/153). No claim of server-price parity or successful model execution. **No tests, scripts, inference, cloud calls, or renders were run.**

## Pinning and method

Primary upstream revision: [drawthingsai/draw-things-community `82bd56c81a313060dceb34092bcf9d671c39a739`](https://github.com/drawthingsai/draw-things-community/tree/82bd56c81a313060dceb34092bcf9d671c39a739).

- [`ComputeUnits.swift`, model resolution and CFG](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L5-L135)
- [`ComputeUnits.swift`, token lengths and latent scaling](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L136-L219)
- [`ComputeUnits.swift`, all 38 architecture cases](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L220-L588)
- [`ComputeUnits.swift`, calibration, multiplier and billing-proxy rounding](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L591-L642)
- [`Sampler.swift`, CFG selection and modifier-dependent channel count](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/SwiftDiffusion/Sources/Samplers/Sampler.swift)
- [Architecture-specific count functions](https://github.com/drawthingsai/draw-things-community/tree/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/SwiftDiffusion/Sources/Models/InstructionCount)

Repo sources inspected on branch `agent/compute-units-local-estimate` at head `ba541136a28540ed0fe8c9b364dd0a63ea8a2d65`: [Python estimator](../../src/dtflow/core/compute_units.py), [cloud serialization](../../src/dtflow/backends/drawthings/wire.py), [catalogue](../../src/dtflow/resources/catalogue/community_model_specs.json), [generation UI](../../src/dtflow/ui/generation_page.py), [plan](../plans/2026-10-22-compute-units-local-estimate.md).

## Formula and critical inputs

For most versions, the upstream calculator obtains architecture-specific instruction proxies `main` and `fixed` (the Swift count functions include dimensions, block shapes, text lengths, and sometimes reference/frame count). It computes:

```text
estimated_CU = ceil((main * steps * max(strength, 0.05) + fixed)
                    * 5.14816e-12 * model_usage_bias)
```

For **Würstchen Stage C**, instead use separate Stage C and Stage B main counts, `steps` and `stage2Steps`, and the fixed counts of both stages; apply strength to the combined main contribution. This is not one UNet multiplied by the number of steps. Sources: [standard and two-stage formulas](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L608-L643).

The **model-usage bias is 2.5** for v1, v2, Kandinsky 2.1, SDXL Base/Refiner, SSD 1B, Würstchen Stage C/B and SVD i2v; **1.0** for the other cases. This multiplier is an intentional policy preference, **not a hardware instruction measurement**. The scalar is a calibrated instruction proxy, not real GPU execution time. [Source lines 591–606](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L591-L606).

**Quantization:** Q4/Q6/Q8/FP16 does not appear as a direct multiplier in this upstream estimator. The exact model file still matters for *version, architecture, modifier, guidance embed, text token length, consistency designation*, and acceptance. Different model variants with the same quantization can cost different CU. [Model context](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L14-L69).

- **CFG:** not always `guidance != 1 ? 2 : 1`; the upstream code considers consistency, guidance embed, image guidance, first-frame CFG, version and sampler modifier. Editing can use **three** CFG channels; SVD and LongCat have exceptions. [CFG resolver](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L71-L88), [sampler implementation](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/SwiftDiffusion/Sources/Samplers/Sampler.swift).
- **Token count:** obtain from the exact-file model specification when present, otherwise follow the version-specific upstream defaults (512, 256, 128 or 77). No universally correct constant. [Source lines 136–161](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L136-L161).
- **Dimensions:** input `startWidth/startHeight` are scaled by 8 normally; by 4 for Wan 2.2 5B, MiniMax H3 and Qwen Image 2.1; by 2 for LTX2/2.3 and HiDream O1; Würstchen Stage C uses 1.5 and has a separate Stage B size. [Source lines 163–198](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L163-L198).
- **Video:** frame count is transformed to latent-frame count (SVD uses frames directly, Hunyuan/Wan mostly `(frames - 1) / 4 + 1`, LTX `(frames - 1) / 8 + 1`, MiniMax has a custom packing rule). MiniMax and LTX also account for audio-token lengths. [Source lines 90–115](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L90-L115).

## Exhaustive model-version map for the pinned source

**38 model versions across 35 `switch` cases.** The status column describes the **current Python port**, not whether Draw Things itself supports generating with the model.

| Group | Upstream model versions | Count implementation | PR #153 |
| --- | --- | --- | --- |
| Classic diffusion | `v1`, `v2` | UNet, UNetv2 | Missing; 2.5 bias |
| Other classic diffusion | `kandinsky21` | Kandinsky UNet | Missing; 2.5 bias |
| SDXL | `sdxlBase`, `sdxlRefiner`, `ssd1b` | Different UNetXL block arrangements + fixed counts | Missing; 2.5 bias |
| Two-stage / latent | `wurstchenStageC`, `wurstchenStageB` | Würstchen Stage C + Stage B; Stage C requires second-pass steps | Missing; 2.5 bias |
| MMDiT | `sd3`, `sd3Large` | MMDiT with different channels/layers; SD3 may use model-specific dual-attention layers | Missing |
| Other image DiT | `pixart`, `auraflow` | PixArt and AuraFlow count functions | Missing |
| FLUX 1 | `flux1` | FLUX.1 block counts | Port present |
| FLUX 2 | `flux2`, `flux2_9b`, `flux2_4b` | Separate channels/depths, and potential Kontext-KV reference caching | Ports present for text-only |
| FLUX 3 | `flux3_5b` | FLUX.3 count function, reference tokens | Missing |
| Qwen | `qwenImage`, `qwenImage2_1` | Qwen-Image and Qwen 2.1, latter includes timestep-dependent fixed path | Ports present for text-only |
| Z-Image | `zImage` | Z-Image instruction functions | Port present |
| ERNIE | `ernieImage` | ERNIE instruction functions | Missing |
| Ideogram | `ideogram4` | Ideogram 4 instruction functions | Port present |
| Krea | `krea2` | Krea 2 main + once-only text fusion + CFG fixed counts | Port present |
| SeedVR | `seedvr2_3b`, `seedvr2_7b` | ERNIE-based functions with distinct hidden sizes/depths | Missing |
| Cosmos | `cosmos2_5_2b` | Z-Image-derived function with channels 2048/layers 28, no fixed count | Port present |
| MOSS Image | `mossImage` | MOSS counts and CFG-dependent text refiner | Missing |
| HiDream | `hiDreamI1`, `hiDreamO1` | Different count functions, editing-width and token settings | Missing |
| SVD video | `svdI2v` | Temporal UNetXL; frame-count and special CFG handling | Missing; 2.5 bias |
| Hunyuan video | `hunyuanVideo` | Hunyuan DiT; latent frames and fixed text lengths | Missing |
| WAN video | `wan21_1_3b`, `wan21_14b`, `wan22_5b` | Three different WAN shapes; frame packing and causal-inference/image settings | Missing |
| LongCat avatar | `longcatVideoAvatar1_5` | WAN-derived layout, distilled no-CFG setting | Missing |
| LTX video | `ltx2`, `ltx2_3` | LTX2 with different KV/gated attention and text AdaLN, frame/audio counts | Missing |
| MiniMax video | `minimaxH3` | Video + audio lengths, nontrivial frame packing | Missing |

These are **all** the version cases in [upstream lines 220–588](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/ModelZoo/Sources/ComputeUnits.swift#L220-L588); a model can still have request-specific modifiers outside the corresponding base count.

## Current DTFlow coverage and product boundary

The checked-in `community_model_specs.json` contains **253 records**, of which **101** have one of the 10 Python `SUPPORTED` version values; **152** have a version outside that set. Among those 152, **100** are from currently unported image-architecture labels and **52** are from currently unported video-architecture labels (WAN 2.1, SVD, Hunyuan, MiniMax H3 and LTX2.3). These are *specification counts*, not provider-installed counts, not model capability checks, and not proof of numerical estimator parity.

In `wire.py`, DTFlow direct-cloud generation currently sends one output per request (`batch_size=batch_count=1`) and full strength for text-to-image (`TXT2IMG_STRENGTH=1.0`). Its dedicated image-edit path is separate, and the documented direct-cloud scope is text-to-image plus guarded image edits. This makes the **100 unsupported image specifications** the near-term useful extension; video estimate ports alone would not enable video generation.

The existing branch's estimated CU meter and details inspector are read-only. The Python port has no server-driven cost reconciliation, and its family fallback can emit a base estimate when an exact published spec is absent.

## Risks and recommendations (engineering inference, not upstream promises)

1. **Do not blindly add versions to `SUPPORTED`.** Port each upstream family-specific count function and any required inputs; implement the 2.5 model usage multiplier where applicable. A single complexity/scale coefficient per family is not the official formula.
2. **Reduce the 100 unsupported image specs first.** Sensible waves: (a) v1/v2 + SDXL/SSD (large inventory coverage; multiplier 2.5), (b) SD3/large, HiDream and ERNIE, (c) Würstchen's two stages, (d) families without current catalogue specimens (PixArt, AuraFlow, FLUX3, MOSS, etc.). Prioritization is based on the current catalogue, not on model popularity.
3. **Preserve exact variant context.** A broad `flux_2` family fallback is not proof that the checkpoint is a 4B, 9B or Dev architecture. A modifier involving inpainting, depth, Canny or editing can change operation count; showing an undisclosed base estimate where the true request is different could mislead. Indicate clearly that such numbers are base-architecture-only or decline until inputs are known.
4. **Audit existing ports separately.** Source inspection and automatic CI are not equivalent to numeric Swift/Python equivalence. Controlled numerical parity and Windows acceptance remain unverified, under the owner's current **no-new-tests/no-run-tests** instruction.
5. **Scope video separately.** Require accurate frame count and version-specific temporal/audio/reference context and the corresponding server request support before reporting any video CU in Generate.
6. **Never call the meter a quote.** Quantization does not directly enter the pinned instruction calculator, but actual deployed DT+ billing can differ by server revision/policy.

## Validation statement

This note is a source-only audit of the stated GitHub refs and their contents; no unit tests, lints, UI renders, execution of the estimator, generation, cloud inference, or potentially billable actions were performed. The exact counts (38 branches/versions, 101/152 records) are derived from the source trees inspected, **not** empirical runtime observations. The branch's source and unverified behavior are distinct.

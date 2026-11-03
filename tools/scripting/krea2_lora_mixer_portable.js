// VERSION$00017$ | Portable Draw Things / DTFlow inventory adapter
//@api-1.0
/*
Krea 2 Turbo prompt / random LoRA mixer for Draw Things iOS and DTFlow.
Separate prompts only with pipes (|). Empty entries are ignored.
Newlines and punctuation inside each prompt are preserved. Outer whitespace is trimmed.
Defaults: 2 distinct LoRAs, normalized random weights totaling 0.99,
1 image per prompt, fixed image seed 422, blend seed 42.
Startup follows the supplied wildcards / scene-picker scripts: explicit text
field heights, direct script-level execution, and live pipeline.configuration.
Draw Things keeps its original imported-LoRA behavior. New DTFlow builds list cached
inventory/catalogue/receipt candidates with explicit skipped and unknown metadata.
Draw Things keeps its supplied preset except for selected LoRAs, seed, output dimensions
sampler and text guidance. Default output remains 768 × 1152 with sampler 10.
Model/LoRA compatibility is user-supplied metadata, not runtime-verified here.
No activation words are added: all supplied prefixes are empty.

Import/paste this complete file into Draw Things Scripts or DTFlow Scripts.
Draw Things jobs remain sequential and clear their canvas before pipeline.run.
DTFlow uses runBatch when available and otherwise keeps its sequential fallback.
The preset uses strength 1 and no controls. Save your existing canvas first.
Images are saved incrementally. DTFlow batch outputs are independently exported
to the script sandbox; Draw Things uses its existing canvas export. No automatic retries.
Logs record job seeds, selected LoRAs/weights, prompts and output filenames.
In DTFlow, inherit the selected Generate model and steps instead of the fixed PRESET.
Set LOG_FULL_CONFIGURATION to true only when full configuration logs are needed.
DTFlow provides one Setup form, source-version
authoring preferences and explicit bounded chunks. Compare the full plan ID and
inspect existing attempts before manual continuation; no automatic resume or retry.
The Draw Things/older-DTFlow path below retains its original manual-resume behavior.
Comparison mode tests singles or unique pairs using the prompt selected by the
start number, a fixed image seed and equal shares of total LoRA strength. With
eight selected LoRAs: 8 singles or 28 pairs. Comparison always starts from the
first combination; its start number selects a prompt, not a combination to resume.
Random-mode image count, blend seed and seed increment do not affect comparison.
New DTFlow: Inspect preloads one Setup form; Run Script queues the chosen chunk.
No preview wizard or intermediate confirmation is required.
Draw Things Remember mixer settings prefills all fields on the next launch: exact prompts,
negative prompt, image count, LoRA count, strength, both seeds, increment toggle,
start job, preview toggle, LoRA selections, remember toggle, width, ratio, sampler, text guidance and generation mode. Prompt pipes,
punctuation, line breaks and outer whitespace are preserved. This does not
restore Draw Things' main prompt input. Small PNG crops store encoded text in
bounded filenames after the first saved image. All fields share one compressed
snapshot; there is no extra manifest image. Unchanged inputs reuse the snapshot.
This format ignores older krea2_form_v1 records. Encoded text is not encrypted.
No text-file writing is used. Disabling remember leaves prior saved records intact.
Marker records accumulate because the scripting API has no delete operation.
A persistence capacity limit of 512 chunks applies only to remembering text,
never to the inputs passed to generation; failures are reported without truncation.
Saved filenames include job, image seed, LoRA identities and exact weights.
The script does not download models or change the app's local/remote backend.
If your app routes generation to a paid service, running it may incur charges.
*/

const LOG_FULL_CONFIGURATION = false;

// DTFlow publishes this marker in its scripting engine overlay (PR #217).
// Standard Draw Things has no such marker, so its original behavior remains active.
// The optional batch entry point is exposed only by DTFlow builds supporting parallel scripts.
const IS_DTFLOW = typeof globalThis.__dtflowScriptHost === "object" &&
  globalThis.__dtflowScriptHost !== null &&
  globalThis.__dtflowScriptHost.name === "DTFlow" &&
  globalThis.__dtflowScriptHost.apiVersion === 1;
const HAS_DTFLOW_BATCH = IS_DTFLOW && typeof pipeline.runBatch === "function";

const PRESET = {
  aestheticScore: 6, batchCount: 1, batchSize: 1,
  causalInference: 0, causalInferencePad: 0, cfgZeroInitSteps: 0,
  cfgZeroStar: false, clipLText: "", clipSkip: 2, clipWeight: 1,
  colorCalibration: "none", compressionArtifacts: "disabled",
  compressionArtifactsQuality: 43.1, controls: [], cropLeft: 0, cropTop: 0,
  decodingTileHeight: 512, decodingTileOverlap: 128, decodingTileWidth: 512,
  diffusionTileHeight: 1024, diffusionTileOverlap: 128, diffusionTileWidth: 1024,
  expandPromptToJson: false, faceRestoration: "", fps: 5,
  guidanceEmbed: 3.5, guidanceScale: 1, guidingFrameNoise: 0.02,
  height: 1152, hiresFix: false, hiresFixHeight: 960,
  hiresFixStrength: 0.7, hiresFixWidth: 640, imageGuidanceScale: 1.5,
  imagePriorSteps: 5, loras: [], maskBlur: 2.5, maskBlurOutset: 0,
  model: "krea_2_turbo_q8p.ckpt", motionScale: 127,
  negativeAestheticScore: 2.5, negativeOriginalImageHeight: 576,
  negativeOriginalImageWidth: 512, negativePromptForImagePrior: true,
  numFrames: 14, openClipGText: "", originalImageHeight: 1152,
  originalImageWidth: 768, preserveOriginalAfterInpaint: true,
  refinerModel: "", refinerStart: 0.763, resolutionDependentShift: true,
  sampler: 10, seed: 422, seedMode: 2, separateClipL: false,
  separateOpenClipG: false, separateT5: false, sharpness: 0,
  shift: 2.8339362000000001, shiftForAudio: 3,
  solAttentionStart: 2, solAttentionTau: 0.5,
  speedUpWithGuidanceEmbed: true, stage2Guidance: 1,
  stage2Shift: 1, stage2Steps: 10, startFrameGuidance: 1,
  steps: 8, stochasticSamplingGamma: 0.3, strength: 1,
  t5Text: "", t5TextEncoder: true, targetImageHeight: 1152,
  targetImageWidth: 768, teaCache: false, teaCacheEnd: -1,
  teaCacheMaxSkipSteps: 3, teaCacheStart: 5, teaCacheThreshold: 0.3,
  tiledDecoding: true, tiledDiffusion: false, upscaler: "",
  upscalerScaleFactor: 0, usesSolAttention: false, width: 768,
  zeroNegativePrompt: false
};

const LORAS = [
  { name: "YARN_Hegre", file: "yarn_hegre_lora_f16.ckpt" },
  { name: "YARN_ShowyBeauty", file: "yarn_showybeauty_lora_f16.ckpt" },
  { name: "YARN_BlackedRaw", file: "yarn_blackedraw_lora_f16.ckpt" },
  { name: "YARN_GFRevenge", file: "yarn_gfrevenge_lora_f16.ckpt" },
  { name: "YARN_Zishy", file: "yarn_zishy_lora_f16.ckpt" },
  { name: "YARN_FTV", file: "yarn_ftv_lora_f16.ckpt" },
  { name: "YARN_MetArt", file: "yarn_metart_lora_f16.ckpt" },
  { name: "YARN_SexArt", file: "yarn_sexart_lora_f16.ckpt" }
];

// Stable ratio IDs: keep this order for saved-state compatibility.
const OUTPUT_RATIOS = [
  {label: "2:3", width: 2, height: 3},
  {label: "4:3", width: 4, height: 3},
  {label: "16:9", width: 16, height: 9},
  {label: "1:1", width: 1, height: 1},
  {label: "3:4", width: 3, height: 4},
  {label: "1:2", width: 1, height: 2},
  {label: "9:16", width: 9, height: 16}
];
// IDs are from Draw Things api-1.0 SamplerType, not menu positions.
// Keep the working preset first. Alternatives are not runtime-validated here.
const OUTPUT_SAMPLERS = [
  {id: 10, name: "Euler A Trailing (preset)"},
  {id: 15, name: "DPM++ 2M Trailing"},
  {id: 11, name: "DPM++ SDE Trailing"},
  {id: 16, name: "DDIM Trailing"},
  {id: 17, name: "UniPC Trailing"},
  {id: 5, name: "UniPC"},
  {id: 0, name: "DPM++ 2M Karras"},
  {id: 1, name: "Euler Ancestral"},
  {id: 4, name: "DPM++ SDE Karras"},
  {id: 3, name: "PLMS"},
  {id: 2, name: "DDIM"},
  {id: 6, name: "LCM"},
  {id: 7, name: "Euler A Substep"},
  {id: 8, name: "DPM++ SDE Substep"},
  {id: 9, name: "TCD"},
  {id: 19, name: "TCD Trailing"},
  {id: 12, name: "DPM++ 2M AYS"},
  {id: 13, name: "Euler A AYS"},
  {id: 14, name: "DPM++ SDE AYS"},
  {id: 18, name: "UniPC AYS"}
];
const RATIO_ICONS = ["data:image/png;base64,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", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAJAAAACkCAIAAAD3zzBUAAAFNElEQVR4nO3df0yUdQDH8e/xQxYWdxWS9IsZkmIkQliaUqOQScAoWSo1tNK5GHNApsGSkdqoLbXWREZYTti1RfNKo9Gi2YylkTcumYUixwERhCLedQcFx7H+YLH7g+TueZ678wOf11/Hcd/v893z3vPcF8YOldU2LAiHn68XQO5hMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDABMsfHrclRZB2ziqGhRvJYWVcYa0kj57xJD8Zackg+eyppfzXlfLxvjh+TduzZaW3W5snHEu6NcjcdrOUumWeMu0QwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOBYTAwcoM5/6N2coXMM6ay2oaljYxbkyPnwGRoqJEwSvoVJu14NEHy2ZN1S2QzaeScN+m3RPIJ7hLBMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGJsDXC5DCbrenr3/Z2NElhCgtKsjJznJllNU21Him6dTpMxfb2vv+vDL89z/zQu94ZNnDL254LiFuqYeXrBjIYJVHtRO13PJW2cETX3/r/ExvX39vX/9X9d/lbs3ZsX2bcgv0ILxgnd09FUdqFkTcN3Bt0Gobcn2gRh2yfl36E48/FvlAxD3h80dGRy+2GT+s+ORc8/mKIzVLFkWlpiR5btlKwQtWsu+9kZHR0uLCgqI97g18I9/5y+DgW1Y+Gh8fG5O5cUt7R6e29kuIYGCbDt3J+rM/Nz+T8tTqlcsVmTAoaE5yUqIQosPk9j3WJ5CCmc2Wdw6Uz50bvHvXdgWnDQzwF0LcFnKrgnN6DlKwsgPl182WgtwtYfNCb/Cy1kuXF8YmLoxN/OHHpmnndDgc3zeeFUKsWB6v2EI9CeY97KdzzbqT9YsfjNz0gkub+GlZbUPtRtPhquoLv126O/yuvG0YH5qFEWx01F6yb79Kpdr75uv+/v5ypurq7nk6I3vyy8DAwA1ZGfm5r4SF3il7md6AEexwVbWp6/fnn02LXxYz7YujF0W1n290cWa73d5yobVJ/0tGarK8NXoJQDCjqavyqFajDtlVmCt/toj7753Iabb81d3zR62urlZXV1i0p//K1a2bs6cd7nMAm46y/YfsdvvO/Fdv16gVnFajDln6UPTbJTuLX8sTQnxQ/vF1s0XB+T0E4MPBUtdtumw03fg1Go1af7pO2vxW21DcqrVCiGOV769akSBtEq8BuMI8zTE2NvFg7L8HNzOA97B6XfWUzyc8mW42W1z/bf3/+fTzE0IIPz+/6MVRcubxjhl4hU35g3N7R+fGl/K0n33xa2vb1YFBh8NhtQ3pDS3Fpe8ePFQlhMhMS4HY2QNcYYoYHx/XG1r0hpYpv5uctHrv7h1eXpI0syVYVOSC49qPGk41NukNvX39A9cGg4LmhM8Pi41ZkpmWcvPvNSYB7BLJ2Qx8D5vZGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4H5Fy+JULnsvsG1AAAAAElFTkSuQmCC", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAJAAAACkCAIAAAD3zzBUAAAHEklEQVR4nO3deVCUdRzH8S8sJIeWN56ICqJZKmJUhhNOyGGRgEqGNKY25JSWZmlRTodWYjNimVpqNaSNaEoeELh0mKlhEaYdprDchyJqCcSx7m5/bDEbEi7Ps8p+xs/rr4dneZ7n9zxvnt0f67g41NT+JYTDsaMHQO3DYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwME4qt/eb9IhNxnFDOZa1RfG2qu4w1lJGzXVTHoy11FB89RyU/Yc+y+Nl7kpWduwbU9jUWc3LCp4b1U46WKu9VF4xzhLBMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGDUBrP8jGiyhsorpvBj0IWfW6+asr8PofwOU/P3KEjx1VP1lMhmyqi5bsqfEqlDcJYIhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAOHX0AP7DYDT+dvJ0Tu6JH3KP5xw7ceHiHyKyb8eHI3x92tiquKRsS0rqt0e+rzhz1qVTp8FeA8OCg6Ijwrp2vcXK45pMps+1X+1ITfv15Om/6ut79+wROD4g/tFYz4H91Z+UbdnXZ03tSdcuTljeYmXbwbbv2vda4prGxqYW66MfDF+1PMGagxoMhoXPv5qh/brFeldXlw1JbwTefYc1O7lu7OsO02g0t93qe8fY0ePGjurerevDs+e3/f170rUvLX/LZDLdH3rfrNhpw4cNddQ46gqK0zK/MJmsPWjSug8ytF87ODg8PiduZkxkzx7d8nRFiUnrD2fnPLn4Je3urR69e6k9MduxrzvMkq6wODQyTv7/DjtXfSFkSmxNbd28uY88+1S8sqP8eanmnuCohsbGmQ9FvZrwTPN6vV4fEhlXWlYxY9qDK5Y9p2zn1wLwpGPr9tSa2rpBngMWPTlX8U6yf8htaGwUkfjZsZbrnZ2dZ8fFiEhaxpd6vV7lUG0IONiedK2IREeEaTQaxTsprzgjIq4uLv379mnxkM/QwSJSW1d34tffVQzTxlCDVZ+/UFZeKSIB/mN+/Onnx+Yv8Z8w+baA4NCouFVrNpinly2cPJXnPXqC9+gJBw8fbV5pMr/WObRyCNO/L4N5+YW2PwGl7GvSYb2ikjLzQu7xX1av3WgwGs1f6gqKdQXFu9O0ye+vNt8ibevj0UtE6usbKirP9uvrYfmQrqDIvFB1rtqGI1cJ9Q67VFNrXlj97qa+fXpvfGflz0ezcg9lJL6W0Nndvepc9ROLXrx8+fJV9xPg72d+Rt2UvM1yvV6v/+iTT83LtXV2NC9DvcNM/95SYjJtXJs4zHuIiLi6yNQp4e5urvOfXVZYXJqRdSAiPLh5kxG+PvnHv22xn149u0+dEr4jNW1rSmqXzu6x083T+sLEpPdKSss1Go3BYHB0tKMfazsaSru4ubmZF+4NvMtcq1nYpCDzOxRHsnOs2dWyJU+P8xtlMpnWb/o4MCR6uP/EiJg5h777fnrUA4MG9heRm7t0tvXwlUO9w3r36mFe8B7qdeWj3kO8SkrLK89WWbMrV1eXrZvfTtm5d3e6Nk9XaLhs8PEePDMmKvKBkNHjQ0XEa9AA2w1cLdRgXp4DbrrJuanpf35D+meC19rkrzVOTk5xM6LjZkRbrjx2/BfzO15jbh+pYqQ2hvqUqNFoAvz9RCRfV3Tlo/mFxSLSv5/HlQ9ZL2XXXhEZ5zeqxeyxY6EGE5GoiFAR+eZQ9un8Asv1mVkHSkrLReTewLsU7/zg4aOf7dsvIvPmxqkbpo0BB4sID751uI/BaIxfsPSrg0fqGxpqautS92a88MpKERk5YlhwUKDl97f6i7OIZGYdWJyw/HB2TlX1+aYmfUFRSdK6zfMWvmA0GqdH3h804e7relZXY1+vYQaDwXdsUIuVETFzmpdzD2U0z9kcHR3fW/Nm7Jz5ZRVn4hcstdzEy3PA+qTXrZyONzY17UnXmt/osjQtcvKKl5e09xSuNfsK1l79+nqk70zenLwt84tvysorxUG8PAeGTwqaFTvN3d3Nyp1MDpmo0Wh2p+0/lac7f+Fi925d/ceMemhqxPg7/a/p4JWx339eoVYBv4bdmBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOB+Rt2ZAcbikfAsQAAAABJRU5ErkJggg==", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAJAAAACkCAIAAAD3zzBUAAACxUlEQVR4nO3bv0ojQQCA8cRYqug9gxI8xGC4KwVBhBRaCQcWIlrb2dn6HoLYHVjdgQda+acQJUELw3mK+AIqKHYRC0HEyszkHL/N96uWwM4O8zHLbJH83f1DThwdqSeg5hgMxmAwBoMxGExn5P0jE7MtmUdbqW2tB98btcOsFSZm3cKDWStG8Orlwz6cXz/vz8Za2LPbU2V67uU64N0Ye+iwVrMiV8xTIozBYAwGYzAYg8EYDMZgMAaDMRiMwWAMBmMwGIPBGAzGYDAGgzEYjMFgDAZjMBiDwRgMxmAwBoMxGIzBYAwGYzAYg8EYDMZgMAaDMRiMwWAMBmMwGIPBGAzGYDAGgzEYjMFgDAZjMBiDwRgMxmAwBoMxGIzBYAwGYzAYg8EYDMZgMAaDMRiMwWAMBmMwGIPBGAzGYDAGgzEYjMFgDAZjMBiDwRgMxmAwBoMxGIzBYAwGYzAYg8EYDMZgMAaDMRiMwWAMBmMwGIPBGAzGYDAGgzEYjMFgDAZjMBiDwRgMxmAwBoMxGIzBYAwGYzCY2GCV6bmWzKN9RK5Y/u7+IezOkYnZmAertrUecFf4Dgt7np4Fr17UK9FmYWLWLfyVqCQ8JcIYDMZgMAaDMRiMwWAMBmMwGIPBGAzGYDAGgzEYjMFgDAZjMBiDwRgMpjP1BN6l8fh4Wj87qp4cVo+PaifXN7e5XO7Xz9XB4sAHD5IcI9jvze2l5ZXPMEhyjGCFQmHoa/F7ufStPPylr3dmfjHVIMkxgk1Wxicr48/XF5dXCQdJzkMHjMFgMhis/vdff2m0vzS6s3+Qei6tl8Fg2WYwGMYpsSmDxYHz493Us/hf3GEwBoMxGIzBYAwGk8Fg2f5wZhzrG41GsTz25sepHwsv19W9zZ7urg8YJLkM7rBs80/pMO4wGIPBGAzGYDAGgzEYjMFgDAZjMBiDwRgM5gmMkYRejHSSVwAAAABJRU5ErkJggg==", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAJAAAACkCAIAAAD3zzBUAAAFNklEQVR4nO3cf0zUdRzH8c/xQxYWdxWS9IsZkmIkQliaUqOQQcAoWSo1pNK5GHNApsGSkdqoLbXWREZYThht0bzSaLRoNmNp5I1LZqHIcUAEoYh33R0Fx7H+YLGbuBvgfb+fvez1+Ovuy32/3/d3z33vPsB2Gpt9WBAOH9kD0MwwGBgGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYBgMDIOB8ZN47pg12RLPfiOMjTWyTi3tDsOtJaQOLycYdK0Jsi5Bo/5/nN0v9ZujR1Q++41LzsyZfKz+e6PMRQdiLSF7bK4SwTAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGAYDw2BgGAwMg4FhMDAMBobBwDAYGJnBkjNzJJ591uSOrbHZh9U/a8yabPVP6nXGxhr1TyrnDpNyqd4l6xKkvSVCN5M4vJy3RJo1rhLBMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPDYGAYDAyDgWEwMAwGhsHAMBgYBgPjJ3uAabHZHU2nmk+cPHW+vaP/z0vDf/8zL/iOR5Y9/OL65+Jils7umE6nM23dy6bObiFEaVFBdlamV0dWCkawt8r2H/v6W/ctff0Dff0DXzV8l7s5e9vWLbM4ZuXh2olaWDCC6bRB69amPfH4Y+EPhN0TOn9kdPR8u+nDik/OtJytOFSzZFFESlLCjA7Y1dNbcahmQdh9g1eGbHaHQmMrASNYyRv57k8DA29Z+WhsbHRUxoZNHZ1dtXVfzjRYyZ73RkZGS4sLC4p2eXVSxQEvOgIC5iQmxAshOs0ze2fTH284/XPLM0lPrV65XJnRFAQcTAjh7+crhLgt6Nbp72KxWN/ZVz53buDOHVsVm0tBwMFcLtf3TaeFECuWx7pvb7twcWF0/MLo+B9+bJ66V9m+8qsWa0HuppB5wSoN6lUYn2HXsNkdHSbzwarqc79duDv0rrwt0/0ehp/OtOiPNyx+MHzjCxiL+KmQgnX39D6dnjX51N/ff31men7uKyHBd05n99FRZ8mevRqNZvebr/v6+io2prKQgl3D6XS2nmtrNvySnpLovj1yUUTH2aaprz9YVW3u/v35Z1Njl0WpNaP3IQULu//eiRIW6189vX/U6evr9PWFRbsGLl3enJPleV+TubvycK1OG7SjMFeVYZUCuejQaYOWPhT5dsn24tfyhBAflH981WL1vEvZ3gNOp3N7/qu367SqzKgU7O/psNkdMauShRBHKt9ftSLOwytT1m68aDJ7PppOpzWcrPfmfAqAvMMmucbGJh6M/ffgpof0GTbVp58fE0L4+PhELo7w/MoGffV1t8c9mWaxWIH+Wg9wh3V0dm14Ka/2sy9+bWu/PDjkcrlsdofB2Fpc+u7+A1VCiIzUJPeVvedfnNEB3GHj4+MGY6vB2HrdnyYmrN69c5vKI0kEECwifMHR2o8aTzQ1G4x9/QODV4YCAuaEzg+JjlqSkZrkea1x88FeJf4PAXyGkTsGA8NgYBgMDIOBYTAwDAaGwcAwGBgGA8NgYP4FskJQuWAEk4kAAAAASUVORK5CYII=", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAJAAAACkCAIAAAD3zzBUAAAEiElEQVR4nO3bf2iUdQDH8e9t5h9Zul+67bYh6rZzYR0O+91o/0gaROWs0HRjUUPJfthCRAiCICiEXEQRWiDbPxVFIdHIv1KXzubWhnq6zfXLH6nDbd3cdLKjPwIRvbnnybt9n/c9n9dfx3bc8+F58z1uHAtEh0eMcKTZHiDuKBiMgsEoGIyCwSgYjILBKBiMgsEoGIyCwSgYjILBKBjMNNsDbmbx0rVWrtuxu9HKdZ3w7gmzVcvupSfl0WDWb5n1ARMJePAb52tvVvPXO6f46suqaq4+9uB7o0dP2H+mvpatizrn6WByIwWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYNRMBgFg1EwGAWDUTAYBYPxdLBlVTU+uahzgejwiO0NcSxeutb2BNOxu9H2hDg8esKs3yzrAybi0WDG6i3zbC3j2bdEmYh3T5jEpWAwCgajYDAKBqNgMAoGo2AwCgajYDAKBqNgMAoGo2AwCgajYDAKBqNgMAoGo2AwCgajYDAKBqNgMAoGo2AwCgYzzfYAR8ZjsaOR7rb2rl/aO9s6ui4MDBpjdn35eVmoxPmLdPf27Wlp/bn10J9/nTp7rt8ETFFBsOKh+2pWrwzm5yZreqIx/hniu+9/rN/yznU/dBXs5Om/K5c/E/dXM2bc3vDe25UVD97SxKnCOGHp6emL7grdWx5eUn5PVmbGqtoNbl8hEDClxfMffeT+hx9YUlgQzJ2TE40Otxxoe3/bJ+f7L2yof6v526bCYF4yxicW44Rd68Rvfzz21Brj/i0xrmPdJ1Y8/9LY2JW62tWbXl+fiIHJ5fcPHQtLF5SH7zbGHD3WY3uLI34PZozJyc40xsRiMdtDHEnBYJHjPcXhiuJwxZ6W1kmfHIvF2jsPG2NCJQuSPy0BUjCYK198s+v0mbNpaWlVTz5ue4sjvg7W3dv37taPjDFrnnt6YSnjhDE+1rtSFirp7dw76dPOne+ve3Xz6Oil8KKyzW+8PAXDEsKnJ2xgcKi6buPJU2dCJfM/+3jr9Om32V7klB+DDf0Tra7b2Nv3+7y5RTs//SBj1kzbi1zwXbDhixdr19VHjvcUBvMatzfkZGfZXuSOv4KNjIzWrn+z60gkd87sph0f5uXOtr3INR8Fu3T58ouvbOroPJyTndW0o6GwIN/2ov8jBYPF/cN5bOzKute2HGz7NTNjVuP2bfPmFllceCsYH+vHx8dD5ZXX/fCJZ1+4+rh93w8z77zjJq/QdSSyb/9BY8zA4NDyFdU3PqEgP++n5q8SsDXJUvCEpTbe1ys+pxMGo2AwCgajYDAKBqNgMAoGo2AwCgajYDAKBvMvNEP5pJigzaoAAAAASUVORK5CYII=", "data:image/png;base64,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"];

function outputSize(widthInput, ratioIndex) {
  const width = Math.round(numberInput(widthInput, "Output width", 256, 2048, false) / 64) * 64;
  const index = numberInput(ratioIndex, "Ratio selection", 0, OUTPUT_RATIOS.length - 1, true);
  const ratio = OUTPUT_RATIOS[index];
  const height = Math.round(width * ratio.height / ratio.width / 64) * 64;
  if (height < 128 || height > 2048) {
    const maximumWidth = Math.floor(2048 * ratio.width / ratio.height / 64) * 64;
    throw new Error("Output would be " + width + " × " + height +
      ". For " + ratio.label + ", choose width no higher than " + maximumWidth + " px (2048 px height limit).");
  }
  return {width: width, height: height, ratio: ratio.label};
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function numberInput(value, label, min, max, integer) {
  if (String(value).trim() === "") throw new Error(label + " is required.");
  const number = Number(value);
  if (!Number.isFinite(number) || number < min || number > max ||
      (integer && !Number.isInteger(number))) {
    throw new Error(label + " must be " + (integer ? "an integer" : "a number") +
      " from " + min + " to " + max + ".");
  }
  return number;
}

// Per-job PRNG: skipping earlier jobs does not change later blends.
function randomForJob(seed, index) {
  let state = (seed + Math.imul(index + 1, 0x9e3779b9)) >>> 0;
  return function () {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), state | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function makeBlend(pool, count, totalWeight, random) {
  const shuffled = pool.slice();
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    const temporary = shuffled[i]; shuffled[i] = shuffled[j]; shuffled[j] = temporary;
  }
  const chosen = shuffled.slice(0, count);
  // Positive shares avoid effectively disabling a selected LoRA.
  const shares = chosen.map(() => 0.25 + random() * 0.75);
  const sum = shares.reduce((a, b) => a + b, 0);
  const totalUnits = Math.round(totalWeight * 10000);
  let usedUnits = 0;
  return chosen.map((lora, index) => {
    const units = index === chosen.length - 1 ? totalUnits - usedUnits :
      Math.floor(totalUnits * shares[index] / sum);
    usedUnits += units;
    return { file: lora.file, mode: "all", weight: units / 10000 };
  });
}

// Enumerate unordered combinations once, in the fixed LoRA pool order.
function comparisonBlends(pool, count, totalWeight) {
  const blends = [];
  function visit(start, chosen) {
    if (chosen.length === count) {
      blends.push(chosen.map(lora => ({file: lora.file, mode: "all", weight: totalWeight / count})));
      return;
    }
    for (let i = start; i <= pool.length - (count - chosen.length); i++) {
      visit(i + 1, chosen.concat(pool[i]));
    }
  }
  visit(0, []);
  return blends;
}

function switchInput(value, label) {
  if (value === true || value === 1 || value === "true") return true;
  if (value === false || value === 0 || value === "false") return false;
  throw new Error("Unexpected switch value for " + label + ": " + String(value));
}

function resetView(configuration) {
  canvas.updateCanvasSize(configuration);
  canvas.canvasZoom = 1;
  canvas.moveCanvas(0, 0);
}

// Compact form snapshots: flatten the fields, compress UTF-8 with LZSS,
// and encode as filename-safe Base64. Each part carries the count/checksum;
// no extra manifest image is needed. This is compression, not encryption.
const INPUT_STATE_PREFIX = "k2s_";
const INPUT_CHUNK_LENGTH = 200; // Full ASCII filename stays below 255 bytes.
const INPUT_MAX_CHUNKS = 512;
const INPUT_MAX_BYTES = 1048576;
const INPUT_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

function encodeInputText(text) {
  const bytes = [];
  const utf8 = encodeURIComponent(text);
  for (let i = 0; i < utf8.length; i++) {
    if (utf8[i] === "%") { bytes.push(parseInt(utf8.slice(i + 1, i + 3), 16)); i += 2; }
    else bytes.push(utf8.charCodeAt(i));
  }
  if (bytes.length > INPUT_MAX_BYTES) throw new Error("Remembered text exceeds 1 MiB.");
  const packed = [], chains = new Map();
  let position = 0;
  while (position < bytes.length) {
    const flagPosition = packed.length;
    packed.push(0);
    for (let bit = 0; bit < 8 && position < bytes.length; bit++) {
      let bestLength = 0, bestOffset = 0;
      const key = bytes.slice(position, position + 3).join(",");
      const candidates = chains.get(key) || [];
      for (let j = candidates.length - 1; j >= 0; j--) {
        const offset = position - candidates[j];
        if (offset > 4095) break;
        let length = 0;
        while (length < 18 && position + length < bytes.length &&
               bytes[position + length] === bytes[position + length - offset]) length++;
        if (length > bestLength) { bestLength = length; bestOffset = offset; }
        if (length === 18) break;
      }
      const consumed = bestLength >= 3 ? bestLength : 1;
      if (bestLength >= 3) {
        packed[flagPosition] |= 1 << bit;
        packed.push(bestOffset >> 4, ((bestOffset & 15) << 4) | (bestLength - 3));
      } else packed.push(bytes[position]);
      for (let i = 0; i < consumed; i++) {
        const key = bytes.slice(position, position + 3).join(",");
        const list = chains.get(key) || [];
        list.push(position++);
        if (list.length > 64) list.shift();
        chains.set(key, list);
      }
    }
  }
  const compressed = packed.length < bytes.length;
  const data = compressed ? packed : bytes;
  let output = compressed ? "z" : "r", buffer = 0, bits = 0;
  for (const byte of data) {
    buffer = (buffer << 8) | byte;
    bits += 8;
    while (bits >= 6) { bits -= 6; output += INPUT_ALPHABET[(buffer >>> bits) & 63]; }
  }
  if (bits) output += INPUT_ALPHABET[(buffer << (6 - bits)) & 63];
  return output;
}

function decodeInputText(encoded) {
  if (!/^[zr][A-Za-z0-9_-]*$/.test(encoded) || encoded.length > INPUT_CHUNK_LENGTH * INPUT_MAX_CHUNKS)
    throw new Error("Invalid settings encoding.");
  const data = [];
  let buffer = 0, bits = 0;
  for (const character of encoded.slice(1)) {
    buffer = (buffer << 6) | INPUT_ALPHABET.indexOf(character);
    bits += 6;
    if (bits >= 8) { bits -= 8; data.push((buffer >>> bits) & 255); }
  }
  let bytes = data;
  if (encoded[0] === "z") {
    bytes = [];
    let position = 0;
    while (position < data.length) {
      const flags = data[position++];
      if (position === data.length) throw new Error("Incomplete compression group.");
      for (let bit = 0; bit < 8 && position < data.length; bit++) {
        if (flags & (1 << bit)) {
          if (position + 1 >= data.length) throw new Error("Incomplete compression match.");
          const first = data[position++], second = data[position++];
          const offset = (first << 4) | (second >> 4), length = (second & 15) + 3;
          if (!offset || offset > bytes.length) throw new Error("Invalid compression offset.");
          for (let i = 0; i < length; i++) bytes.push(bytes[bytes.length - offset]);
        } else bytes.push(data[position++]);
        if (bytes.length > INPUT_MAX_BYTES) throw new Error("Settings exceed decode capacity.");
      }
    }
  }
  return decodeURIComponent(bytes.map(byte => "%" + byte.toString(16).padStart(2, "0")).join(""));
}

function inputChecksum(text) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619) >>> 0;
  return hash.toString(16).padStart(8, "0");
}

function inputStateEntries() {
  return (filesystem.readEntries(filesystem.pictures.path) || []).map(entry =>
    String(entry).replace(/\\/g, "/").split("/").pop());
}

function inputStateText(state) {
  return JSON.stringify(state.fields.map((section, index) => index === 10 ? section : section[0]));
}

function loadRememberedInputs() {
  try {
    const records = new Map();
    for (const name of inputStateEntries()) {
      const match = /^k2s_(\d{13}-[0-9a-f]{8})_(\d{3})_(\d{3})_([0-9a-f]{8})_([A-Za-z0-9_-]+)\.png$/.exec(name);
      if (!match) continue;
      const count = Number(match[2]), index = Number(match[3]);
      if (count < 1 || count > INPUT_MAX_CHUNKS || index >= count || match[5].length > INPUT_CHUNK_LENGTH) continue;
      const key = match[1] + "_" + match[2] + "_" + match[4];
      const record = records.get(key) || {count: count, checksum: match[4], parts: new Map(), duplicate: false};
      if (record.parts.has(index)) record.duplicate = true;
      record.parts.set(index, match[5]);
      records.set(key, record);
    }
    for (const key of Array.from(records.keys()).sort().reverse()) {
      const record = records.get(key);
      if (record.duplicate || record.parts.size !== record.count) continue;
      const encoded = Array.from({length: record.count}, (_, i) => record.parts.get(i)).join("");
      if (inputChecksum(encoded) !== record.checksum) continue;
      try {
        const values = JSON.parse(decodeInputText(encoded));
        if (!Array.isArray(values) || (values.length < 16 || values.length > 17) ||
            typeof values[0] !== "string" || typeof values[1] !== "string" ||
            !Array.isArray(values[10]) || values[10].length !== LORAS.length) continue;
        numberInput(values[15], "Text guidance (CFG)", 0, 10, false);
        numberInput(values[16] === undefined ? 0 : values[16], "Generation mode", 0, 1, true);
        outputSize(values[12], values[13]);
        numberInput(values[14], "Sampler selection", 0, OUTPUT_SAMPLERS.length - 1, true);
        console.log("Loaded remembered mixer settings.");
        return {fields: values.map((value, i) => i === 10 ? value : [value])};
      } catch (_) { /* Try the previous complete snapshot. */ }
    }
  } catch (error) { console.warn("Could not load remembered settings: " + String(error)); }
  return null;
}

function saveRememberedInputs(state, configuration) {
  try {
    const text = inputStateText(state);
    const existing = loadRememberedInputs();
    if (existing && inputStateText(existing) === text) return true;
    const encoded = encodeInputText(text);
    const count = Math.ceil(encoded.length / INPUT_CHUNK_LENGTH);
    if (count < 1 || count > INPUT_MAX_CHUNKS) throw new Error("Settings exceed filename storage capacity; prompts are not truncated.");
    const id = String(Date.now()).padStart(13, "0") + "-" +
      Math.floor(Math.random() * 0x100000000).toString(16).padStart(8, "0");
    const base = filesystem.pictures.path + "/" + INPUT_STATE_PREFIX + id + "_" +
      String(count).padStart(3, "0") + "_";
    const checksum = inputChecksum(encoded), written = [];
    try {
      resetView(Object.assign({}, configuration, {width: 64, height: 64}));
      for (let index = 0; index < count; index++) {
        const filename = base + String(index).padStart(3, "0") + "_" + checksum + "_" +
          encoded.slice(index * INPUT_CHUNK_LENGTH, (index + 1) * INPUT_CHUNK_LENGTH) + ".png";
        canvas.saveImage(filename, true);
        written.push(filename.split("/").pop());
      }
      const entries = new Set(inputStateEntries());
      if (!written.every(name => entries.has(name))) throw new Error("Settings snapshot incomplete.");
    } finally { resetView(configuration); }
    console.log("Remembered all mixer fields using " + count + " small PNG marker(s).");
    return true;
  } catch (error) {
    console.warn("Could not remember settings: " + String(error));
    return false;
  }
}

// The DTFlow adaptation is intentionally separate from Draw Things' marker-file
// persistence and fixed iOS preset. Setup remains parked until Run Script.
function runDtflowMixer() {
  const host = globalThis.__dtflowScriptHost;
  const base = clone(pipeline.configuration);
  const limits = host.runLimits();
  const cap = numberInput(limits.maxGenerations, "Captured run limit", 1, 32, true);
  // Optional exact substring filter for exceptionally large inventories. No
  // identifiers are silently dropped from the paged read-only host API.
  const LORA_NAME_FILTER = "";
  let rows = [], offset = 0, inventory;
  do {
    inventory = host.listLoRAs(offset);
    rows = rows.concat(inventory.items);
    offset = inventory.nextOffset;
  } while (offset !== null);
  const candidates = rows.filter(row => row.eligible &&
    (row.file + " " + row.name).toLowerCase().includes(LORA_NAME_FILTER.toLowerCase()));
  const skipped = rows.filter(row => !row.eligible).sort((a, b) =>
    (a.reason === "metadata_mismatch" ? 0 : 1) - (b.reason === "metadata_mismatch" ? 0 : 1) || a.file.localeCompare(b.file));
  if (candidates.length > 400) throw new Error("More than 400 candidate LoRAs: set LORA_NAME_FILTER in this adapter to narrow the form. listLoRAs still enumerates every known entry.");
  let saved = host.loadPreferences();
  if (saved && saved.schemaVersion !== 1) throw new Error("Unsupported saved mixer settings version; it was not overwritten.");
  saved = saved || {schemaVersion: 1};
  const prompts = pipeline.prompts || {};
  const get = (key, fallback) => saved[key] === undefined ? fallback : saved[key];
  const note = candidates.length + " available; " + skipped.length + " skipped; " + inventory.omitted + " unsafe identifiers omitted. Cached inventory.";
  const skipText = skipped.slice(0, 120).map(row => row.file + ": " + row.reason).join("\n") +
    (skipped.length > 120 ? "\nAdditional skipped entries are available through listLoRAs pagination." : "");
  if (!candidates.length) throw new Error("No ready LoRAs. Refresh inventory or Prepare files, then reload Setup.");

  const ratio = numberInput(get("ratio", 0), "Saved ratio", 0, OUTPUT_RATIOS.length - 1, true);
  const ratioOrder = [ratio].concat(OUTPUT_RATIOS.map((_, index) => index).filter(index => index !== ratio));
  const selectedIds = Array.isArray(saved.selected) ? saved.selected : candidates.slice(0, 8).map(row => row.file);
  const input = requestFromUser("Krea2 mixer", "Run Script", function () {
    return [
      this.section("Prompts", "Separate prompts with |.", [
        this.textField(get("prompts", String(prompts.prompt || "")), "Prompts", true, 160)
      ]),
      this.section("Negative prompt", "Used exactly as entered for every job.", [
        this.textField(get("negative", String(prompts.negativePrompt || "")), "Negative prompt", true, 72)
      ]),
      this.section("Mixing", "Comparison enumerates each single/pair for every prompt. The global job number supports explicit chunks.", [
        this.textField(String(get("images", 1)), "Images per prompt (random mode, 1–100)", false),
        this.textField(String(get("count", 2)), "LoRAs per image (1–8; comparison 1–2)", false),
        this.textField(String(get("strength", 0.99)), "Total strength (0.01–2)", false),
        this.textField(String(get("blendSeed", 42)), "Blend seed", false),
        this.textField(String(get("imageSeed", 422)), "Image seed", false),
        this.switch(get("increment", false), "Increment image seed (random mode)"),
        { ...this.menu(get("mode", 0), ["Random mixer", "Compare singles/pairs"]), title: "Generation mode" }
      ]),
      this.section("Output", "Uses Generate’s model and steps.", [
        this.slider(get("width", 768), this.slider.fractional(0), 256, 2048, "Width"),
        { ...this.image(ratioOrder.map(index => RATIO_ICONS[index]), 72, true), title: "Aspect ratio" },
        { ...this.menu(get("sampler", 0), OUTPUT_SAMPLERS.map(item => item.name)), title: "Sampler" },
        this.slider(get("guidance", 1), this.slider.fractional(1), 0, 10, "Guidance")
      ]),
      this.section("Chunk", "At most " + cap + " jobs in this run. Start is global, never advanced automatically. Inspect existing attempts before any continuation.", [
        this.textField(String(get("start", 1)), "First global job (1–10000)", false),
        this.textField(String(get("chunk", cap)), "Jobs in this chunk (1–" + cap + ")", false),
        this.slider(get("concurrency", 4), this.slider.fractional(0), 1, 10, "Requested concurrency")
      ]),
      this.section("LoRA candidates", note,
        candidates.map(row => this.switch(selectedIds.includes(row.file), row.name + " — " + row.file +
          " [" + (row.listed ? "listed" : "") + (row.prepared ? " prepared" : "") + "; " + row.compatibility + "]"))),
      this.section("Preferences", "Saved for this script version.", [
        this.switch(get("remember", true), "Remember settings")
      ]),
      this.section("Inventory notes", skipText, [
        this.plainText("Unknown compatibility is allowed; backend checks still apply.")
      ])
    ];
  });
  if (!input || input.length !== 8 || input[5].length !== candidates.length) throw new Error("Unexpected mixer answers; nothing was submitted.");
  const s = {
    schemaVersion: 1,
    prompts: String(input[0][0]), negative: String(input[1][0]),
    images: numberInput(input[2][0], "Images per prompt", 1, 100, true),
    count: numberInput(input[2][1], "LoRAs per image", 1, 8, true),
    strength: numberInput(input[2][2], "Total strength", 0.01, 2, false),
    blendSeed: numberInput(input[2][3], "Blend seed", 0, 4294967295, true),
    imageSeed: numberInput(input[2][4], "Image seed", 0, 4294967295, true),
    increment: switchInput(input[2][5], "Increment seed"),
    mode: numberInput(input[2][6], "Generation mode", 0, 1, true),
    width: Number(input[3][0]), ratio: ratioOrder[numberInput(input[3][1], "Ratio", 0, ratioOrder.length - 1, true)],
    sampler: numberInput(input[3][2], "Sampler", 0, OUTPUT_SAMPLERS.length - 1, true),
    guidance: numberInput(input[3][3], "Guidance", 0, 10, false),
    start: numberInput(input[4][0], "First job", 1, 10000, true),
    chunk: numberInput(input[4][1], "Chunk size", 1, cap, true),
    concurrency: numberInput(input[4][2], "Concurrency", 1, 10, true),
    selected: candidates.filter((row, index) => switchInput(input[5][index], row.file)).map(row => row.file),
    remember: switchInput(input[6][0], "Remember settings")
  };
  const parts = s.prompts.split("|").map(value => value.trim()).filter(Boolean);
  if (!parts.length) throw new Error("Enter at least one prompt; nothing was submitted.");
  const pool = candidates.filter(row => s.selected.includes(row.file));
  if (pool.length < s.count) throw new Error("Select at least LoRAs-per-image candidates; nothing was submitted.");
  const unknownCount = pool.filter(row => row.compatibility === "unknown").length;
  if (s.mode && s.count > 2) throw new Error("Comparison supports singles or pairs only.");
  const perPrompt = s.mode ? (s.count === 1 ? pool.length : pool.length * (pool.length - 1) / 2) : s.images;
  const total = parts.length * perPrompt;
  if (total > 10000 || s.start > total) throw new Error("Plan must contain 1–10000 jobs and include the chosen start number.");
  if (!s.mode && s.increment && s.imageSeed + total - 1 > 4294967295) throw new Error("Image seed would overflow.");
  const combinations = s.mode ? comparisonBlends(pool, s.count, s.strength) : null;
  const dimensions = outputSize(s.width, s.ratio);
  const common = clone(base);
  Object.assign(common, {width: dimensions.width, height: dimensions.height,
    sampler: OUTPUT_SAMPLERS[s.sampler].id, guidanceScale: s.guidance, seed: 0, loras: []});
  const identity = {schemaVersion: 1, common: common, prompts: parts, negative: s.negative,
    mode: s.mode, images: s.mode ? null : s.images, count: s.count, strength: s.strength,
    blendSeed: s.mode ? null : s.blendSeed, imageSeed: s.imageSeed,
    increment: s.mode ? false : s.increment, selected: s.selected};
  const planId = host.planId(identity);
  const end = Math.min(total, s.start + s.chunk - 1);
  const jobs = [];
  for (let index = s.start - 1; index < end; index++) {
    const config = clone(common);
    config.seed = s.imageSeed + (!s.mode && s.increment ? index : 0);
    config.loras = s.mode ? clone(combinations[index % perPrompt]) : makeBlend(pool, s.count, s.strength, randomForJob(s.blendSeed, index));
    jobs.push({configuration: config, prompt: parts[Math.floor(index / perPrompt)], negativePrompt: s.negative,
      planId: planId, jobIndex: index + 1,
      outputFile: "krea2_" + planId.slice(0, 12) + "_job" + String(index + 1).padStart(5, "0") + ".png"});
  }
  const changedPlan = saved.lastPlanId && saved.lastPlanId !== planId;
  const continuationNote = s.start > 1 ? (changedPlan
    ? "Plan changed; this is not continuation of the previous plan."
    : "Manual chunk: matching plan identity does not prove earlier jobs succeeded. Inspect existing attempts.") : "";
  s.lastPlanId = planId;
  saved = s;
  let preferenceNote = "Settings not saved (Remember off).";
  if (s.remember) {
    try { host.savePreferences(s); preferenceNote = "Authoring settings saved for this exact source version."; }
    catch (_) { preferenceNote = "Settings could not be saved; active inputs are not truncated. No automatic continuation."; }
  }
  console.log("Plan " + planId + "; jobs " + s.start + "–" + end + "/" + total + ". " + preferenceNote + " " + continuationNote);
  if (unknownCount) console.warn("Compatibility unknown for " + unknownCount + " selected LoRAs.");
  const results = pipeline.runBatch(jobs, s.concurrency);
  if (!Array.isArray(results) || results.length !== jobs.length) throw new Error("Unexpected batch reply. Inspect existing attempts before restarting.");
  results.forEach((result, index) => console.log("Global job " + jobs[index].jobIndex + ": " + result.status +
    "; attempt " + (result.attemptId || "none")));
  if (results.some(result => result.status !== "succeeded"))
    throw new Error("Chunk has failed, unknown, save-failed or unstarted jobs. Inspect recorded attempts; do not rerun the chunk blindly.");
  console.log(end < total ? "Chunk complete. Next global job is " + (end + 1) +
    "; continuing is manual with the same plan ID after inspecting history." :
    "Chunk reached the final plan job; this run does not verify any earlier chunks.");
  return;
}

if (HAS_DTFLOW_BATCH && typeof globalThis.__dtflowScriptHost.listLoRAs === "function" &&
    typeof globalThis.__dtflowScriptHost.planId === "function") {
  runDtflowMixer();
  return;
}

console.log("Krea 2 Turbo LoRA Mixer: opening input dialog.");
const currentPrompts = pipeline.prompts || {};
const rememberedInputs = IS_DTFLOW ? null : loadRememberedInputs();
function rememberedField(section, index, fallback) {
  return rememberedInputs && rememberedInputs.fields[section] ? rememberedInputs.fields[section][index] : fallback;
}
const initialRatioIndex = numberInput(rememberedField(13, 0, 0), "Saved ratio", 0, OUTPUT_RATIOS.length - 1, true);
// The native gallery has no initial-index argument. Put the saved choice first
// and translate the returned gallery index back into the stable ratio ID.
const ratioOrder = [initialRatioIndex].concat(OUTPUT_RATIOS.map((_, index) => index).filter(index => index !== initialRatioIndex));
const popup = requestFromUser("Krea 2 Turbo LoRA Mixer", "Run", function () {
  return [
    this.section("Prompts", "Separate prompts only with |. Newlines, semicolons and periods stay inside a prompt. No script length limit.", [
      this.textField(rememberedField(0, 0, String(currentPrompts.prompt || "")), "First prompt | second prompt | third prompt", true, 220)
    ]),
    this.section("Negative prompt", "Applied to every image. Leave empty if unused.", [
      this.textField(rememberedField(1, 0, String(currentPrompts.negativePrompt || "")), "Negative prompt", true, 80)
    ]),
    this.section("Images per prompt", IS_DTFLOW ? "DTFlow: maximum 30 image-generation calls per script. Comparison tests each combination once." : "Random mode: 1–100 images for each prompt. Comparison mode: ignored; one image per unique combination.", [
      this.textField(rememberedField(2, 0, "1"), "1–100", false, 44)
    ]),
    this.section("LoRAs per image", "Random: 1–8. Comparison: only 1 (each LoRA) or 2 (every unique pair at 50/50).", [
      this.textField(rememberedField(3, 0, "2"), "1–8", false, 44)
    ]),
    this.section("Total LoRA strength", "0.01–2. Shared across the selected LoRAs, not applied to each one.", [
      this.textField(rememberedField(4, 0, "0.99"), "0.01–2", false, 44)
    ]),
    this.section("Blend seed", "Random mode: reproducible LoRA selection and weights. Ignored in comparison mode. Range: 0–4294967295.", [
      this.textField(rememberedField(5, 0, "42"), "Blend seed", false, 44)
    ]),
    this.section("Image seed", "Controls generation noise. Range: 0–4294967295.", [
      this.textField(rememberedField(6, 0, "422"), "Image seed", false, 44)
    ]),
    this.section("Image seed behavior", "Random mode only. Comparison mode always keeps the image seed fixed.", [
      this.switch(rememberedField(7, 0, false), "Increment image seed per job")
    ]),
    this.section("Start number — job / prompt", "Random: first job to run (manual resume). Comparison: prompt number to use; all singles or pairs are tested from the beginning. Prompts are numbered after empty pipe entries are removed.", [
      this.textField(rememberedField(8, 0, "1"), "Job number / comparison prompt number", false, 44)
    ]),
    this.section("Preview only", IS_DTFLOW ? "DTFlow runs are started explicitly from Scripts > Setup. This script has no preview mode there." : "Keep off to generate. Preview logs the plan without submitting jobs.", [
      IS_DTFLOW ? this.plainText("DTFlow: generation starts only after Run Script.") :
        this.switch(rememberedField(9, 0, false), "Preview only — log plan without generating")
    ]),
    this.section("LoRA pool", "Select the LoRAs available for random mixing or systematic comparison.",
      LORAS.map((lora, index) => this.switch(rememberedField(10, index, true), lora.name))),
    this.section("Remember mixer settings", IS_DTFLOW ? "DTFlow manages form-answer suggestions per saved script version; no settings-marker PNGs are written." : "Prefill all fields on the next launch. Saved after the first successful image. Turning this off leaves the previous saved settings unchanged. Preview does not save changes.", [
      IS_DTFLOW ? this.plainText("Use the Scripts Setup form to review remembered fields.") :
        this.switch(rememberedField(11, 0, true), "Remember all mixer fields")
    ]),
    this.section("Output width", "256–2048 px. Rounded to the nearest 64 px. Height follows the selected ratio; both dimensions are limited to 2048 px.", [
      this.slider(Number(rememberedField(12, 0, 768)), this.slider.fractional(0), 256, 2048, "Width (px)")
    ]),
    this.section("Aspect ratio", "Tap a shape. The first icon is your saved ratio (default 2:3). Height is rounded to the nearest 64 px, so the output ratio can differ slightly.", [
      this.image(ratioOrder.map(index => RATIO_ICONS[index]), 96, true)
    ]),
    this.section("Sampler", "Euler A Trailing keeps the working preset. Alternative samplers are available for comparison; their Krea 2 Turbo behavior is not verified here.", [
      this.menu(Number(rememberedField(14, 0, 0)), OUTPUT_SAMPLERS.map(sampler => sampler.name))
    ]),
    this.section("Text guidance (CFG)", "Default 1.0. Adjustable from 0 to 10.", [
      this.slider(Number(rememberedField(15, 0, 1)), this.slider.fractional(1), 0, 10, "Text guidance (CFG)")
    ]),
    this.section("Generation mode", "Comparison uses the prompt selected by Start number, a fixed image seed, and every single LoRA or unique pair at equal shares of total strength. Images per prompt, blend seed and seed increment are ignored. Preview still applies.", [
      this.menu(Number(rememberedField(16, 0, 0)), ["Random mixer", "Compare all LoRA combinations"])
    ]),
    this.section("Generation", IS_DTFLOW
      ? "Uses the model and steps selected in DTFlow Generate. Each result is saved independently. Batch concurrency is limited by backend availability; cloud work may use credits."
      : "Selected width, ratio, sampler and text guidance; 8 steps. Each real job clears the canvas and moodboard, then saves its PNG to Draw Things Pictures. Save your current canvas first. No automatic model downloads.", []),
    ...(HAS_DTFLOW_BATCH ? [
      this.section("Max concurrent jobs", "DTFlow only. Choose 1–10 simultaneous requests; effective concurrency cannot exceed the backend and shared queue limits.", [
        this.slider(4, this.slider.fractional(0), 1, 10, "Max concurrent jobs")
      ])
    ] : [])
  ];
});
if (!popup || popup.length === 0) { throw new Error("Input dialog cancelled; no generation started."); }
console.log("Input dialog confirmed; preparing batch.");
// Every labeled input section returns exactly one value. Section headings
// remain visible even when the field contains a value.
if (popup.length < 17 || popup.slice(0, 10).some((section, index) =>
    !Array.isArray(section) || section.length !== (IS_DTFLOW && index === 9 ? 0 : 1)) ||
    !Array.isArray(popup[10]) || popup[10].length !== LORAS.length ||
    popup.slice(11, 17).some((section, index) =>
      !Array.isArray(section) || section.length !== (IS_DTFLOW && index === 0 ? 0 : 1))) {
  throw new Error("Unexpected dialog response; no generation started.");
}
if (HAS_DTFLOW_BATCH && (popup.length !== 19 || !Array.isArray(popup[18]) || popup[18].length !== 1)) {
  throw new Error("Unexpected concurrency response; no generation started.");
}
const maxConcurrent = HAS_DTFLOW_BATCH ?
  numberInput(popup[18][0], "Max concurrent jobs", 1, 10, true) : 1;
const rawPromptText = String(popup[0][0]);
const rememberSettings = IS_DTFLOW ? false : switchInput(popup[11][0], "Remember mixer settings");
const ratioGalleryIndex = numberInput(popup[13][0], "Ratio selection", 0, ratioOrder.length - 1, true);
const selectedRatioIndex = ratioOrder[ratioGalleryIndex];
const dimensions = outputSize(popup[12][0], selectedRatioIndex);
const samplerIndex = numberInput(popup[14][0], "Sampler selection", 0, OUTPUT_SAMPLERS.length - 1, true);
const selectedSampler = OUTPUT_SAMPLERS[samplerIndex];
const textGuidance = numberInput(popup[15][0], "Text guidance (CFG)", 0, 10, false);
const comparison = numberInput(popup[16][0], "Generation mode", 0, 1, true) === 1;
const stateToRemember = {fields: clone(popup.slice(0, 17))};
stateToRemember.fields[12][0] = dimensions.width;
stateToRemember.fields[13][0] = selectedRatioIndex;
const prompts = rawPromptText.split("|").map(p => p.trim()).filter(Boolean);
if (!prompts.length) throw new Error("Enter at least one prompt.");
const negativePrompt = String(popup[1][0] || "");
const options = popup.slice(2, 10).map(section => section[0]);
const images = numberInput(options[0], "Images per prompt", 1, 100, true);
const count = numberInput(options[1], "LoRAs per image", 1, 8, true);
const strength = numberInput(options[2], "Total strength", 0.01, 2, false);
const blendSeed = numberInput(options[3], "Blend seed", 0, 4294967295, true);
const imageSeed = numberInput(options[4], "Image seed", 0, 4294967295, true);
const incrementSeed = switchInput(options[5], "Increment image seed");
const preview = IS_DTFLOW ? false : switchInput(options[7], "Preview only");
const pool = LORAS.filter((lora, index) => switchInput(popup[10][index], lora.name));
if (pool.length < count) throw new Error("Select at least " + count + " LoRAs, or lower LoRAs per image.");
if (comparison && count > 2) throw new Error("Comparison supports only 1 or 2 LoRAs per image.");
const blends = comparison ? comparisonBlends(pool, count, strength) : null;
const total = comparison ? blends.length : prompts.length * images;
if (total > 10000) throw new Error("Batch exceeds 10000 images. Split your prompt list.");
// DTFlow's default run ceiling is 30; reject before submitting any job.
if (IS_DTFLOW && total > 30) {
  throw new Error("DTFlow run exceeds 30 generations. Reduce the image count or selected comparisons.");
}
const startNumber = numberInput(options[6], comparison ? "Comparison prompt number" : "Start job", 1, comparison ? prompts.length : total, true);
const comparisonPromptIndex = comparison ? startNumber - 1 : 0;
const start = comparison ? 1 : startNumber;
if (!comparison && incrementSeed && imageSeed + total - 1 > 4294967295) {
  throw new Error("Incremented image seed would exceed 4294967295.");
}
// Follow the supplied wildcards / scene-picker execution pattern.
// Availability checks are omitted: imported custom files and remote setups
// must not be blocked by a local built-in model availability query.
const configuration = pipeline.configuration;
const originalConfiguration = clone(configuration);
console.log("Preparing model: " + (IS_DTFLOW ? originalConfiguration.model : PRESET.model));
console.log("Output: " + dimensions.width + " × " + dimensions.height + "; target ratio=" + dimensions.ratio + "; sampler=" + selectedSampler.name + "; text guidance=" + textGuidance);
const runId = Date.now().toString() + "_" + Math.floor(Math.random() * 0x100000000).toString(16);
console.log(preview ? "PREVIEW ONLY: no generation will be submitted." : "GENERATE MODE: image generation enabled.");
console.log("Batch " + runId + ": " + total + " jobs; starting at " + start);
console.log(comparison ? "COMPARISON: prompt " + (comparisonPromptIndex + 1) + "; fixed image seed; equal shares; " + total + " unique combinations. Images per prompt, blend seed and seed increment are ignored." : "RANDOM MIXER: prompt order with seeded random blends.");
console.log("Blend seed=" + blendSeed + "; image seed=" + imageSeed + "; total LoRA strength=" + strength);
console.log("LoRA pool: " + pool.map(lora => lora.name).join(", "));
let completed = 0;
let rememberAttempted = false;
const batchJobs = [];
for (let index = start - 1; index < total; index++) {
  const promptIndex = comparison ? comparisonPromptIndex : Math.floor(index / images);
  // Preview uses a detached snapshot; real runs update the actual app object.
  const jobConfiguration = IS_DTFLOW
    ? clone(originalConfiguration)  // Keep unsupported fields at their Generate baseline values.
    : Object.assign(clone(originalConfiguration), clone(PRESET));
  jobConfiguration.width = dimensions.width;
  jobConfiguration.height = dimensions.height;
  if (!IS_DTFLOW) {
    jobConfiguration.originalImageWidth = dimensions.width;
    jobConfiguration.originalImageHeight = dimensions.height;
    jobConfiguration.targetImageWidth = dimensions.width;
    jobConfiguration.targetImageHeight = dimensions.height;
  }
  jobConfiguration.sampler = selectedSampler.id;
  jobConfiguration.guidanceScale = textGuidance;
  jobConfiguration.seed = imageSeed + (!comparison && incrementSeed ? index : 0);
  jobConfiguration.loras = comparison ? clone(blends[index]) : makeBlend(pool, count, strength, randomForJob(blendSeed, index));
  const blendTag = jobConfiguration.loras.map(lora => {
    const source = LORAS.find(entry => entry.file === lora.file);
    return source.name.replace(/^YARN_/, "") + "-" + lora.weight.toFixed(4);
  }).join("_");
  const filename = (comparison ? "krea2_cmp_p" + String(comparisonPromptIndex + 1).padStart(4, "0") + "_" : "krea2_") + runId + "_job" + String(index + 1).padStart(5, "0") +
    "_seed" + jobConfiguration.seed + "_" + blendTag + ".png";
  console.log("Job " + (index + 1) + "/" + total + "; prompt " + (promptIndex + 1) + "; seed=" + jobConfiguration.seed);
  console.log("Blend: " + blendTag);
  console.log("Prompt: " + prompts[promptIndex]);
  if (negativePrompt) console.log("Negative prompt: " + negativePrompt);
  console.log("Output filename: " + filename);
  if (LOG_FULL_CONFIGURATION) console.log(JSON.stringify(jobConfiguration));
  if (preview) {
    console.log("PREVIEW: skipping pipeline.run for job " + (index + 1));
    continue;
  }
  if (HAS_DTFLOW_BATCH) {
    // Snapshot all fields now; the host links each child attempt before submission.
    // Never clear or read the shared canvas for a concurrent job.
    batchJobs.push({
      configuration: clone(jobConfiguration),
      prompt: prompts[promptIndex],
      negativePrompt: negativePrompt,
      outputFile: filename
    });
    continue;
  }
  try {
    Object.assign(configuration, jobConfiguration);
    console.log("Configuration applied for job " + (index + 1));
    console.log("Clearing canvas and moodboard for job " + (index + 1));
    canvas.clear();
    canvas.clearMoodboard();
    resetView(configuration);
    // Pass an empty negative prompt explicitly rather than inheriting app text.
    const runRequest = {
      configuration: configuration,
      prompt: prompts[promptIndex],
      negativePrompt: negativePrompt
    };
    console.log("CALL pipeline.run — job " + (index + 1) + "/" + total);
    pipeline.run(runRequest);
    console.log("Generation returned for job " + (index + 1) + "; saving PNG.");
    console.log("Preparing canvas for PNG export.");
    resetView(configuration);
    const outputPath = filesystem.pictures.path + "/" + filename;
    canvas.saveImage(outputPath, true);
    completed++;
    console.log("Saved job " + (index + 1) + ": " + outputPath);
    if (rememberSettings && !rememberAttempted) {
      rememberAttempted = true;
      saveRememberedInputs(stateToRemember, configuration);
    }
  } catch (error) {
    console.error("Stopped at job " + (index + 1) + ": " + String(error));
    console.error(comparison ? "Comparison stopped; Start number selects the prompt and does not resume combinations. Saved this invocation: " + completed : "Inspect the last canvas before retrying. For manual resume, keep all inputs unchanged and select the first unsaved job. Saved this invocation: " + completed);
    throw error;
  }
}
if (HAS_DTFLOW_BATCH && batchJobs.length) {
  console.log("Submitting " + batchJobs.length + " independent jobs; requested concurrency=" + maxConcurrent);
  const results = pipeline.runBatch(batchJobs, maxConcurrent);
  if (!Array.isArray(results) || results.length !== batchJobs.length) {
    throw new Error("DTFlow returned an unexpected batch result; inspect the ScriptRun attempts before retrying.");
  }
  let failures = 0;
  results.forEach((result, index) => {
    if (result.status === "succeeded") {
      completed++;
      console.log("Batch job " + (index + 1) + " saved: " +
        (result.savedPath || result.outputPaths.join(", ")) + "; attempt " + result.attemptId);
    } else {
      failures++;
      console.error("Batch job " + (index + 1) + " " + result.status +
        (result.error ? ": " + result.error : "") + "; attempt " + (result.attemptId || "none"));
    }
  });
  if (failures) {
    const counts = {};
    results.forEach(result => {
      if (result.status !== "succeeded") {
        counts[result.status] = (counts[result.status] || 0) + 1;
      }
    });
    const breakdown = Object.keys(counts).sort().map(status =>
      counts[status] + " " + status.replace(/_/g, " ")).join(", ");
    // Prefer the first actual failure over an unstarted job when explaining the cause.
    let issueIndex = results.findIndex(result =>
      result.status !== "succeeded" && result.status !== "not_started");
    if (issueIndex < 0) issueIndex = results.findIndex(result => result.status !== "succeeded");
    const issue = results[issueIndex];
    const code = typeof issue.error === "string" ?
      issue.error.replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 80) : "";
    throw new Error("Parallel batch: " + completed + "/" + batchJobs.length +
      " saved; " + breakdown + ". First issue: job " + (issueIndex + 1) +
      " (" + issue.status + (code ? ", code " + code : "") +
      "). Inspect Console and existing attempt records before restarting.");
  }
}
console.log(preview ? "Preview complete; no images generated." :
  "Batch complete. Saved this invocation: " + completed + ". Last job: " + total);

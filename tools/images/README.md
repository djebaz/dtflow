# Pixel-statistics detector PoC

`detect_noise.py` is a standalone, offline diagnostic, with the same pixel-statistics service as DTFlow export sorting; it does not own export or job handling. It uses the existing Pillow dependency, no model or network service.

From the repository root in PowerShell:

```powershell
uv run python tools/images/detect_noise.py --help
uv run python tools/images/detect_noise.py -s 'C:\Users\dje\Documents\GitHub\dtflow\tmp\smoke3' -o 'tmp/noise-report.json'
```

Pass multiple files/folders after `-s/--source` to compare selected images. Folder scans are flat unless `-r/--recursive` is specified. `-o/--out` writes a new JSON file only; without it, JSON goes to stdout. The output parent directory must exist. Reports contain local paths, but no prompts or copied generation metadata. Keep reports private if paths are sensitive.

## Metrics and interpretation

Analysis uses the first frame, EXIF orientation, alpha composited onto white, and an aspect-preserving LANCZOS thumbnail of at most 256 pixels per side. The report includes original/analysis sizes and exact provisional thresholds.

- `saturated_fraction`: fraction of pixels with HSV saturation at least 200/255.
- `extreme_channel_fraction`: fraction of RGB channel samples at most 5 or at least 250 (not fraction of pixels).
- `neighbor_rgb_difference`: mean absolute RGB difference between horizontal/vertical neighbours, divided by 255.
- `luma_mean`, `luma_stddev`: Pillow L-channel statistics divided by 255; `luma_entropy_bits` is histogram entropy and does not determine the noise flag.
- `coarse_residual_rms`: RGB RMS difference from a BOX-downsampled, bilinear-reconstructed 32-pixel coarse image, divided by 255.
- `coarse_residual_to_rgb_stddev`: residual RMS divided by RGB standard deviation (floor 1/255); this ratio can exceed one. This is a spatial proxy, not an FFT spectrum.

Four noise signals are saturation >=0.30, extreme channels >=0.20, neighbour difference >=0.12, and a joint residual test (RMS >=0.10 and residual/stddev >=0.75). `possible_noise` requires at least three signals plus neighbour difference >=0.08. `possible_blank` takes priority when luminance standard deviation <0.01. `noise_signal_count` counts rules, **not confidence, probability or an aesthetic score**.

**Version 2** also flags noise when all three spatial conditions hold: neighbour difference >=0.08, residual RMS >=0.18 and residual/stddev >=0.65. This independent route reports `joint_spatial_noise_rule` when the original route did not flag the image. `noise_signal_count` still counts only the original four signals; it is not a threshold for this alternate route. Blank detection retains priority. Reports use algorithm identity `dtflow-pixel-statistics-poc-v2`.

Labels are `possible_noise`, `possible_blank`, `no_obvious_failure`, and `unreadable`. Review flagged images manually. A clean flag does not establish a great or correct generation: anatomy errors, unwanted composition and prompt mismatch can pass. Foliage, textures, sharp patterns, artwork and intentional solid colours can trigger false positives. Resize, image dimensions, compression and white alpha compositing affect metrics.

**Thresholds are provisional and sample-fitted, not independently validated.** Owner-labelled runs found zero flags in 262 good images and 11/12 noise detections in the failed folder. Smoke3 review identified two noise misses; cached-statistics evaluation of the added spatial rule catches exactly those two additional images while still flagging zero of the 262 good examples. These reused samples do not establish general accuracy. Independent examples and owner review remain necessary before production use.

## Safety and limits

The CLI makes no source writes, deletion, regeneration, job-status updates or provider calls. The Connection export checkbox optionally uses the shared service to route newly exported copies only into noise/blank folders; it does not modify originals. Inputs are limited to 10000 unique files, 64 MiB encoded per file and 32 million pixels; limits/read failures produce `unreadable`, not evidence of a failed generation. Existing report files are never overwritten. An interrupted run may leave an incomplete report; choose a new name for the next run. Exit code 0 means processing completed (including advisory flags), 1 means at least one unreadable input, and 2 means CLI/input/report-path error.

No automated tests were added or run under fast-implement. Subsequent image detector runs were explicitly requested by the owner; independent validation of version 2 remains pending. Static syntax/import/ruff/help checks do not establish detection accuracy.

## Reference JPEG/EXIF exporter

`export_jpeg_exif_poc.py` preserves the original reference experiment, independent of application export. It requires explicit paths and has no owner-specific defaults or imports from ignored tmp scripts:

```powershell
uv run python tools/images/export_jpeg_exif_poc.py -s '<saved-attempt-folder>' -o 'tmp/reference-jpeg-export'
```

It supports RGB PNGs only and includes built-in EXIF/pixel/original-preservation verification. It writes JPG, JSON and verification reports and uses numeric collision suffixes. Unlike production export, it intentionally retains **full private source metadata**, including prompts, paths and asset references; do not share its exports blindly. Its filename date is source mtime, not verified generation time. For portable privacy, deduplication and historical creation/Date taken fields, use the application export instead. Copying the script does not execute its verification.

## CPU person/zone mask PoC

`person_zone_mask_poc.py` is standalone and non-generative: one local image,
one CPU segmentation call and a manual rectangle. No DTFlow imports, production
UI changes, automatic boxes, Draw Things calls or generation.

### Smoke with the owner's existing FastSeg model

From repository root, change the image path and box to fit your image:

```powershell
uv run --locked --group mask-poc python tools/images/person_zone_mask_poc.py `
  -p fastseg `
  -s 'C:\Images\person.jpg' `
  -m 'tmp/320models/simplified.onnx' `
  -c 3377fbbb7aaf8858c14449638d0968c5fafba139661a170d8ae1cbf7dbff7048 `
  -b 100 150 500 700 -t 1 -o 'tmp/person-zone/run-001'
```

For the owner's INT8 export, change `-m` to
`tmp/320models/mobilev3small_lraspp_f64_int8.onnx` and `-c` to
`1e9018116a6a790c62c34956d308e9a63b5575ded41ba4217022d27f9de36159`.
Start with FP32 to separate segmentation/export problems from quantization.
These checksums identify the files inspected on 2026-10-10, not upstream artifacts.
Model files stay ignored; a fresh checkout does not include them. Missing/mismatched
files fail, never download. Always use a new output directory.

### Explicit profiles and preprocessing

`-p/--profile fastseg` selects **FastSeg MobileV3Small LR-ASPP F64**, based on
`mobilev3small-lraspp-f64-114fc23b.pt`, with 19 Cityscapes output logits and
**person train ID 11**. Rider (12) is deliberately excluded. The owner supplied
the export recipe: eval mode, fixed 320 square input, pooling kernel/stride
patched to 40x40, simplified ONNX and optional dynamic QInt8 weights. These
exports lack custom contract metadata; choosing this profile and a trusted
checksum is an explicit caller assertion, not architecture/provenance proof.

`-p torchvision` (default, retained original profile) requires an externally
prepared TorchVision LR-ASPP MobileNetV3-Large COCO_WITH_VOC_LABELS_V1 export:
21 classes, person 15, and custom metadata
`mask_poc_contract=torchvision-lraspp-mobilenet-v3-large-voc21-320-v1`.
The two models are not interchangeable. Model shape/profile mismatches fail.

Both profiles require one self-contained float32 input `[1,3,320,320]` and one
float32 logits output `[1,C,320,320]`. External tensor files and the original
1024x2048 exports are unsupported. No graph/model files are modified.

Preprocessing: one frame only, EXIF transpose, alpha composited on white, RGB,
Pillow bilinear **stretch** to 320 square, divide by 255, normalize with mean
`[0.485,0.456,0.406]` and std `[0.229,0.224,0.225]`, float32 NCHW. FastSeg's upstream
predict normalizes without resizing; TorchVision's upstream weights resize the
short edge to 520. This square adaptation and the owner's pooling change are
experimental; do not infer published model accuracy or CPU speed. Cityscapes
street-scene training does not establish performance on other image domains.

Sources: [FastSeg model/checkpoint](https://github.com/ekzhang/fastseg),
[FastSeg normalization/argmax](https://github.com/ekzhang/fastseg/blob/master/fastseg/model/base.py),
[Cityscapes person train ID](https://github.com/mcordts/cityscapesScripts/blob/master/cityscapesscripts/helpers/labels.py),
[TorchVision weights](https://github.com/pytorch/vision/blob/main/torchvision/models/segmentation/lraspp.py),
[ORT threading](https://onnxruntime.ai/docs/performance/tune-performance/threading.html).

### Geometry, outputs and safety

`-b/--box` is `x0 y0 x1 y1` in **EXIF-oriented source pixels**, origin top left,
left/top inclusive and right/bottom exclusive. Empty/out-of-bounds boxes fail;
there is no clipping. Argmax selects the profile's person class in model space,
then nearest-neighbour resizes the binary mask to oriented source dimensions.
`edit = person AND rectangle`; no feather/dilation/spill. All people form a
semantic union, not separate instances.

Outputs below a new, non-redirected repository `tmp/` directory:
`person_mask.png`, `zone_mask.png`, `edit_mask.png` (255 selected, 0 unselected),
`overlay.jpg` (red edit tint) and `report.json`. The report identifies profile,
model/checksum, actual provider, platform/packages, class mapping, preprocessing,
box coordinates, thread settings, selected pixels and separate load, decode,
preprocessing, cold inference, postprocessing and image-write timings.

The dedicated non-default `mask-poc` uv group contains CPU ONNX Runtime, NumPy
and Pillow. One caller/worker/session/image/inference call, default 1 intra-op
thread capped at `min(4, logical CPU count)`, inter-op 1, sequential execution,
spinning disabled, **CPUExecutionProvider only**. No GPU probing/fallback,
startup installs/downloads or pip tricks; dependencies synchronize through uv.
NumPy retains the repository's legacy-CPU pin; ORT on older CPUs remains unverified.

Limits: 64 MiB per source/model and 16 million source pixels. Sources/model bytes
are read only. Output files are created exclusively; failures return 2 and may
leave partial outputs, never delete or overwrite. Success returns 0 even for an
empty mask. No source metadata is copied. Reports/overlays may be sensitive.
One cold inference is not a throughput benchmark or an inpainting compatibility
claim. Automatic boxes using a separate VideoSceneDetect-style decoder are future
work, not implemented here.

### Validation boundary

Before the owner invoked fast-implement, fake-session checks were written/run;
these are not real segmentation evidence. No further tests or smoke runs are
performed under fast-implement. Final real-model and target-CPU smoke belongs
to the owner. No pretrained image inference, training, generation or paid provider
operations have been run by the agent.

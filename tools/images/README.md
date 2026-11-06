# Pixel-statistics detector PoC

For the later SCHP/edge/depth/SAM/custom SEG and OBB experiments in PR #236, see [CPU segmentation and OBB experiments](segmentation-pocs.md), including current commands, output modes, draft-label resume, dependency gaps and owner-smoke evidence. These are standalone tools, not desktop model support.

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

## CPU YOLO26 person mask and manual rectangle

`yolo_person_zone_mask_poc.py` is the same experiment as the FastSeg one, with an Ultralytics
**YOLO26n-seg** end2end export (instance masks instead of a semantic class). It needs the
`segment` + `end2end` metadata: raw YOLO exports and other tasks fail. Input is one float32
`[1,3,640,640]` tensor, outputs are `[1,300,38]` detections and `[1,32,160,160]` prototypes. Images are
letterboxed to 640 square (Pillow bilinear, grey pad 114, divide by 255). The person class (0) above
`-f/--confidence` (default 0.25) is kept, at most the 20 best instances, and their masks are
united. The CLI is `-s -m -c -b -f -t -o` as above (`-b x0 y0 x1 y1` is the manual rectangle) and writes the
same four images and `report.json`. Both Ultralytics exports used here are AGPL-3.0: check licensing
before shipping anything built on them in an app.

## Person parts mask PoC (YOLO26 + NudeNet + pose + clothes)

`person_parts_mask_poc.py` cuts the YOLO26 person mask into labelled parts. It is standalone, CPU only,
sequential (one ONNX session alive at a time, freed before the next one loads), and non-generative.
It imports the decoder, letterbox and checks of `yolo_person_zone_mask_poc.py` and the image/output
helpers of `person_zone_mask_poc.py`. Nothing is downloaded: every model is passed with its
SHA-256 and a mismatch fails.

### Stages

1. **Person mask**: YOLO26n-seg as above, with a `--person-box-margin` (default 8 model px) around each
   box and `--fill-holes` (default 1500 px) to close small holes.
2. **Parts** (required): NudeNet **320n** (Ultralytics YOLOv8 detect, raw `[1,22,N]`, 18 classes). Armpit
   classes are excluded, then greedy class-agnostic NMS (`--parts-confidence`, default 0.25, and
   `--parts-iou`, default 0.45). Each detection is grown (`--grow`), limited by a shape
   (`--mask-box-limit-shape rectangle|rounded|ellipse`), reduced to the component under its centre,
   smoothed (`--mask-smooth-*`) and intersected with the person. `--overlap center|nearest|smaller` decides
   box overlaps.
3. **Pose** (optional, `--pose-model` + `--pose-sha256`): **yolo26n-pose** end2end `[1,300,57]`, 17 COCO
   keypoints, NMS applied (near-duplicate people otherwise appear). It adds limb zones: upper arm,
   forearm, hand, thigh, shin, foot. A zone is the slab between the two planes perpendicular to the
   keypoint vector, within `--limb-width` (arms, default 0.35) or `--leg-width` (legs, default 0.20) of the
   limb length, intersected with the person and with the connected piece through the limb axis. A hand or
   foot is an extension past the wrist or ankle (`--hand-extent`, `--foot-extent`; 0 = until the mask
   ends). Limb zones are painted after the box parts; overlaps between zones go to the nearest limb
   axis. `--foot-zones auto` skips foot zones when NudeNet found a `FEET_*` box, `--no-leg-zones`
   keeps arms and hands only.
4. **Clothes** (optional, `--clothes-model` + `--clothes-sha256`, optional `--clothes-config`): a
   SegFormer ONNX with dynamic NCHW input, run on the person bounding-box crop (long side
   `--clothes-size`, default 512, multiple of 32). Tested with `mattmdjaga/segformer_b2_clothes`
   (110 MB, 18 labels: Background, Hat, Hair, Sunglasses, Upper-clothes, Skirt, Pants, Dress, Belt,
   Left-shoe, Right-shoe, Face, Left-leg, Right-leg, Left-arm, Right-arm, Bag, Scarf). The B0 variant was
   clearly weaker on the owner's samples. There is **no** label for stockings, tights, socks or underwear:
   such pixels land in a leg, pants or skirt label. `Face` is capped to the NudeNet face box plus
   `--face-below` (default 0.15 of its height); Face pixels beyond it become a synthetic `Skin` label.
   `--limb-skin-only` removes garment pixels from every pose zone (needs the clothes model).
5. **Expansion** (`--expand none|rows|nearest|geodesic`, `--expand-max`, `--limb-expand-max`):
   geodesic floods each part through the person mask, so it follows limbs and curves. Pixels not connected
   to any part stay unassigned (`unassigned_person_pixels` in the report).

### Running

```powershell
uv run --locked --group mask-poc python tools/images/person_parts_mask_poc.py `
  -s 'C:\Images\person.jpg' `
  -m 'tmp/models/yolo26n-seg.onnx' -c <sha256> `
  --parts-model 'tmp/models/320n.onnx' --parts-sha256 <sha256> `
  --pose-model 'tmp/models/yolo26n-pose.onnx' --pose-sha256 <sha256> `
  --clothes-model 'tmp/models/segformer_b2_clothes.onnx' --clothes-sha256 <sha256> `
  --limb-width 0.17 --hand-extent 0.7 --limb-expand-max 15 `
  --expand geodesic --expand-max 150 --overlap nearest --limb-skin-only `
  -o 'tmp/person-zone/parts-001'
```

`-o` must be a new directory below repository `tmp/`; `--overwrite` reuses an existing one and replaces only
the files this script owns. **Folder mode**: `-d/--source-dir <folder>` replaces `-s`/`-o`; it takes
the images directly inside the folder (not recursive, symlinks ignored) and writes each to
`<folder>/analyze/<filename>/` (the extension is kept only when two files share a name). Existing result
folders are skipped unless `--overwrite`; a failing image is logged and the run continues (exit code 1 at
the end). Models are reloaded for each image. Folder-mode outputs are not under `tmp/`.

### Outputs

Per image: `person_mask.png`, `parts_label_map.png` and `parts_core_label_map.png` (0 outside the person,
1 person without a part, detection index + 2 for a part; core = before expansion), their `*_view.png`
colour versions with part names, `parts/NN_LABEL.png`, `labels/LABEL.png` (union per label),
`overlay.jpg` and `report.json`. With the clothes model: `clothes_label_map.png` (0 outside, class id + 1
inside) and its view, `clothes/*.png`, `clothes_overlay.jpg`, `clothed_mask.png`, `skin_mask.png` and
`parts_fused/NN_LABEL_covered|skin.png`; each part gets `clothed_pixels`, `skin_pixels` and
`other_pixels` in the report. Clothing labels are Upper-clothes, Skirt, Pants, Dress, Belt, Scarf;
skin labels are Face, Left/Right-arm, Left/Right-leg and `Skin`; Hair, Background, shoes and the rest
count as neither.

`combined_overlay.png` puts everything in one image (`--combined-layers`, default
`fill,parts,clothes,names`; also `boxes`, `pose`; `none` skips it). Fill colour is the status: red =
exposed (non-garment pixels of a detector `*_EXPOSED` part), amber = other bare skin, blue = clothing,
grey = other. Contour hue is the part, a black line is a clothing edge, white is the person outline,
and a legend strip sits below the picture. Without the clothes model, `*_COVERED` parts stand in for
clothing.

The report records models and checksums, providers and versions, preprocessing, every decoding option,
per-part pixel counts, pose keypoints and diagnostics (`foot_zones_skipped`,
`limb_pixels_removed_as_clothing`), clothes label counts and timings per stage. On the owner's old
Core 2-class CPU a full run with SegFormer B2 took roughly 20 to 30 s, of which the clothes inference was
5 to 13 s and writing the PNGs 4 to 7 s; these are single cold runs, not a benchmark.

### Known limits

- Parts are detector boxes intersected with the person mask: rectangular cuts, not part segmentation,
  and detections can be missed. For example `FEMALE_GENITALIA_COVERED` was absent on a standing photo with a
  short skirt, so the belly and thighs absorbed that area. The report does not yet say whether such a
  candidate scored under the threshold or lost in NMS.
- Pose keypoints can be wrong under skirts or long hems: a knee placed at the hem makes the thigh zone
  tiny and the shin zone contain the whole leg. There is no knee sanity check yet.
- With `--limb-skin-only`, pixels removed from a limb zone are not protected: box parts can flood into them
  and a zone made only of clothing can end empty.
- SegFormer logits are 1/4 of the input resolution; the ATR label set may mislabel skin, underwear or
  unusual poses; Left/Right follow the model's convention. Its licence was not checked.
- Person masks are the union of up to 20 instances; parts are not attributed to instances.
- Both Ultralytics models are AGPL-3.0. Reports and overlays contain local paths and may be sensitive.

### Validation boundary

Fake-session and synthetic-array smoke tests (decoders, split and expansion, limb zones, pose NMS,
clothes fusion, label views, folder mode, combined overlay) were run in a sandbox without the YOLO
or pose weights; the NudeNet 320n and SegFormer B0 sessions were real. All other model results come from the owner's
runs on real photos, reviewed by eye. No accuracy, speed or licence claim is made. Thresholds and defaults are
sample-fitted on a handful of images.


## SCHP ATR-18 folder smoke test (CPU ONNX)

`schp_atr18_smoke.py` is a standalone experiment using existing NumPy, Pillow
and ONNX Runtime dependencies. It detects YOLO26 person instances, runs padded
SCHP on each crop and detects all NudeNet classes at confidence >= 0.10.
Optional pose correction remains separate. Missing checkpoints download from
pinned sources; explicit local paths and `--no-download` remain supported.

```powershell
uv run python tools/images/schp_atr18_smoke.py -i "C:\Images\Test" --body-only --pose-model
```

### Independent body regions

Every NudeNet detection produces its own mask inside its box and the decoded
person silhouette. Another region never excludes it, even for same-class boxes.
Within-class detector NMS still applies.

Previously a hard SCHP score cutoff removed all non-red pixels, and a single-label
overlay hid overlapping regions. Boundaries now combine **65% normalized local RGB
contrast and 35% absolute SCHP score change**, assigned to both endpoints of each
horizontal/vertical neighboring pair. A one-source-pixel RGB blur reduces texture.
Display colors, opacity and SCHP class IDs do not affect selection.

- `-g/--boundary`: edge-barrier strength, default **0.12**. Higher values retain
  fewer barriers and usually larger regions; lower values can fragment regions.
- `-b/--body-confidence`: seed score for equal-area ties, default **0.90**.
  Always select the largest connected component. If several share the largest
  area, prefer one containing a high-score seed. A smaller confident island
  cannot displace a larger region. This option is **not a pixel exclusion floor**;
  low-score pixels can remain inside regions. Reports state whether the selected
  component contains a high-score seed.
- `-r/--rounding`: source-pixel radius; default **3** in body-only mode, zero
  otherwise. Smoothing stays inside the selected component and silhouette.

The person mask is neither smoothed nor hole-filled. Gaps predicted by YOLO are
preserved; gaps missed by the model cannot be reconstructed by this change.
This is a heuristic, not anatomical segmentation or 3D reconstruction: shadows
and texture can create false edges; weak/open edges can merge regions. Default
weights and thresholds need owner review on real photos. SCHP scores remain
uncalibrated and do not measure correctness of NudeNet anatomical labels.

### Outputs

Output folders are grouped by type; existing runs are preserved with numbered
sibling directories. Each image/person has a shared numbered filename stem.

| Output in `overlays/` | Content |
| --- | --- |
| `*_body_overlay.png` | Independent regions, averaged overlap colors, every contour and embedded legend |
| `*_clothes_overlay.png` | Separate SCHP clothing/accessory classes |
| `*_person_overlay.png` | Raw silhouette over the photo beside its binary mask, including internal gaps |
| `*_score_overlay.png` | Blended SCHP scores, silhouette outline and NudeNet boxes |
| `*_score_map.png` | Unblended scores inside the silhouette, with legend |
| `*_boundary_overlay.png` | Actual combined image/score barriers in cyan |

`--body-only` saves these six images and JSON/CSV reports; it skips binary masks,
crops, labels and comparisons. It still renders all independent body regions.

Full mode additionally saves `masks/*_body_detection_NNN_LABEL.png`: one source-aligned
0/255 mask per detection, including empty results. Class-union masks remain for
convenience. The single-label body map and comparison use the highest NudeNet score
for compatibility and **cannot encode overlaps**; independent masks are authoritative.
The body overlay is rendered from those independent masks. Its legend sums detection
areas, counting overlap more than once. Reports retain mask paths, seed fallback,
edge settings, selected pixels and overlap counts.

Raw/refined SCHP outputs, confidence maps, crops and optional pose diagnostics remain
in full mode. `--min-confidence` affects refined SCHP labels only.
`--opacity` changes visualization only; `--threads` controls SCHP/pose CPU threads.

### Validation boundary

Earlier versions produced working overlays in the owner's Windows runs. The original SCHP follow-up recorded synthetic offline checks for image/score boundaries, overlapping detections, silhouette holes, rounding, image writes and CLI settings, without model inference in those checks. Later PR #236 work includes owner model runs, owner Colab training/exports and explicitly requested small-model local inference; see the [current experiment evidence and limits](segmentation-pocs.md#evidence-and-unresolved-limits--2026-11-06). None establishes independent quantitative accuracy, broad Windows performance, model license/provenance clearance or production support.

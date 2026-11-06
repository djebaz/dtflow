# CPU segmentation and OBB experiments — PR #236

These are standalone, local evaluation tools, not new DTFlow desktop model support. They process private images and may write local paths into reports. Keep checkpoints, datasets, previews, exports and training results under ignored `tmp/` rather than Git. No release version is changed by this experiment.

## Scope and environment

Inference uses ONNX Runtime with `CPUExecutionProvider`. Custom-model tools use NumPy, Pillow and OpenCV for pixels, geometry and rendering; they do not load `.pt` checkpoints. OpenCV is not currently declared in the project's dependency metadata: success in the owner's provisioned environment does not establish that these OpenCV-based tools run in a fresh locked environment. This PR does not add a runtime dependency or amend `uv.lock`; dependency reproducibility remains a follow-up before broader support. Use the repository's uv environment and agent setup instructions; do not silently install or upgrade packages during a run.

The separate `export_obb_onnx.py` converter has pinned PEP 723 dependencies in an isolated uv script environment. It uses PyTorch to load a trusted `.pt` file and trace an export; `-y / --trust` explicitly acknowledges that checkpoint unpickling can execute code. A successful export/ONNX structural check does not prove inference parity, precision or performance. The owner also performed exports and training in Colab; those notebook cells and private datasets are not repository tooling.

Many earlier POCs automatically download missing pinned public checkpoints and offer `--no-download`. Verification establishes artifact identity, not license clearance. Custom SEG/OBB checkpoints must already exist locally. The supplied NSFW model README did not state a license; neither its metrics nor dataset provenance were independently verified here.

## Tool map

| Tool | Experiment |
| --- | --- |
| `schp_atr18_smoke.py` | SCHP ATR-18 score maps, independent NudeNet-seeded regions, person and boundary diagnostics; see the [original guide](README.md#schp-atr-18-folder-smoke-test-cpu-onnx). |
| `depth_volume_poc.py` | Initial depth-gradient volume regions. |
| `pascal_nudenet_poc.py` | Pascal-7 previews and NudeNet-seeded competing regions. |
| `edge_nudenet_poc.py` | PiDiNet/other edge or SCHP LIP-20 score boundaries, overlapping zones, main-person cutout, box-fit and parameter comparisons. |
| `nudenet_depth_poc.py` | Target-box depth/normal contour comparisons; heuristic regions, not anatomical ground truth. |
| `nudenet_sam_poc.py` | NudeNet-prompted EfficientSAM-Ti INT8 masks, expanded box prompts and candidate-rank comparisons. |
| `custom_parts_poc.py` | Custom SEG/detection/OBB comparisons; full-image, penisV2-context, NudeNet-context or reviewed-OBB-context SEG; mask sweeps and coverage diagnostics. |
| `custom_obb_annotate.py` | penisV2-context SEG masks converted to draft oriented rectangles with review overlays, rejection reasons and resumable append. |
| `obb_rotation_probe.py` | One image rotated through selected angles, predictions mapped back, and optional augmented training-label previews. |
| `export_obb_onnx.py` | Explicit trusted-checkpoint FP32 ONNX conversion with raw or end-to-end OBB head selection. |

Use each tool's `--help` for its current flags: short aliases are tool-specific. In particular, `-f`, `-r` and `-s` do not have universal meanings across this collection. Output layouts vary: the original SCHP tool groups outputs by type, while later experiments generally use flat output directories.

## Custom-model comparison recipes

Commands below run from the repository root. Replace `C:\Images\Test` with an input folder. All model files are private local prerequisites, not assets included in a checkout. Folder input is nonrecursive and supported still images are sorted. Model identities, actual input shapes, settings and timings are recorded in JSON unless `-f / --images-only` is selected.

### Original SEG using the reviewed OBB as context

```powershell
uv run python tools/images/custom_parts_poc.py -i 'C:\Images\Test' -o 'tmp/obb-context-seg' -bc -sf -os 0.5 -oc dick=0.5 balls=0.55 dick-head=0.15 -ss 0.05 -sc dick=0.1 dick-head=0.05 hand=0.90 balls=0.08 -k 1.8 -ks box -n 0.1 -t 2 -mt -1
```

`-bc / --obb-context` defaults to `tmp/models/yolo26n_obb_duck_reviewed.onnx`; the original SEG defaults to `tmp/models/best_seg_dick.onnx`. OBB locates crops but does not produce masks. Crop anchors use the enclosing axis-aligned bounds of detected balls/main-part/head polygons, expanded by `-k`; overlapping crops merge. Crops are not deskewed or rotated. `-ks square` instead expands both dimensions from the anchor's longer side. SEG masks are not clipped to OBB polygons or person silhouettes.

Output choices:

- `-sf / --seg-only`: one mask-overlay image with legend per input; no OBB boxes or crop guides. Without `-bc`, this runs full-image SEG and loads no detector; it does not mean OBB-context SEG with hidden boxes.
- `-so / --superimpose` with `-bc`: one overlay combining SEG masks and OBB outlines/scores. Do not combine with `-sf`, `-cp` or `-sw`.
- `-cp / --compare`: side-by-side panels; without a single-image output mode the default saves separate reference/context images.
- `-f / --images-only`: suppress JSON, not a model or crop setting.
- `-w / --overwrite`: replace recognized custom-parts outputs only after models load; rejects unrelated files and redirected output entries. This is replacement, not resume.

`-os` and `-oc` control OBB confidence; `-ss` and `-sc` control SEG confidence. Lower `-n` means stronger raw-head NMS suppression; supported end-to-end OBB outputs skip NMS. `-mt -1` lowers the mask-logit cutoff after detection; it cannot recover a detection rejected by confidence. Predicted SEG-box clipping remains on unless `-uc` is selected. `-sw` reuses one SEG inference per crop to compare three mask thresholds with box clipping on/off; it requires `-vc` or `-bc` and replaces other panel modes.

### Coverage review, without modifying predictions

Add `-mm / --mismatches` to `-bc`, optionally setting `-mc / --min-coverage` (default `0.4`). Same-class OBB/mask pairs are matched greedily one-to-one by raster IoU. Coverage is matched SEG pixels **inside the visible rasterized OBB**, divided by that OBB's visible pixel count. Spill outside does not count toward coverage. Missing matches have zero coverage; less than 40% is flagged by default. JSON includes matches, coverage, IoU, spill, unmatched SEG indices and reasons. Red markings appear on context/superimposed views; `-sf` deliberately keeps its image clean, even if mismatch data is requested in JSON.

These are disagreement flags, not correctness judgments. A tight contour naturally covers less area than an enclosing rectangle; neither model is ground truth. The single-class `penis` model and the older `dick`/`dick-head`/`balls` taxonomy are not equivalent for same-class mismatch matching.

### New single-class SEG using penisV2 context

```powershell
uv run python tools/images/custom_parts_poc.py -i 'C:\Images\Test' -o 'tmp/new-seg-v2-context' -vc -cp -sm 'tmp/models/nsfw-seg-penis-s.onnx' -sn penis -ds 0.15 -ss 0.03 -k 1.3 -ks box -n 0.1 -t 2 -mt -1
```

`-vc` loads `tmp/models/penisV2.onnx` as the locator and skips OBB, NudeNet and full-image SEG. It renders locator/context-SEG comparison panels; it cannot be combined with `-sf`. For square context, use `-k 2.0 -ks square`. Custom models use centered aspect-preserving letterbox padding with fill 114, RGB NCHW `/255` preprocessing, and source-coordinate mask restoration.

The inspected NSFW checkpoint exports class `{0: 'item'}`. `-sn / --seg-name penis` explicitly renames a **single-class SEG** model; reports retain both effective `classes` and `exported_classes`. This does not rename a multi-class model, change its weights, or make the class equivalent to the older anatomical taxonomy. Omit the old model's `-sc` overrides for this checkpoint.

For new-model full-image SEG use `-sf -sm 'tmp/models/nsfw-seg-penis-s.onnx' -sn penis -ss 0.03 -n 0.1 -t 2 -mt -1`, without `-bc` or `-vc`. The 832px experiment uses a separate `nsfw-seg-penis-s-832.onnx` export; runtime size comes from the static ONNX input shape, not the filename or an inference CLI resize flag. All recipes are experiments, not tuned production defaults.

## Draft OBB annotation and rotation diagnosis

```powershell
uv run python tools/images/custom_obb_annotate.py -i 'C:\Images\Train' -o 'tmp/obb-drafts'
uv run python tools/images/custom_obb_annotate.py -i 'C:\Images\Train' -o 'tmp/obb-drafts' -rs
uv run python tools/images/obb_rotation_probe.py -i 'C:\Images\Test' -o 'tmp/obb-rotation-probe' -m 'tmp/models/yolo26n_obb_duck_reviewed.onnx'
```

The annotator uses penisV2 and the original contextual SEG model, then fits a minimum-area rectangle to the dominant mask component. It emits normalized PNG sources, YOLO four-corner OBB `.txt` labels, review overlays and JSON in a flat directory. Class IDs are `0: balls`, `1: dick`, `2: dick-head`. Tiny, fragmented, degenerate or out-of-frame rectangle fits are rejected rather than clipped into distorted polygons. No empty negative labels are generated for detector/SEG misses. Defaults include rectangular context 1.3, mask logit threshold -1, minimum component area 32 source pixels and dominant fraction 0.85; inspect `--help` before changing them.

`-rs / --resume` checks completed source hashes, model identities and recipe settings for selected existing sources, preserves prior files and appends unused numbers for new/incomplete work. A completed sidecar and its expected artifacts are required for a skip. Changing completed selected inputs/models/settings requires another output directory; resume does not regenerate reviewed labels. It loads models but skips inference for completed unchanged sources. Incomplete artifacts remain inspectable rather than being deleted.

Filtering images to those containing all three classes does not guarantee complete/correct annotations. The owner reviewed a private subset by removing rejected previews; companion cleanup, grouped splitting and ZIP creation were one-off local preparation, not reusable commands shipped in this PR. Build a dataset from surviving image/label pairs, not a stale subset manifest. Group exact duplicates and known source variants before splitting or augmentation; perceptual near-duplicates still need review. Only genuine target-free images are valid negative examples. Missing labels on visible targets can teach false background.

The rotation probe processes one image (the first sorted file if given a folder), at 0/30/60 degrees by default, without cropping the rotated canvas. It saves predictions alongside polygons mapped back to the original frame and a JSON report. `-ds / --dataset` optionally previews `augrot_`/`balls_extra_` training labels and records pixel-space corner angles without inference. A new output directory is required. This distinguishes rotation response from true alignment; axis-aligned original labels remain misaligned with a tilted object after rotating both image and rectangle.

For trusted OBB exports, use `uv run tools/images/export_obb_onnx.py --help` to invoke its isolated inline-dependency environment. Raw outputs and supported YOLO26 end-to-end `cx,cy,w,h,score,class,angle` outputs are decoded separately. Angles represent an undirected long axis, not anatomical base-to-tip direction. Raw OBB NMS uses exact polygon IoU rather than Ultralytics ProbIoU.

## Evidence and unresolved limits — 2026-11-06

- Owner Windows runs exercised SCHP, edges, depth/normals, SAM, custom models and annotation/review workflows. The owner trained/exported custom YOLO26 nano OBB models in Colab; supplied checkpoints and private review datasets are not repository assets or independently evaluated releases.
- Small-model inference on explicitly selected local photos was also run by the agent at the owner's request. Static Ruff/import/help checks are separate from those inference runs. No automated tests were added or run during the later owner-directed iterations or this documentation sync; historical synthetic checks in the original SCHP description are historical, not newly rerun acceptance.
- The new small SEG model produced contours the owner judged precise but missed many photos. Crop shape/scale and 640px versus 832px exports change confidence sharply: deterministic inference does not imply robustness to changed inputs. A single-photo raw-score diagnostic found 640px full-image above the chosen cutoff while the cropped/832px alternatives were below it.
- On one fixed first-10-photo smoke subset, 640px full-image SEG produced masks in 5/10 images; penisV2 rectangular context 1.3 and square context 2.0 each produced masks in 7/10, but on different images. These are mask-presence counts, not accuracy/recall or general throughput benchmarks. They do not establish a universal crop winner.
- The owner reported a PC crash while running the large NSFW `x` export. Only one saved large-model result was observed; memory pressure was not established as the cause. No retry or CPU-safety recommendation for that model is implied. Model file size does not establish peak runtime memory.
- Reviewed-OBB/SEG alignment and coverage reports expose disagreement, not verified anatomical boundaries. Public model claims, licenses, domain generalization, clean-environment reproducibility, memory bounds and desktop/release acceptance remain unverified. A multi-crop/fallback contour-refinement pipeline was discussed but is **not implemented**.
- The production-code delta is limited to `person_selection._instances(..., mask_threshold=0.5)`: finite `(0,1)` probability validation and conversion to a logit cutoff; existing callers retain the default zero-logit behavior. Experimental NudeNet preprocessing accepts an input-size keyword, defaulting to 320. These seams do not add production support for custom/640m checkpoints or change normal desktop model selection. No generation/provider request or new billable service call was made by the local image tools; owner Colab activity is separate.

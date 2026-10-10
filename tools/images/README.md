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

`person_zone_mask_poc.py` is standalone and non-generative: one image, one CPU
segmentation call, and a manual rectangle. It does not import DTFlow, modify the
production UI, call Draw Things, detect boxes, or establish inpainting compatibility.

### Model contract (required)

Use **TorchVision LR-ASPP MobileNetV3-Large**, weights
`LRASPP_MobileNet_V3_Large_Weights.COCO_WITH_VOC_LABELS_V1`, exported in eval mode
as a self-contained ONNX graph. This is semantic segmentation with 21 VOC classes;
person is class **15**, not a generic foreground mask. Multiple people form one
union; selecting a specific person instance is not implemented.

The graph must have exactly one float32 input `[1,3,320,320]` and one float32
output `[1,21,320,320]`, containing raw `model(x)["out"]` logits. Metadata must
include `mask_poc_contract=torchvision-lraspp-mobilenet-v3-large-voc21-320-v1`.
The required SHA-256 pins your chosen export; shape/metadata checks cannot prove
its architecture, trained weights or accuracy. Obtain/export only trusted models.

Preprocessing: first/only frame, EXIF transpose, alpha composited on white, RGB,
Pillow bilinear **stretch** to 320 square, divide by 255, normalize with mean
`[0.485,0.456,0.406]` and std `[0.229,0.224,0.225]`, float32 NCHW. This reduced
square resolution is an **experimental CPU tradeoff**, not the upstream transform
(which resizes the short edge to 520 while preserving aspect ratio). Distortion
and resolution can reduce mask quality; no speed/accuracy guarantee is claimed.
Postprocessing takes class argmax, selects class 15, then nearest-neighbour
resizes the binary mask to the oriented image dimensions. No softmax threshold,
feather, dilation or edge spill is used.

Sources: [model/weights](https://github.com/pytorch/vision/blob/main/torchvision/models/segmentation/lraspp.py),
[VOC categories](https://github.com/pytorch/vision/blob/main/torchvision/models/_meta.py),
[normalization](https://github.com/pytorch/vision/blob/main/torchvision/transforms/_presets.py),
[ORT threading](https://onnxruntime.ai/docs/performance/tune-performance/threading.html).

### Prepare a model separately

No weights are bundled, installed or downloaded by the tool. Keep them outside
Git (for example `C:\Models\mask-poc\`). Existing NudeNet/person-detector ONNX
files and U2Net foreground models do **not** satisfy this contract.

In a separately prepared CPU export environment with compatible PyTorch,
TorchVision and ONNX already installed, export your **existing local** official
checkpoint using the recipe below. This preparation is not part of PoC startup;
the repository's `mask-poc` dependency group intentionally excludes the heavy
export stack. Record those export package versions and checkpoint provenance
beside your model. The recipe is provided, not agent-executed/validated against
an actual pretrained export.

```python
from pathlib import Path
import onnx
import torch
from torchvision.models.segmentation import lraspp_mobilenet_v3_large

checkpoint = Path(r"C:\Models\mask-poc\lraspp_mobilenet_v3_large-d234d4ea.pth")
output = Path(r"C:\Models\mask-poc\lraspp-voc21-320.onnx")
assert checkpoint.is_file() and not output.exists()
torch.set_num_threads(1)
model = lraspp_mobilenet_v3_large(weights=None, weights_backbone=None, num_classes=21)
model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
model.eval()

class Logits(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model
    def forward(self, image):
        return self.model(image)["out"]

torch.onnx.export(
    Logits(model).eval(), torch.zeros(1, 3, 320, 320), str(output),
    input_names=["image"], output_names=["logits"],
    opset_version=17, dynamo=False, external_data=False,
)
graph = onnx.load(str(output))
onnx.helper.set_model_props(graph, {
    "mask_poc_contract": "torchvision-lraspp-mobilenet-v3-large-voc21-320-v1"
})
onnx.checker.check_model(graph)
onnx.save(graph, str(output))
```

### Run from repository root (PowerShell)

```powershell
uv sync --locked --group mask-poc
$model = 'C:\Models\mask-poc\lraspp-voc21-320.onnx'
$checksum = (Get-FileHash -LiteralPath $model -Algorithm SHA256).Hash.ToLowerInvariant()
uv run --locked --group mask-poc python tools/images/person_zone_mask_poc.py --help
uv run --locked --group mask-poc python tools/images/person_zone_mask_poc.py `
  -s 'C:\Images\person.jpg' -m $model -c $checksum `
  -b 100 150 500 700 -t 1 -o 'tmp/person-zone/run-001'
```

Compare the checksum with the checksum of your trusted export; hashing an
arbitrary file alone does not establish its provenance. The dedicated non-default
uv group installs CPU `onnxruntime`, NumPy and Pillow through the lockfile. No
runtime pip tricks or startup installation occur. ORT wheel compatibility on
old CPUs is **not** established by VideoSceneDetect working there; test on the
actual machine. NumPy retains the repository's legacy-CPU pin.

The box is `x0 y0 x1 y1` in **EXIF-oriented source pixels**, origin top left,
left/top inclusive, right/bottom exclusive. Out-of-bounds or empty boxes fail;
there is no silent clipping. Choose a new output directory strictly below the
repository's ignored `tmp/`; existing or redirected directories are rejected.

Outputs have oriented source dimensions:

- `person_mask.png`: binary person union.
- `zone_mask.png`: binary rectangle.
- `edit_mask.png`: exact intersection (255 selected, 0 unselected).
- `overlay.jpg`: source preview tinted red only inside the edit mask.
- `report.json`: model path/checksum/contract, actual session provider, package
  and platform identity, preprocessing/mapping, pixel counts, thread settings and
  separate model-read/checksum, image-decode, session-load, preprocessing, cold
  inference, postprocessing and image-write timings.

One caller/worker, one decoded image and one reused session; intra-op threads
range from 1 to `min(4, logical CPU count)`, default 1; inter-op is 1, execution
sequential, thread spinning off, **CPUExecutionProvider only**. There is no GPU
probe or fallback. Limits: 64 MiB per input/model, 16 million source pixels;
use a self-contained graph (external tensor files are unsupported). Files are
created exclusively, without copying source metadata; failures return code 2,
may leave partial output and never retry, overwrite or delete. Successful writes
return 0, including an empty edit mask; inspect it rather than treating it as
segmentation acceptance. Report paths and overlay content may be sensitive.

### Validation and next step

Focused offline tests exercise geometry, preprocessing, contract failures and
output/report composition with a **fake session**. They are not real-model
inference or performance evidence. No production UI/backend integration is added.

Next, independently validate a real pretrained export and manual-box samples on
the target CPU. Only then add a separate automatic-box adapter patterned after
VideoSceneDetect's letterbox preprocessing and source-coordinate mapping. Its
YOLO box decoder must remain distinct from this 21-class segmentation decoder;
automatic detection is deliberately not implemented in this first version.

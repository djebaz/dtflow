# CPU person-zone mask PoC

Date: 2026-10-10. Scope: developer tool only, not Phase 9 implementation/acceptance.

## Contract and implementation

The owner requested one local image, CPU person segmentation, manual XYXY box,
and `edit = person AND box`. The standalone tool is under
[tools/images](../../tools/images/README.md#cpu-personzone-mask-poc), with a dedicated
non-default `mask-poc` uv group. Production dependencies, UI and generation paths
are unchanged. NumPy keeps the existing legacy-CPU pin.

Selected model: externally prepared TorchVision LR-ASPP MobileNetV3-Large
COCO_WITH_VOC_LABELS_V1 ONNX, fixed 320 square input and VOC21 logits; person class
15. Required custom metadata and expected SHA-256 identify the supplied contract.
They are not independent architecture/provenance verification. No weights are
committed/downloaded. The experimental square resize differs from upstream's
aspect-preserving short-edge 520 preset. Exact preprocessing, export recipe,
coordinate conventions, bounds and limitations are documented beside the tool.

One sequential CPU session, one decoded image, one inference call. Threads default
to 1, capped at 4/logical CPU count; ORT spinning is disabled. No GPU probing,
provider fallback, auto installs/downloads, Draw Things access or generation.
Outputs are exclusive new files below ignored repository tmp/. Originals are read
only. Failed writes may leave partial output; no cleanup deletes user files.

## Verification

- Dedicated uv group synchronized; lock check passed.
- Ruff lint/format, Python compilation, imports and CLI help checked.
- Three focused offline tests passed: geometry/preprocessing, fake-session CPU
  options/contract rejection, output/report composition and source preservation.
- Tests use synthetic logits/fake sessions, not trained-model inference.
- Full application/Qt suite was not run for this isolated tool.
- No model export, real segmentation, training, generation or billable action ran.

## Remaining

Prepare the trusted local model using the documented recipe, then owner-smoke-test
manual boxes and overlay/masks on actual CPU targets. No measured speed or mask
accuracy claim yet. Real export and old-CPU ORT compatibility remain unverified.
Automatic box detection is a follow-up using VideoSceneDetect-style letterbox and
coordinate mapping with its own decoder; it is deliberately absent from v1.
Backend mask polarity/preparation/inpainting compatibility remains separate work.

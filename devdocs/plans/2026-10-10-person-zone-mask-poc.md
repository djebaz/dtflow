# Plan: CPU person-zone mask PoC

Date: 2026-10-10. Developer tool only, not Phase 9 production acceptance.

## Scope
- In: CPU semantic person mask, manual XYXY intersection, five ignored outputs,
  timings/checksum/provider report, dedicated non-default mask-poc uv group.
- Out: UI/backend changes, automatic boxes, downloads/export/installation tricks,
  model mutation, generation, old-CPU speed claims and inpainting compatibility.

## Action items
- [x] Add standalone tool, bounded CPU execution and dedicated locked dev group.
- [x] Add explicit FastSeg profile for owner-supplied 320 models, retaining TorchVision.
- [x] Verify upstream FastSeg normalization and Cityscapes mapping (person 11, not rider 12).
- [x] Document short-argument smoke command with exact owner-file checksums.
- [ ] Owner real-model/target-CPU smoke and review of masks/overlay.
- [ ] Later independent automatic-box adapter with VideoSceneDetect-style coordinates.

## Decisions and Design Changes
- Initial profile: TorchVision MobileNetV3-Large VOC21, person15, required metadata.
- Owner then supplied FastSeg MobileV3Small F64 320 exports plus checkpoint/export
  recipe. FastSeg is Cityscapes19, person11, RGB /255 then ImageNet normalization.
  Explicit -p fastseg selects that contract without requiring metadata absent from
  these files; tensor shapes and supplied checksum are still validated.
- The owner changed pooling kernel/stride to40x40, simplified the graph and produced
  dynamic QInt8 weights. The PoC does not modify models or assert numerical parity.
- Selected FastSeg FP32 file: tmp/320models/simplified.onnx, SHA256
  3377fbbb7aaf8858c14449638d0968c5fafba139661a170d8ae1cbf7dbff7048.
  INT8 alternative: tmp/320models/mobilev3small_lraspp_f64_int8.onnx, SHA256
  1e9018116a6a790c62c34956d308e9a63b5575ded41ba4217022d27f9de36159.
- Existing source files/models are preserved; no automatic downloads. New output
  directories/files only under ignored tmp; partial output is never auto-deleted.
- Owner invoked fast-implement during adaptation: stop all further test authoring,
  test execution and smoke runs. Final acceptance belongs to owner.

## Open questions
- No coding blockers; real export quality, mask suitability, quantization parity
  and old-CPU performance remain acceptance questions, not established facts.

## Validation
- Historical initial checks: lock sync/check, Ruff, compilation/import/help and
  three fake-session checks passed. During FastSeg adaptation, before the owner's
  stop instruction, six parametrized fake-session checks passed (1.26s).
- Both supplied 320 models were loaded for metadata inspection on CPU; no run/inference.
- After fast-implement activation: no tests or smoke tests added/run. Static
  syntax/help/format checks only; final tests and smoke tests deferred to user.
- No real pretrained segmentation inference, training, generation or billable
  provider actions. No packaging: isolated source developer tool.

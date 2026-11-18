# Unreleased

Changes through 6.4.1 are consolidated in [DTFlow 6.4.1](v6.4.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Generate has a Controlnet mode: generate from a photograph or a body map with a Union Pro 1.0 pose control on DT+ Cloud, with control force and apply from / to, a person pick for photographs, omitted joints named under the map and a Source / Pose / Result canvas. Only `flux_1_dev_q5p` with Union Pro 1.0 q5p at 0.7 is verified; other FLUX.1 checkpoints show "not verified". Gallery Use settings restores the pose.

## Changed

- The Queues composer shows editable **Fixed LoRAs** independently of LoRA weight sweeps and lists available models across inventory families in **Add model** (PR #510, refs #507).
- The pose detector is now YOLO26n-pose (reinstall it once from the Pose guidance card or the CLI).
- The sampler list offers every sampler in all Generate modes, sorted A to Z.
- The Generate mode (Text to image, Inpaint, Controlnet, Scripts) is remembered between launches.

## Fixed

- **Edit as new queue…** no longer silently carries unwanted fixed LoRAs from older plans; reconstructed drafts and manually removed fixed LoRAs discard obsolete DT+ receipt bindings without changing the source queue (PR #510).
## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: #499
- Scope: Version bump 6.4.1 and release-note consolidation.

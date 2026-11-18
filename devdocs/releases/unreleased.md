# Unreleased

Changes through 6.4.1 are consolidated in [DTFlow 6.4.1](v6.4.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Generate has a Controlnet mode: generate from a photograph or a body map with a Union Pro 1.0 pose control on DT+ Cloud, with control force and apply from / to, a person pick for photographs, omitted joints named under the map and a Source / Pose / Result canvas. Only `flux_1_dev_q5p` with Union Pro 1.0 q5p at 0.7 is verified; other FLUX.1 checkpoints show "not verified". Gallery Use settings restores the pose.
- Connection → Custom LoRAs lists managed LoRAs that have no retained local copy and lets you remove their local registry records after confirmation; remote DT+ storage and saved results are unchanged (#511).

## Changed

- The pose detector is now YOLO26n-pose (reinstall it once from the Pose guidance card or the CLI).
- The sampler list offers every sampler in all Generate modes, sorted A to Z.
- The Generate mode (Text to image, Inpaint, Controlnet, Scripts) is remembered between launches.
- Scripts mode has a Controlnet tab that returns to Generate in Controlnet mode (#511).

## Fixed

- Startup always opens on Connection; selecting Generate later opens its remembered sub-mode, including Scripts, without a startup redirect (#511).
- Removing a registry-only LoRA updates the affected row without rescanning all retained LoRAs or rebuilding the entire table (#511).

## Upgrade and compatibility

## Validations checklist

- PR #511: owner reports tests and Windows smoke checks passed, including improved LoRA deletion responsiveness and no UI stalls. Commands, counts and exact executable identity were not provided here; packaged-release acceptance remains separate.

## Release audit

- PRs: #499; unreleased PR #511.
- Scope: Version bump 6.4.1 and release-note consolidation; PR #511 adds local LoRA cleanup and startup/navigation corrections.

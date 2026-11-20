# Unreleased

Changes through 6.4.1 are consolidated in [DTFlow 6.4.1](v6.4.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Controlnet pose generations can be queued with **Images N**, **Repeat**, and **New queue plan** (DT+ Cloud only): each item gets a separate seed and shares one staged pose map and Union control. Queue creation stays local; Start sends the map and may consume credits (PR #513, refs #505; owner smoke pending).
- Local pose templates retain a bounded body map and exact control settings from a captured Controlnet or recorded queue draft, without execution-file dependencies or upload consent. Loading changes only the composer; creating/starting the queue keeps the existing DT+ checks and consent (PR #513; owner smoke pending).
- Generate has a Controlnet mode: generate from a photograph or a body map with a Union Pro 1.0 pose control on DT+ Cloud, with control force and apply from / to, a person pick for photographs, omitted joints named under the map and a Source / Pose / Result canvas. Only `flux_1_dev_q5p` with Union Pro 1.0 q5p at 0.7 is verified; other FLUX.1 checkpoints show "not verified". Gallery Use settings restores the pose.
- Connection → Custom LoRAs lists managed LoRAs that have no retained local copy and lets you remove their local registry records after confirmation; remote DT+ storage and saved results are unchanged (#511).

## Changed

- The Queues composer shows editable **Fixed LoRAs** independently of LoRA weight sweeps and lists available models across inventory families in **Add model** (PR #510, refs #507).
- The pose detector is now YOLO26n-pose (reinstall it once from the Pose guidance card or the CLI).
- The sampler list offers every sampler in all Generate modes, sorted A to Z.
- The Generate mode (Text to image, Inpaint, Controlnet, Scripts) is remembered between launches.
- Scripts mode has a Controlnet tab that returns to Generate in Controlnet mode (#511).

## Fixed

- Generate/Retry can hand a pose to the queue-behind creation path without an argument-count or queue-ID unpacking error. Pose templates own their maps instead of retaining staged execution identities (PR #513; source-reviewed, owner smoke pending).
- Queued pose items preserve their control and retained map across dispatch, Relaunch, Retry and Edit as new. Missing staged inputs fail explicitly instead of falling back to unguided text-to-image generation (PR #513; acceptance pending).
- **Edit as new queue…** no longer silently carries unwanted fixed LoRAs from older plans; reconstructed drafts and manually removed fixed LoRAs discard obsolete DT+ receipt bindings without changing the source queue (PR #510).
- Startup always opens on Connection; selecting Generate later opens its remembered sub-mode, including Scripts, without a startup redirect (#511).
- Removing a registry-only LoRA updates the affected row without rescanning all retained LoRAs or rebuilding the entire table (#511).

## Upgrade and compatibility

- Template database schema stays 1; pose recipes use version 2 while existing text-only version-1 recipes still load. Older apps refuse libraries containing v2 recipes without changing them. Existing per-recipe/library limits remain; pose PNGs are additionally limited to 128 KiB before Base64 encoding. No automatic migration, image extraction, upload or queue replay is added.
## Validations checklist

- PR #511: owner reports tests and Windows smoke checks passed, including improved LoRA deletion responsiveness and no UI stalls. Commands, counts and exact executable identity were not provided here; packaged-release acceptance remains separate.

## Release audit

- PRs: #499, #504, #510, #511, #513.
- Scope: Version bump 6.4.1 and release-note consolidation; Controlnet generation, pose queues and local map-owning templates; queue composer corrections; local LoRA cleanup and startup/navigation corrections; pose queue-behind repair.

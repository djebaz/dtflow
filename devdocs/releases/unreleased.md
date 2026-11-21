# Unreleased

Changes through 6.4.1 are consolidated in [DTFlow 6.4.1](v6.4.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- **Video companion lifecycle (PR #535, refs #530):** Images Trash/Delete now include sidecar-proven MP4 clips with their posters using the existing managed-original ledger and partial-failure reporting. Mixed Images exports copy original MP4s without re-encoding while still converting images to JPEG; missing/unverifiable clips fail visibly instead of exporting the poster. Shared export controls now label saved videos correctly. Sidecars and generation history remain retained; automatic restore is not supported. Source-reviewed only; Windows deletion/export smoke pending.

- **Images video playback:** Videos in Detail now have a compact single-row toolbar with an expanding seek bar and blue playhead marker, Play/Pause, elapsed/total time, mute, restart, Autoplay and Close. Click the poster/video or use K/S for Play/Pause; W/X seek one second, numeric keypad 4/6 seek ten seconds, keypad 0 restarts, and M mutes. Ordinary Left/Right browse outputs. **Autoplay** is off by default and saved in viewer preferences; when enabled, opening or returning to a video starts playback at 0:00. Grid/List and the filmstrip show video durations, and Detail shows saved/probed media facts. Still-image behavior is unchanged (PR #534, closes #531; owner-accepted Windows UX, Preview passed on the code head; bundled playback remains #529).

- Pose-enabled Queue drafts expose ControlNet checkpoint/quantization, strength, control force and Apply from/to, with a read-only map preview and explicit replacement by another prepared body map. Edits remain local to the draft; template Save/Update remains explicit, and photo extraction stays in Generate (PR #522, refs #515; owner-reported functional acceptance and automatic CI/Preview passed on `c22d8cd8`; UI/UX polish is tracked separately in #528, not broad DPI/packaged acceptance).
- Controlnet pose generations can be queued with **Images N**, **Repeat**, and **New queue plan** (DT+ Cloud only): each item gets a separate seed and shares one staged pose map and Union control. Queue creation stays local; Start sends the map and may consume credits (PR #513, refs #505; owner-reported Windows smoke completed).
- Local pose templates retain a bounded body map and exact control settings from a captured Controlnet or recorded queue draft, without execution-file dependencies or upload consent. Loading changes only the composer; creating/starting the queue keeps the existing DT+ checks and consent (PR #513; owner-reported template Save/Load smoke completed).
- Generate has a Controlnet mode: generate from a photograph or a body map with a Union Pro 1.0 pose control on DT+ Cloud, with control force and apply from / to, a person pick for photographs, omitted joints named under the map and a Source / Pose / Result canvas. Only `flux_1_dev_q5p` with Union Pro 1.0 q5p at 0.7 is verified; other FLUX.1 checkpoints show "not verified". Gallery Use settings restores the pose.
- Connection → Custom LoRAs lists managed LoRAs that have no retained local copy and lets you remove their local registry records after confirmation; remote DT+ storage and saved results are unchanged (#511).

## Changed

- The Queues composer shows editable **Fixed LoRAs** independently of LoRA weight sweeps and lists available models across inventory families in **Add model** (PR #510, refs #507).
- The pose detector is now YOLO26n-pose (reinstall it once from the Pose guidance card or the CLI).
- The sampler list offers every sampler in all Generate modes, sorted A to Z.
- The Generate mode (Text to image, Inpaint, Controlnet, Scripts) is remembered between launches.
- Scripts mode has a Controlnet tab that returns to Generate in Controlnet mode (#511).

## Fixed

- The desktop Connection and Generate provider selectors no longer offer the internal Fake backend. Previously saved Fake selections require an explicit choice of Local Draw Things or DT+ Cloud before generation; existing Fake data and CLI/test/preview support are preserved (PR #519, refs #518; owner-reported manual testing and CI/Preview passed on the preceding code commit).
- Documentation examples no longer embed owner-specific workstation paths. The WSL X11 helper derives the current Windows application-data directory, selected WSL user's home and its own checkout path instead of hard-coding an account; this does not remove older Git history or Action logs. Static validation only; the helper was not executed.
- Repository path/PII scanning now supports the Action's existing check presets and extension selection using the newer shared scanner; scanner failures are no longer summarized as a clean scan. These are heuristic findings, not proof of private-data exposure; the updated Action has not been rerun by the agent.
- Controlnet → New queue plan explicitly refreshes the current pose map/controls through Use Generate settings' pose-only mode, preserving the Queue draft's base settings and plan variations instead of reusing a stale composer pose. The composer summary and queued Parameters tooltip expose exact pose checkpoint/quantization, strength, force and range (PR #513; owner-reported Windows retest confirmed non-default pose settings are preserved).
- Generate/Retry can hand a pose to the queue-behind creation path without an argument-count or queue-ID unpacking error. Pose templates own their maps instead of retaining staged execution identities (PR #513; source-reviewed; owner smoke confirmed queue-behind and template workflows).
- Queued pose items preserve their control and retained map across dispatch, Relaunch, Retry and Edit as new. Missing staged inputs fail explicitly instead of falling back to unguided text-to-image generation (PR #513; owner smoke confirmed pose dispatch, Relaunch, Retry and Edit as new; missing/altered-map failure not directly exercised).
- **Edit as new queue…** no longer silently carries unwanted fixed LoRAs from older plans; reconstructed drafts and manually removed fixed LoRAs discard obsolete DT+ receipt bindings without changing the source queue (PR #510).
- Startup always opens on Connection; selecting Generate later opens its remembered sub-mode, including Scripts, without a startup redirect (#511).
- Removing a registry-only LoRA updates the affected row without rescanning all retained LoRAs or rebuilding the entire table (#511).

## Upgrade and compatibility

- Template database schema stays 1; pose recipes use version 2 while existing text-only version-1 recipes still load. Older apps refuse libraries containing v2 recipes without changing them. Existing per-recipe/library limits remain; pose PNGs are additionally limited to 128 KiB before Base64 encoding. No automatic migration, image extraction, upload or queue replay is added.
## Validations checklist

- PR #513: owner-reported Windows smoke covered Images N, Repeat, New queue plan with non-default controls, DT+ pose dispatch and saved metadata, Save paused and upload/credit disclosure, backend/checkpoint refusals, text-to-image and Inpaint queue creation, Relaunch, Retry item, Edit as new, and pose template Save/Load. All AC-1 through AC-10 are checked in the PR. Missing/altered pose-map failures, unavailable cloud control inventory, older file compatibility and template corruption/size limits were not directly exercised in this smoke; CI and packaged-release acceptance are separate.
- PR #511: owner reports tests and Windows smoke checks passed, including improved LoRA deletion responsiveness and no UI stalls. Commands, counts and exact executable identity were not provided here; packaged-release acceptance remains separate.

## Release audit

- PRs: #499, #504, #510, #511, #513, #522.
- Scope: Version bump 6.4.1 and release-note consolidation; Controlnet generation, pose queues and local map-owning templates; queue composer corrections and direct pose editing/map replacement; local LoRA cleanup and startup/navigation corrections; pose queue-behind repair.

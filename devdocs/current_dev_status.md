# Current development status

Updated 2026-11-18 for 6.4.1 preparation and in-review PR #510; the merged-main baseline remains `81d68eb7` through PR #498. [6.4.0](https://github.com/djebaz/dtflow/releases/tag/v6.4.0) is published. See [6.4.1 notes](releases/v6.4.1.md) and its [release audit](plans/2026-11-16-release-6.4.1.md) for the next release scope and build gates.

## Delivered since 6.4.0

- DT+ generation timelines, optional `-time` detail and one Echo per generation path; no measured generation-speed claim.
- Performance measurements require `-x -perf`, independently of ordinary developer logging and `-time` detail.
- Taller selected-LoRA list with per-row weights and consistent 0.05 wheel stepping.
- Queue concurrency follows Connection's DT+ default at each start; separate queue controls and stored queue/recipe limits no longer govern dispatch.
- Slideshow New direction shows arrivals next under Newest first sorting, then resumes forward playback.
- Downloads runs bounded stale staging/orphan-source cleanup once at startup, off Qt; failed/refused conversion sources remain retained. Civitai documentation now covers tokens, permissions, file retention and recorded acceptance boundaries.

## Unreleased: Controlnet mode (#503, PR #504)

- Generate Controlnet mode: pose guidance from a photograph (local YOLO26n-pose ONNX) or a body map with Union Pro 1.0 on DT+ Cloud; control force, apply from / to, person pick, Gallery Use settings restore. Verified only for `flux_1_dev_q5p` with Union Pro 1.0 q5p at 0.7; other FLUX.1 checkpoints are "not verified". Queueing a pose is tracked in #505.

## Unreleased: queue composer corrections (#507, PR #510)

- **Edit as new queue…** exposes and edits inherited Fixed LoRAs, excludes varying LoRAs from reconstructed legacy bases, and avoids carrying obsolete DT+ bindings into the new draft. **Add model** reads all known inventory families, not the Generate family's picker.
- Owner reports a successful smoke test and supplied Windows PowerShell output of **179 passed in 36.40s**. The test command, tested backend and executable identity were not provided; this is owner-reported evidence, not independent agent execution or final release acceptance. The Fixed LoRAs/weight-chip removal distinction is explicitly accepted by the owner.

## 6.4.0 foundations

- Civitai Downloads/Browse, exact version/file choices, saved filters, optional auto-prepare and managed provenance; bounded search streams do not promise global sort order.
- Managed multicore CPU q8p, DT+ storage/headroom/recovery, version-aware Prepare reuse, descriptive metadata and conversion/overlay reliability fixes.
- The published 6.4.0 EXE includes the refreshed component catalogue and required kmeans1d package data. Its artifact acceptance does not automatically validate a new executable.

## Implemented phase map

| Area | Delivered foundations | Remaining boundary |
| --- | --- | --- |
| Backends, CLI and persistence | Fake/local/DT+, immutable resolved requests, durable queues/attempts, receipts and shared diagnostics. | Wider protocol/media compatibility and remote cancellation are not universal guarantees. |
| Qt, planning and Images | Shared forms/theme, independent drafts, queue planning, Grid/List/Detail, presets, export, snapshots, slideshow and guarded removal. | Windows/DPI, cache budgets, startup performance and platform mappings retain their scoped evidence. |
| Image operations and Workflows | Immutable inputs, local detectors, inpainting, consent-based model provisioning and resumable per-branch workflow execution. | No universal model/control support or stock ComfyUI runtime compatibility. |
| Scripts | Native library/editor/setup, durable linked child execution and bounded batch slots. | Broader samples, virtual pixels/masks and detection/guidance retain existing issue ownership. |

## Current validation and release gate

- Feature PRs record scoped owner Windows/DT+ smoke and Linux offline checks; the release audit identifies their scope. No final-release full-suite result is inferred from those earlier runs.
- The 6.4.0 owner suite (700 passed, 3 skipped) and Fast previews (31/31) are historical evidence for that release, not 6.4.1 results.
- 6.4.1 uses the owner-run local Windows build path because Actions credits remain exhausted. No workflow is dispatched; clean artifact identity/checksum/manifest and owner acceptance remain required before tagging/publication.

## Remaining hardening and compatibility

- Advanced scheduling/fairness, broader verified image/cloud/media operations, script sample coverage and mixer acceptance remain separately scoped.
- Logs #106 memory/byte budgets and #107 fault/native/storage/policy/DPI acceptance remain open; export admission is not full budget compliance. Platform #77, gallery #88 and startup/package #169/#170 retain existing ownership.
- Execution schema remains 9; LoRA asset registry remains 6. Back up complete state and outputs; unsupported registries are refused rather than reset. Queue templates remain version 1. Restore a pre-upgrade backup for downgrade.
- No simultaneous Windows/WSL SQLite writers, silent provider/account switch, implicit upload consent or uncertain-work replay. Manual Retry can submit a new billable request; existing bounded automatic retry semantics remain unchanged.
- Older-CPU source observations do not establish compatibility of a new packaged executable. Follow [releasing](guides/releasing.md) and [building](guides/building.md) for artifact acceptance.

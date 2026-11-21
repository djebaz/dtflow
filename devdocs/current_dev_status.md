# Current development status

Updated 2026-11-21 for Queue pose editor PR #522; prior release/feature evidence below retains its recorded scope. [6.4.0](https://github.com/djebaz/dtflow/releases/tag/v6.4.0) is published. See [6.4.1 notes](releases/v6.4.1.md) and its [release audit](plans/2026-11-16-release-6.4.1.md) for the next release scope and build gates.

## In review: video UX alignment (#540, PR #545; 2026-11-21)

Video controls reuse Generate's shared section-card design, wrapping duration evidence and neutral source actions. Source/Width/Height hover and keyboard focus expose the live centered crop without replacing the result. Draft validation notifies Generate availability; second-pass progress uses only that pass's reported counts. Static validation is separate from pending Windows/DPI, restart and backend acceptance; see the [desktop guide](guides/desktop.md#generate).

## Unreleased: Windows video packaging (#529, PR #533)

PR #533 supports five explicit Windows FFmpeg packaging choices: Gyan 8.1.2 Essentials (standalone CLI default, static); TAS-FFMPEG 8.1.2 (experimental shared DLLs); Unpins FFmpeg 8.1.2-1 (experimental multicall without GPU acceleration); imageio-ffmpeg 0.5.1 (FFmpeg 4.2.2 + Gyan ffprobe 8.1.2); and imageio-mini (Windows workflow default, same encoder + stripped ffmpeg-mini-win64 FFprobe 7.1). Each pins the publisher archive checksum and bundles the required `ffmpeg.exe` / `ffprobe.exe` paths with matching third-party notices and manifest checksums. Unpins uses two named copies of its single multicall EXE; no runtime application change is needed. Frozen video requests use only the bundled tools while source runs keep `PATH`; worker-side readiness checks and the existing no-console/encoding policy remain unchanged.

**Evidence status:** the owner triggered a successful Windows Actions build with Gyan on earlier PR head `d04f91a` (EXE 189,480,625 bytes); this is build evidence only, not a completed frozen generation/playback smoke. An owner-triggered TAS Actions build succeeded on `fb88392` (EXE 182,853,916 bytes, run `44881166356`), about 6.6 MB smaller than the earlier Gyan build, but neither is a controlled same-commit comparison. An Unpins Actions build succeeded at `e3ab45e` (EXE 201,977,106 bytes, run `44882530391`), larger than both Gyan and TAS. The imageio + Gyan probe build succeeded on `6f7524d` (EXE 174,997,706 bytes, [run 44883546323](https://github.com/djebaz/dtflow/actions/runs/44883546323)), the smallest measured so far. The imageio-mini Windows EXE built successfully on `9c5caf2` at **140,140,874 bytes** ([run 44884296087](https://github.com/djebaz/dtflow/actions/runs/44884296087)); the owner reported successful real video generation in that standalone EXE. Windows Actions now defaults to this provider; the independent CLI builder defaults to Gyan. Detailed Qt playback, stream/probe metadata and corresponding-source delivery still require itemized review. The owner reported functional frozen video generation; no local smoke or distribution audit was performed by the implementation agent. Public redistribution must satisfy the GPL's corresponding-source requirements; provenance URLs alone are insufficient. See [building](guides/building.md) and [PR #533](https://github.com/djebaz/dtflow/pull/533).

## Delivered since 6.4.0

- DT+ generation timelines, optional `-time` detail and one Echo per generation path; no measured generation-speed claim.
- Performance measurements require `-x -perf`, independently of ordinary developer logging and `-time` detail.
- Taller selected-LoRA list with per-row weights and consistent 0.05 wheel stepping.
- Queue concurrency follows Connection's DT+ default at each start; separate queue controls and stored queue/recipe limits no longer govern dispatch.
- Slideshow New direction shows arrivals next under Newest first sorting, then resumes forward playback.
- Downloads runs bounded stale staging/orphan-source cleanup once at startup, off Qt; failed/refused conversion sources remain retained. Civitai documentation now covers tokens, permissions, file retention and recorded acceptance boundaries.

## Merge preparation: Queue pose editing (#515, PR #522)

- The independent Queue composer exposes exact pose controls, a local worker-prepared map preview and explicit prepared-map replacement without changing Generate or saved queues/templates. The owner reports **“all works”**; supplied logs record eight succeeded cloud Queue items, and automatic CI/Preview passed on rebased code head `c22d8cd8`. Functional ACs are accepted on owner report plus source review, not exhaustive failure/DPI or independently attested packaged acceptance. Non-blocking source-reviewed UI/UX improvements are separately tracked in [#528](https://github.com/djebaz/dtflow/issues/528); no UI changes are added during merge preparation. See the [implementation/evidence plan](plans/2026-11-21-queue-pose-editor.md).

## Unreleased: desktop provider selection (#518, PR #519)

- Connection and Generate no longer offer Fake as a desktop provider. A saved Fake session restores without an active provider, requiring an explicit Local Draw Things or DT+ Cloud selection before generation. Fake remains supported by the CLI, tests and offline previews; saved results and backend registration are retained. Owner-reported manual acceptance and successful CI/Preview on PR #519 are not packaged Windows release acceptance.

## Unreleased: video generation POC (#521, PR #523)

- Throwaway CLI `tools/video_poc/video_poc.py` sends one paid DT+ video generation per call (LTX-2.3 and Wan 2.1 verified through it) and records response shape, timings and the MP4 check in `tmp/`. Owner-reported Windows runs: LTX with sound works, one start image is followed, several start images use only the first frame; storage option B (MP4 plus poster image) accepted. No product video support yet and no acceptance of deliberate error cases or larger sizes. See [the POC findings](research/2026-11-21-video-generation-poc.md).

## Unreleased: Controlnet mode (#503, PR #504)

- Generate Controlnet mode: pose guidance from a photograph (local YOLO26n-pose ONNX) or a body map with Union Pro 1.0 on DT+ Cloud; control force, apply from / to, person pick, Gallery Use settings restore. Verified only for `flux_1_dev_q5p` with Union Pro 1.0 q5p at 0.7; other FLUX.1 checkpoints are "not verified". Queueing a pose is implemented in PR #513 (#505), pending owner smoke and review.

## Unreleased: Controlnet pose queues (#505, PR #513)

- Images N, Repeat and Queue plans can retain one locally staged pose map and its DT+ Union control across individual single-image items. The queue dispatcher restores and verifies it before submission; Relaunch/Retry/Edit as new keep the pose evidence. No provider inference or owner acceptance was run by the agent.
- Review repairs correct Generate/Retry queue-behind argument and queue-ID handling. The owner-approved template extension saves bounded body-map bytes and exact controls from live or recorded poses, independent of source execution files; text-only v1 recipes remain supported. Template Save/Load stays local and never grants upload consent.
- Owner Windows/DT+ smoke reports Images N and Repeat → Add to queue preserve controls, but Prepare Queue / New queue plan reused defaults (0.70, Balanced, 0..1). The owner-selected follow-up explicitly refreshes only the pose map/controls through Use Generate settings for Controlnet's New queue plan action, preserving the Queue draft's base settings and configured variation rows; exact pose values are inspectable in the composer and item Parameters tooltip. AC-1 remains unchecked pending runtime retest; the smoke report did not identify the tested executable revision or prove provider-effective values. See [owner findings](https://github.com/djebaz/dtflow/pull/513#issuecomment-7051433713).
- **Pending validation:** owner Windows/DT+ smoke, saved-map failure/retry paths, and full CI. The owner requested updating AC-3 to permit inventory refresh and local durable map staging after scope checks, while preserving Start/Add-to-queue upload consent; its Must level and unchecked behavioral acceptance remain unchanged. See the [review-fix plan](plans/2026-11-20-pose-queue-review-fixes.md) for static evidence and limits.

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
- Execution schema remains 9; LoRA asset registry remains 6. Back up complete state and outputs; unsupported registries are refused rather than reset. Queue template database schema remains 1, with text-only recipe v1 and pose recipe v2. Restore a pre-upgrade backup for downgrade.
- No simultaneous Windows/WSL SQLite writers, silent provider/account switch, implicit upload consent or uncertain-work replay. Manual Retry can submit a new billable request; existing bounded automatic retry semantics remain unchanged.
- Older-CPU source observations do not establish compatibility of a new packaged executable. Follow [releasing](guides/releasing.md) and [building](guides/building.md) for artifact acceptance.

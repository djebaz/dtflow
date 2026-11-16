# Current development status

Updated 2026-11-16 for 6.4.0 preparation, from merged main `d14c0ece` through PR #480. [6.3.0](https://github.com/djebaz/dtflow/releases/tag/v6.3.0) is the published baseline. [6.4.0 notes](releases/v6.4.0.md) summarize the next release; the [release audit](plans/2026-11-16-release-6.4.0.md) holds historical checkpoints and exact-artifact gates.

## Delivered since 6.3.0

- Downloads: saved Civitai credentials, bounded link/ID downloads, exact version/file choices, conversion/source provenance, Browse filters/tags/sorts, persistent independent AND/OR modes and explicitly enabled auto-prepare. Startup never replays a search; keyword-only search has no keyword-as-tag fallback. Duplicate cards merge version/file choices; independent streams do not promise global order.
- Managed q8p: supported retained F16 inputs can be quantized into separate retained copies from Connection's per-row actions, with bounded multicore CPU work and progress/ETA. Originals remain untouched; Prepare uses the ordinary explicit editor. CLI and optional approximate-GPU Colab experiments remain separately evidenced.
- DT+ storage: selected-key headroom, explicit deletion, Cloud only pills based on known managed files, stale-hint invalidation and Prepare recovery without losing the draft. Required missing custom LoRAs stop generation before submission if restoration cannot proceed.
- Prepare/conversion: eligible version-aware reuse, training metadata/trigger suggestions, Krea 2 mapping fixes, alpha validation, meaningful provenance, abandoned owned-staging cleanup, hashing-pause callback repair and clearer partial failures/overlay lifecycle.
- Desktop/tooling: collapsible Connection sections, larger managed library, readable narrow Logs filters, source check/test shortcuts, unified Preview/timeout diagnostics and notebook exclusions from Ruff. Automatic Actions starts remain paused for exhausted credits.

## Implemented phase map

| Area | Delivered foundations | Remaining boundary |
| --- | --- | --- |
| Backends, CLI and persistence | Fake/local/DT+, immutable resolved requests, durable queues/attempts, receipts and shared diagnostics. | Wider protocol/media compatibility and remote cancellation are not universal guarantees. |
| Qt, planning and Images | Shared forms/theme, independent drafts, queue planning, Grid/List/Detail, presets, export, snapshots, slideshow and guarded removal. | Windows/DPI, cache budgets, startup performance and platform mappings retain their scoped evidence. |
| Image operations and Workflows | Immutable inputs, local detectors, inpainting, consent-based model provisioning and resumable per-branch workflow execution. | No universal model/control support or stock ComfyUI runtime compatibility. |
| Scripts | Native library/editor/setup, durable linked child execution and bounded batch slots. | Broader samples, virtual pixels/masks and detection/guidance retain existing issue ownership. |

## Current validation and release gate

- Owner full local suite before the metadata-only bump: 700 passed, 3 skipped in 190.65 s. Owner Fast preview saved 31/31 states; Browse acceptance additionally uses native smoke/screenshots. See the audit for exact source boundaries.
- Owner accepted selected managed q8p conversion/provider workflows and improved CPU utilization; no universal quality, GPU equivalence or measured scheduler-speedup claim is made.
- 6.4.0 uses the owner-run local Windows build path because Actions credits are exhausted. No CI/build workflow is dispatched. The canonical clean merged EXE, checksum/manifest and exact-artifact owner smoke remain required before tagging/publication.

## Remaining hardening and compatibility

- Advanced scheduling/fairness, broader verified image/cloud/media operations, script sample coverage and mixer acceptance remain separately scoped.
- Logs #106 memory/byte budgets and #107 fault/native/storage/policy/DPI acceptance remain open; export admission is not full budget compliance. Platform #77, gallery #88 and startup/package #169/#170 retain existing ownership.
- Execution schema remains 9; LoRA asset registry remains 6. Back up complete state and outputs; unsupported registries are refused rather than reset. Queue templates remain version 1. Restore a pre-upgrade backup for downgrade.
- No simultaneous Windows/WSL SQLite writers, silent provider/account switch, implicit upload consent or uncertain-work replay. Manual Retry can submit a new billable request; existing bounded automatic retry semantics remain unchanged.
- Older-CPU source observations do not establish compatibility of a new packaged executable. Follow [releasing](guides/releasing.md) and [building](guides/building.md) for artifact acceptance.

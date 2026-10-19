# Current development status

**Snapshot: 2026-10-19.** Coding orientation against the [initial implementation plan](plans/initial.md), not a release declaration. Repository source and linked evidence are authoritative; implemented code, owner-reported compatibility and final packaged acceptance are distinct.

## Release and branch state

- **Published:** [DTFlow 4.0.0](https://github.com/djebaz/dtflow/releases/tag/v4.0.0).
- **In preparation:** version metadata/uv lock are 5.0.0 on `release/5.0.0`; [PR #109](https://github.com/djebaz/dtflow/pull/109) is open, not merged or published. See the [consolidated release notes](releases/v5.0.0.md) and [preparation/acceptance plan](plans/2026-10-19-release-5.0.0.md).
- **Merged feature baseline:** main includes #105 (`d846f7c`), #104 and the combined #95/#102 gallery delivery. Earlier references to those PRs, #76, #85 or #89 as pending merges are obsolete. Source availability does not certify the final 5.0.0 Windows EXE.

## Implemented scope mapped to the initial plan

| Initial-plan phase | Current source scope | Boundary / reference |
| --- | --- | --- |
| 1–3 — Connectivity, backends and CLI/services | Backend-neutral contract, fake/local Draw Things/direct DT+ adapters, generation/configuration, LoRA preflight and cloud asset receipts, durable rolling queues and uncertainty handling. | Compatibility is model/backend specific; stopping local waiting does not prove remote cancellation. [Architecture](architecture/), [CLI](guides/cli.md). |
| 4–5 — Qt shell and desktop execution | Connection/credentials, Generate, configuration/LoRA actions and queue controls; shared inline confirmations/editors/progress/outcomes, canonical Designer forms and shared theme. | UI delegates dispatch and persistence to shared services. [Desktop guide](guides/desktop.md), [DESIGN.md](../DESIGN.md). |
| 6 — Batch planning | Shared seed/Pair/Cross planning, bounded expansion and dry runs; staged Inpaint queues add a Strengths dimension. | Edit plans cannot vary image size; queues are created paused and dispatch requires Start. [Queues guide](guides/queues.md). |
| 8A–8D — Gallery, presets, reuse and cache | Saved-image Grid/List/Detail, presets, settings reuse/explicit regeneration, shared export panel and category-specific cache cleanup. Hybrid scroll/page chips, incremental preparation/mounting and signature-gated polling. | Defaults: **20 images per batch / at most 100 mounted cards**, up to 1000 records per read; 100/300 is a historical trial/stress fixture, not the launch default. Thumbnails: 600 px, JPEG 85 default, optional WebP 80, WebP for alpha. [Gallery delivery and limits](plans/2026-10-17-images-hybrid-pagination.md). |
| 8E — Selection and removal | Checked/all-matching scope, bulk export, explicit Trash/permanent Delete, fresh irreversible consent and per-file durable outcomes, including authenticated edit raw/final files. | No permanent-delete fallback or replay of uncertain outcomes. Cross-runtime aliases do not alone authorize mutations. [Removal plan/evidence](plans/2026-10-17-image-removal-modes.md), [recycling architecture](architecture/image-recycling.md). |
| 9 — Image operations, bounded delivered slice | Headless normalization/fitting and immutable input snapshots; Inpaint Paint/Select editor, local ONNX person/part selection, explicit edit dispatch and queued edits through shared services. Source/mode/settings are remembered, masks/history are not. | Dedicated owner-probe/permit boundaries remain; generic CLI/config generation is txt2img-only, and no universal checkpoint compatibility is claimed. [Inpaint plan](plans/2026-10-10-smart-mask-inpainting.md), [edit queues](plans/2026-10-10-inpaint-queues.md), [desktop guide](guides/desktop.md). |

## Delivered additions and cross-cutting behavior

- **Diagnostics and Logs (#105):** one rotating JSONL stream with typed producers and loss/closure accounting; shared Qt-free reader/query/export and migrated benchmark consumers. Native status-bar Logs has whole-session filters/sorting, 500-row bidirectional pages, Follow with surviving selection, details/copy/export/raw-file/folder actions and active log-directory override. Timing-chip rounding does not change raw precision. Historical text/CSV remains untouched; new sessions do not create automatic gallery CSV. [Delivery audit, source ownership and evidence](plans/2026-10-19-structured-log-viewer.md).
- **Windows benchmark tooling (#104/#105):** real UIA profile smoke and Grid `-ml/-mc` matrices, visible-position start/arrival verification, continuous down/back wheel journeys and warning/incomplete-trial exclusions. Single-run rankings are provisional; tooling does not establish an optimal production default or zero-stall browsing. [Benchmark plan](plans/2026-10-18-windows-gallery-benchmarks.md).
- **Windows/WSL1 (#73/#75/#76/#85):** guarded original-file leases/helper, verified read aliases, per-runtime export folders, saved-request reuse, model-readability checks and asynchronous desktop handoff. Browsing snapshots expose committed metadata during live-store refusal but never authorize export/edit/removal. [Snapshot architecture/evidence](architecture/gallery-snapshot.md), [WSL follow-ups](plans/2026-10-17-wsl-followups.md).
- **Developer support:** uv-managed agent setup, deterministic offline image fixtures, expanded named Qt preview states, Windows offscreen fonts/config fixes and parallel pytest. Preview definitions/screenshots remain layout evidence, not native interaction/DPI acceptance. [Development guide](guides/development.md), [testing guide](guides/testing.md).

## Remaining implementation and hardening

| Scope | Still open |
| --- | --- |
| 7 — Advanced scheduling | Priorities, fairness and adaptive concurrency beyond existing rolling dispatch. |
| 9 — Image operations beyond the delivered slice | Broader independently verified checkpoint/backend support and remaining smart-mask/editor acceptance; additional controls/references are not implied by current Inpaint delivery. |
| 10 — Workflows | Versioned workflow execution/compiler and visual authoring. |
| 11 — Broader DT+ | Additional independently verified cloud capabilities/hardening; existing auth, secret safety and ambiguous-outcome protection are already implemented, not deferred work. |
| Logs memory/export | [#106](https://github.com/djebaz/dtflow/issues/106): end-to-end measured byte budgets (including selection, hydration and exports) and lazy-workspace evaluation. PR #111 adds export cancellation and a conservative bounded-failure admission policy for export candidates; this does not establish full 16/32 MiB acceptance. |
| Diagnostics acceptance | [#107](https://github.com/djebaz/dtflow/issues/107): fault/native replay, schema/projection/benchmark/storage/policy evidence and strict lint/format gating. Original all-gates-before-merge scheduling was superseded by explicit follow-ups, not by claiming those checks passed. |
| Logs presentation | [#108](https://github.com/djebaz/dtflow/issues/108): source now paints independent COMPONENT/EVENT header sort targets, counts matching arrivals for Refresh with incomplete evidence disclosed, shows a separate empty-state panel and collapses details on an outside click. Native Windows rendering and interaction acceptance remain open. |
| Native platform and previews | [#77](https://github.com/djebaz/dtflow/issues/77): safe WSL2 Windows-drive original access. [#88](https://github.com/djebaz/dtflow/issues/88): remaining offline preview coverage/Detail stall investigation. |

## Evidence and operational limits

- The owner [confirms ONNX selection compatibility on older CPUs](https://github.com/djebaz/dtflow/pull/109#issuecomment-6265197891). The withdrawn unverified-CPU warning must not be repeated. Model-quality claims, standalone PoC cost measurements and acceptance of the exact 5.0.0 executable remain separate.
- Prior CI and owner source-run smoke observations belong to their recorded commits/configurations; follow the linked delivery plans for details. Do not reuse old test counts or skipped render jobs as evidence for the current release head. Ruff currently reports non-blocking warnings, so inspect actual step outcomes rather than green conclusions alone.
- Do not run simultaneous Windows/WSL SQLite state writers. Unverified WSL2 Windows-drive mounts refuse original-touching operations; paths/outputs are not automatically migrated. Snapshots are browsing-only. A remote-successful generation may still fail local saving with absent/partial files; repeating generation may consume credits. [Cross-runtime guidance](guides/desktop.md#windows-history-viewed-in-wsl-1).
- Cache eviction is entry-count based, not a byte budget; queued thumbnail writes can republish after removal cleanup, and oversized legacy flat caches have separate cleanup limits. Remaining native stalls/startup latency are not fixed merely by changing benchmark configuration. [Current gallery caveats](plans/2026-10-17-images-hybrid-pagination.md).
- Final release-head checks and owner acceptance of the exact clean Windows executable are pending under the [release guide](guides/releasing.md). This documentation refresh executes no tests, lint/format/syntax/import checks, renders, builds, inference, provider or billable actions.

Historical implementation checkpoints are retained in Git, versioned release notes and the linked plans/research. They are intentionally not duplicated here as competing descriptions of current behavior.

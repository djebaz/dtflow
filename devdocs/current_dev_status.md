# Current development status

Snapshot: 2026-10-06. PR #34 implements the Phase 8A saved-output viewer on top of PR #33's merged Images presentation. The 0.3.0 publication evidence below is retained from the earlier snapshot; PR #31 build/draft workflow status is not re-verified here.
This is coding orientation against [initial.md](plans/initial.md), not a release declaration. Implementation scope comes from source/plan inspection; owner-reported tests and interactive smoke observations are recorded separately. The agent ran no live generation or provider operations.

## Implemented on main

| Initial-plan phase | Implemented scope |
| --- | --- |
| 1 — Connectivity proof | Diagnostic gRPC/direct-cloud feasibility probes and recorded evidence. |
| 2 — Backends | Backend-neutral contract, fake backend, local Draw Things and direct DT+ adapters; packaging investigations. |
| 3 — CLI/services | Generation/configuration, LoRA preflight, cloud LoRA prepare/reuse with durable receipts, durable rolling queues. |
| 4 — Qt shell | Connection selection/status and Windows saved API-key service. |
| 5 — Desktop execution | Generate, LoRA/cloud asset actions, configuration save/load and queue controls through shared services. |
| 6 — Batch planning | Shared seed/pair/cross planner, bounded expansion and CLI/desktop dry runs. |

Implemented does not mean universal compatibility or remote cancellation. On 2026-10-05 the owner reported the other 0.3.0 release checks complete, identifying the exact portable Qt executable smoke test as the remaining gate; this is owner-reported, not newly assistant-verified. Keep existing detailed source/live evidence rather than rerunning it without need. PR #29 changes the builder to a GUI-first windowed launcher; the owner has now built and exercised its dirty 0.1.0 artifact, including key/form persistence and bounded local/cloud generation/queues. See [the identified GUI smoke](research/windows-gui-smoke-2026-10-05.md) for artifact identity, one unresolved cloud outcome and coverage limits. The owner subsequently built/launched the clean 0.3.0 artifact and confirmed its displayed identity; the agent verified bundled metadata/checksum and downloaded release assets. [0.3.0 is published](https://github.com/djebaz/dtflow/releases/tag/v0.3.0); earlier live coverage/limitations remain separate. See [the build guide](guides/building.md), [0.3.0 notes](releases/v0.3.0.md) and [packaging evidence](research/windows-packaging.md).

## Not yet implemented

| Phase | Remaining scope |
| --- | --- |
| 7 — Advanced scheduling | Priorities, fairness and adaptive concurrency; not a prerequisite for the proposed release milestones. Basic rolling dispatch already exists. |
| 8 — Gallery/presets | Phase 8A saved-output viewing is implemented in PR #34; 8B presets, 8C reuse/regeneration, 8D cache management and separately scoped 8E organisation remain. Inventory cache and last-session restore already exist; configuration save/load is not a preset library. |
| 9 — Image operations | 9A img2img, 9B existing mask-file inpainting, 9C interactive masks and selected controls/references. |
| 10 — Workflows | 10A versioned workflow files/compiler/execution; 10B visual editor and expanded semantics. |
| 11 — Broader DT+ | Additional verified cloud capabilities and hardening. Token handling, secret safety, subscription-only policy and ambiguous-outcome protection already belong to Phases 2/3; do not rebuild or defer them. |

## Added/refined scope

- Shared persistent Connection/Generate/Queues shell, image-first fit/zoom/pan preview, centralized design tokens and monogram: PRs #25/#26.
- Offline simulation, per-provider form memory, desktop random seed 0 and output/Inspector/concurrency refinements: PR #27, already merged (not an open draft).
- PR #28 added usage guides, a GUI-first README and this short coding snapshot.
- PR #29 adds the desktop-first stamped builder and no-console startup/log diagnostics, with source CLI and generation policies unchanged.

- PR #31 adds an opt-in cached Windows build and draft-only release workflow, including artifact-only same-repository PR-head builds; not yet runner-verified. Owner executable smoke/publication remain explicit.

- PR #33 established the offline Grid/List/Detail presentation and shared Images styling; its fixture launcher remains available for visual development. See [the preview plan](plans/2026-10-05-phase-8a-ui-preview.md).
- PR #34 connects those views to committed saved outputs through shared read-only services and CLI commands. It adds bounded query/paging, asynchronous thumbnails, positive-prompt/model/LoRA/date/dimensions/aspect filters, deterministic sorting, durable viewer state, anchored auto-refresh, real provenance/raw metadata, file actions and missing/corrupt-file handling. The thumbnail control is 25–250% with 100% as the standard size; first-run Images opens Grid and Detail defaults to Fit. The owner reports the full test suite passed after the Qt lifecycle fix and performed interactive smoke testing that drove thumbnail-layout refinements. Packaged/native-DPI acceptance remains separate. See [the viewer plan](plans/2026-10-05-phase-8a-viewer.md).

## Next coding direction

PR #37 refines Phase 8A refresh: identity-based Grid/List/filmstrip reconciliation
preserves retained thumbnails/in-flight work and the selected Detail image state
when outputs are added, removed or reordered. See the
[follow-up plan](plans/2026-10-06-images-incremental-refresh.md). Code presence is
not visual acceptance; tests/smoke are deferred to the owner under fast-implement.

The [release roadmap](plans/releases.md) sequences Phase 8 daily-use refinement (proposed 0.4.0), then 9A img2img (0.5.0), then 10A workflows (0.6.0). These are planned milestones, not shipped versions. Future executable acceptance remains separate from the next feature to code; follow [the release guide](guides/releasing.md). Follow the [Phase 8 delivery plan](plans/2026-10-05-phase-8-sequencing.md): with 8A now implemented in PR #34, the next planned capability is Draw Things-compatible presets (8B), followed by reuse, cache management and separately scoped organisation. Recheck the roadmap and relevant phase plan before choosing work.

## Maintenance

Update this short snapshot when implementation scope changes, with the reviewed base revision/date and evidence links. Keep completed, remaining and added scope aligned with `initial.md`; retain detailed validation in plans/research/release notes. If this snapshot disagrees with source or verified evidence, those sources win: refresh the snapshot rather than assuming completion. Do not append a running changelog here.

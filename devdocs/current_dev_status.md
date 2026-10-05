# Current development status

Snapshot: 2026-10-05, main baseline `c0d4bd3` (PR #28 merged), plus the GUI-build work in PR #29.
This is coding orientation against [initial.md](plans/initial.md), not a release declaration. Implementation scope comes from source/plan inspection; the bounded owner-run Windows evidence below is recorded separately. The agent ran no live operations.

## Implemented on main

| Initial-plan phase | Implemented scope |
| --- | --- |
| 1 — Connectivity proof | Diagnostic gRPC/direct-cloud feasibility probes and recorded evidence. |
| 2 — Backends | Backend-neutral contract, fake backend, local Draw Things and direct DT+ adapters; packaging investigations. |
| 3 — CLI/services | Generation/configuration, LoRA preflight, cloud LoRA prepare/reuse with durable receipts, durable rolling queues. |
| 4 — Qt shell | Connection selection/status and Windows saved API-key service. |
| 5 — Desktop execution | Generate, LoRA/cloud asset actions, configuration save/load and queue controls through shared services. |
| 6 — Batch planning | Shared seed/pair/cross planner, bounded expansion and CLI/desktop dry runs. |

Implemented does not mean universal compatibility or remote cancellation. On 2026-10-05 the owner reported the other 0.3.0 release checks complete, identifying the exact portable Qt executable smoke test as the remaining gate; this is owner-reported, not newly assistant-verified. Keep existing detailed source/live evidence rather than rerunning it without need. PR #29 changes the builder to a GUI-first windowed launcher; the owner has now built and exercised its dirty 0.1.0 artifact, including key/form persistence and bounded local/cloud generation/queues. See [the identified GUI smoke](research/windows-gui-smoke-2026-10-05.md) for artifact identity, one unresolved cloud outcome and coverage limits. Final clean 0.3.0 artifact acceptance remains separate. See [the build guide](guides/building.md), [unreleased notes](releases/unreleased.md) and [packaging evidence](research/windows-packaging.md).

## Not yet implemented

| Phase | Remaining scope |
| --- | --- |
| 7 — Advanced scheduling | Priorities, fairness and adaptive concurrency; not a prerequisite for the proposed release milestones. Basic rolling dispatch already exists. |
| 8 — Gallery/presets | Output gallery, preset library, regeneration/reuse flows, cache measurement and purge. Inventory cache and last-session restore already exist; configuration save/load is not a preset library. |
| 9 — Image operations | 9A img2img, 9B existing mask-file inpainting, 9C interactive masks and selected controls/references. |
| 10 — Workflows | 10A versioned workflow files/compiler/execution; 10B visual editor and expanded semantics. |
| 11 — Broader DT+ | Additional verified cloud capabilities and hardening. Token handling, secret safety, subscription-only policy and ambiguous-outcome protection already belong to Phases 2/3; do not rebuild or defer them. |

## Added/refined scope

- Shared persistent Connection/Generate/Queues shell, image-first fit/zoom/pan preview, centralized design tokens and monogram: PRs #25/#26.
- Offline simulation, per-provider form memory, desktop random seed 0 and output/Inspector/concurrency refinements: PR #27, already merged (not an open draft).
- PR #28 added usage guides, a GUI-first README and this short coding snapshot.
- PR #29 adds the desktop-first stamped builder and no-console startup/log diagnostics, with source CLI and generation policies unchanged.

## Next coding direction

The [release roadmap](plans/releases.md) sequences Phase 8 daily-use refinement (proposed 0.4.0), then 9A img2img (0.5.0), then 10A workflows (0.6.0). These are planned milestones, not shipped versions. Outstanding 0.2.0/0.3.0 acceptance and state-migration gates remain separate from the next feature to code. Recheck the roadmap and relevant phase plan before choosing work.

## Maintenance

Update this short snapshot when implementation scope changes, with the reviewed base revision/date and evidence links. Keep completed, remaining and added scope aligned with `initial.md`; retain detailed validation in plans/research/release notes. If this snapshot disagrees with source or verified evidence, those sources win: refresh the snapshot rather than assuming completion. Do not append a running changelog here.

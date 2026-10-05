# Current development status

Snapshot: 2026-10-05, compared with `main` at `ada7dfe` (PR #27 merged).
This is coding orientation against [initial.md](plans/initial.md), not release acceptance or a test report. Source/plan inspection only for this snapshot; no tests or live operations were run to establish it.

## Implemented on main

| Initial-plan phase | Implemented scope |
| --- | --- |
| 1 — Connectivity proof | Diagnostic gRPC/direct-cloud feasibility probes and recorded evidence. |
| 2 — Backends | Backend-neutral contract, fake backend, local Draw Things and direct DT+ adapters; packaging investigations. |
| 3 — CLI/services | Generation/configuration, LoRA preflight, cloud LoRA prepare/reuse with durable receipts, durable rolling queues. |
| 4 — Qt shell | Connection selection/status and Windows saved API-key service. |
| 5 — Desktop execution | Generate, LoRA/cloud asset actions, configuration save/load and queue controls through shared services. |
| 6 — Batch planning | Shared seed/pair/cross planner, bounded expansion and CLI/desktop dry runs. |

Implemented does not mean all phase acceptance gates are closed. The plans and evidence still distinguish offline fixtures, owner-reported live workloads and target-Windows/packaged acceptance. Broad model/LoRA compatibility, remote cancellation, charging/refunds, live queue/upload and desktop acceptance must not be inferred from code presence. A stamped Windows build's identity was owner-verified at an earlier revision; that is not current-head desktop acceptance. See [unreleased notes](releases/unreleased.md), [gRPC evidence](research/drawthings-grpc-proof.md) and [packaging evidence](research/windows-packaging.md).

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
- This documentation PR adds usage guides and a GUI-first README; it changes no application behavior.

## Next coding direction

The [release roadmap](plans/releases.md) sequences Phase 8 daily-use refinement (proposed 0.4.0), then 9A img2img (0.5.0), then 10A workflows (0.6.0). These are planned milestones, not shipped versions. Outstanding 0.2.0/0.3.0 acceptance and state-migration gates remain separate from the next feature to code. Recheck the roadmap and relevant phase plan before choosing work.

## Maintenance

Update this short snapshot when implementation scope changes, with the reviewed base revision/date and evidence links. Keep completed, remaining and added scope aligned with `initial.md`; retain detailed validation in plans/research/release notes. If this snapshot disagrees with source or verified evidence, those sources win: refresh the snapshot rather than assuming completion. Do not append a running changelog here.

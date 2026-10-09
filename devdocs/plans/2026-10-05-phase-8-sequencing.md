# Phase 8 — Delivery sequencing

**Date:** 2026-10-05

**Status:** Phase 8A is implemented. PR #46 implements 8B presets, 8C reuse/regeneration and 8D disposable-cache management for the proposed 0.4.0 gate; 8D source/runtime scope was owner-accepted on 2026-10-09 after green CI and smoke (see the [8D completion ledger](2026-10-09-phase-8d-cache-completion.md)); other phase/packaged gates remain separate.

**Target:** Proposed 0.4.0 daily-use refinement, subject to the [release roadmap](releases.md).

**Engineering source:** [Initial Phase 8](initial.md#phase-8--gallery-and-presets).

## Ordered phases

These are delivery phases within initial-plan Phase 8. “Phase 1” here means 8A viewer, not the completed initial connectivity phase.

| Order | Phase | Outcome and scope | Acceptance / handoff |
| --- | --- | --- | --- |
| 1 | **8A — Viewer** | **Implemented in PR #34:** read-only saved-output browsing, selected Grid/List/Detail layouts, query/filter/sort, durable browsing state and recorded metadata inspection. See the [dedicated viewer plan](2026-10-05-phase-8a-viewer.md). | `8-O2` and browsing-only `8-O5`; owner reports the full tests passed after lifecycle fixes; packaged/native-DPI acceptance remains separate. |
| 2 | **8B — Presets** | Reusable named settings with native Draw Things `custom_configs.json` interchange; preserve unsupported fields and distinguish them from applied settings. See the [preset contract and example](2026-10-05-phase-8-gallery-presets.md#8b--draw-things-compatible-presets). Detailed library actions remain to be refined. | `8-O1` and necessary `8-O5`; verify enum mappings and upstream round trips before claiming interoperability. Loading never dispatches. |
| 3 | **8C — Reuse/regeneration** | Populate Generate from recorded settings; explicitly create a fresh request/attempt for regeneration with source lineage, preserving originals. Exact actions remain to be refined. | `8-O3` and necessary `8-O5`; preserve existing preflight, dispatch and no-replay safety. |
| 4 | **8D — Cache management** | Measure and confirm purge of disposable categories, including gallery derivatives, while retaining outputs and all durable state. | `8-O4`; coordinate with active readers/writers and respect no-cache mode. |
| 5 | **8E — Organisation** | Owner selected planned Images multi-selection and bulk deletion in the [8E image-management plan](2026-10-09-phase-8e-image-management.md). Favourites, ratings, tags, notes and collections remain separate candidates. | Not implemented or automatically required for 0.4.0; confirmed Recycle Bin-only actions and Images + Connection checked-image export are owner-approved; follow the [implementation task list](2026-10-09-phase-8e-implementation.md), with durable recovery/eligibility audit before destructive work. |

## Delivery rules

- Implement in the order above, using small coherent PR slices within a phase when useful. Do not omit selected viewer capabilities merely to call a first slice complete.
- Keep UI, shared services, persistence, workers and provider protocols separated. Gallery/preset work does not introduce a second generation engine.
- Start 8A with bounded/progressive queries, asynchronous derived-image work and cache/durable-state separation; do not defer those foundations until 8D's cleanup controls.
- Preserve existing generation configuration save/load. Native preset interchange is implemented by PR #46 within the conservative field mapping documented in the desktop guide; interoperability with Draw Things itself still awaits owner round-trip verification.
- Preserve outputs, attempts, queues, credentials, model assets, presets, viewer preferences and later organisation during disposable-cache cleanup.
- The sequence does not approve a UI layout or all later-phase feature candidates. Refine each phase's unresolved semantics before implementation and follow `DESIGN.md` when desktop design starts.

## Gates and documentation

1. Agree the relevant phase's bounded scope and unresolved contracts before coding.
2. Deliver shared-service semantics first, then focused Qt wiring; test new behaviour at one authoritative layer.
3. Validate locally using saved-output/configuration fixtures. No live generation is required solely for gallery/presets; any genuinely necessary live composition check is separately authorized.
4. Record owner Windows interaction acceptance separately from agent checks. Final portable-executable smoke remains a release gate, not proof inferred from source tests.
5. Update the current status, relevant architecture and unreleased notes when functionality lands; plans alone do not mark phases implemented.
6. Reconcile actual shipped scope with the 0.4.0 roadmap before releasing. Organisation must not silently expand that release gate.

## Supporting documents

- [8A viewer scope and implementation checkpoints](2026-10-05-phase-8a-viewer.md).
- [Phase 8 supporting contracts, native preset sample and later-phase constraints](2026-10-05-phase-8-gallery-presets.md).
- [Runtime state/cache ownership](../architecture/runtime-layout.md).

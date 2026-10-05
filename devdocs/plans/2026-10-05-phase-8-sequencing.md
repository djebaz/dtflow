# Phase 8 — Delivery sequencing

**Date:** 2026-10-05

**Status:** Planned sequence; no implementation or UI design started.

**Target:** Proposed 0.4.0 daily-use refinement, subject to the [release roadmap](releases.md).

**Engineering source:** [Initial Phase 8](initial.md#phase-8--gallery-and-presets).

## Ordered phases

These are delivery phases within initial-plan Phase 8. “Phase 1” here means 8A viewer, not the completed initial connectivity phase.

| Order | Phase | Outcome and scope | Acceptance / handoff |
| --- | --- | --- | --- |
| 1 | **8A — Viewer** | Read-only saved-output browsing, all selected layouts/search/filter/sort/viewing/metadata capabilities. See the [dedicated viewer plan](2026-10-05-phase-8a-viewer.md). | `8-O2` and browsing-only `8-O5`; establish bounded queries and disposable derived assets before presets. |
| 2 | **8B — Presets** | Reusable named settings with native Draw Things `custom_configs.json` interchange; preserve unsupported fields and distinguish them from applied settings. See the [preset contract and example](2026-10-05-phase-8-gallery-presets.md#8b--draw-things-compatible-presets). Detailed library actions remain to be refined. | `8-O1` and necessary `8-O5`; verify enum mappings and upstream round trips before claiming interoperability. Loading never dispatches. |
| 3 | **8C — Reuse/regeneration** | Populate Generate from recorded settings; explicitly create a fresh request/attempt for regeneration with source lineage, preserving originals. Exact actions remain to be refined. | `8-O3` and necessary `8-O5`; preserve existing preflight, dispatch and no-replay safety. |
| 4 | **8D — Cache management** | Measure and confirm purge of disposable categories, including gallery derivatives, while retaining outputs and all durable state. | `8-O4`; coordinate with active readers/writers and respect no-cache mode. |
| 5 | **8E — Organisation** | Separately choose favourites, ratings, tags, notes, collections and any bulk actions. These are candidates, not an approved full feature set. | Define acceptance after scope selection. Not automatically required for 0.4.0; destructive actions require a separate decision. |

## Delivery rules

- Implement in the order above, using small coherent PR slices within a phase when useful. Do not omit selected viewer capabilities merely to call a first slice complete.
- Keep UI, shared services, persistence, workers and provider protocols separated. Gallery/preset work does not introduce a second generation engine.
- Start 8A with bounded/progressive queries, asynchronous derived-image work and cache/durable-state separation; do not defer those foundations until 8D's cleanup controls.
- Preserve existing generation configuration save/load. Native preset compatibility is new planned work, not current behaviour.
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

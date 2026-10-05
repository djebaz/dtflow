# Phase 8A — Saved-output viewer

**Date:** 2026-10-05

**Status:** Selected scope recorded; implementation and UI design not started.

**Position:** First delivery phase within Phase 8, not initial-plan Phase 1.

**Parent:** [Phase sequencing](2026-10-05-phase-8-sequencing.md).

**Acceptance:** `8-O2` and browsing-only `8-O5` from [the initial plan](initial.md#phase-8--gallery-and-presets).

## Purpose

Make saved outputs easy to browse, find and inspect without generating, modifying or deleting them.
This plan preserves all selected viewer features; implementation slices are checkpoints, not a reduced feature list.
Screen layouts, mockups and widget choices are deliberately deferred. Follow [DESIGN.md](../../DESIGN.md) when design begins.

## Approved viewer scope

### Selected capabilities

| Area | Selected scope |
| --- | --- |
| Browsing | **A1** uniform thumbnail grid; **A2** masonry preserving proportions; **A3** compact list with metadata columns; **A4** filmstrip beside a large selected-image preview; **A5** adjustable thumbnail size/density; **A6** keyboard navigation/selection; **A7** remember scroll position, selection and view settings; **A8** refresh when new outputs are saved; **A9** pagination or progressively loaded scrolling. |
| Search/filter | **B1** positive prompts; **B2** negative prompts; **B3** model; **B5** LoRA; **B6** date/date range; **B7** dimensions/aspect ratio. |
| Sorting | **C1** newest/oldest first; **C2** model, size and LoRAs. Define size and multi-LoRA ordering before implementation. |
| Viewing | **D1** large selected-image preview; **D2** fullscreen; **D3** fit/zoom/pan; **D4** true 100% pixel viewing; **D5** previous/next through filtered results; **D7** external image viewer; **D10** dimensions and file size beside the image. |
| Metadata | **E1** positive/negative prompts; **E2** actual resolved seed; **E3** model/provider identifiers; **E4** LoRAs, weights and recorded compatibility evidence; **E5** dimensions, steps, guidance and sampler; **E7** generation date and recorded timings; **E11** raw metadata JSON inspection. |

### Constraints and acceptance

- Browse saved final outputs and their actual metadata. Preserve distinctions between final images, previews and diagnostics.
- Display missing/unrecorded metadata honestly; do not invent timings, resolved seeds or compatibility evidence.
- Build shared query services with stable result ordering, bounded/progressive loading and asynchronous thumbnail work; do not decode an entire library into memory or block Qt.
- Keep derived thumbnails/indexes rebuildable and separate from durable output metadata from the start, even though cleanup controls arrive in 8D.
- Handle missing/corrupt images and metadata without damaging original files or preventing browsing other outputs.
- Keep presets, reuse, regeneration, deletion and organisation actions out of this first viewer phase.
- Validate query/provenance at the service layer (`8-O2`) and focused browsing wiring (`8-O5`), followed by owner Windows interaction checks. No live generation is needed.

## Ordered implementation checkpoints

1. **Inspect existing output/provenance contracts.** Identify saved final artifacts, recorded request metadata and available timestamps. Reuse existing persistence and preview paths where appropriate; do not treat logs as output truth.
2. **Add the shared gallery query foundation.** Define stable artifact identity, ordering, search/filter composition and bounded loading. Establish missing/corrupt-file handling and rebuildable thumbnail/index ownership.
3. **Deliver browsing modes and state.** Implement the selected grid, masonry, compact list and filmstrip capabilities, density, keyboard navigation and remembered view/selection/position. Refresh saved-output results without unnecessarily resetting the user's position.
4. **Deliver search/filter/sort.** Apply the selected prompt, model, LoRA, date and geometry queries through shared services. Keep result order deterministic and selection/previous-next navigation consistent with filtered results.
5. **Deliver image inspection.** Add selected-image/fullscreen viewing, fit/zoom/pan, true 100% viewing, external viewer action and dimensions/file size. Reuse shared image-viewing behaviour rather than fork it unnecessarily.
6. **Deliver metadata inspection.** Show the selected recorded provenance and raw JSON. Represent unavailable fields explicitly; preserve original metadata.
7. **Validate and record acceptance.** Test query/provenance semantics at the authoritative service layer and only necessary Qt composition. Use local fixture libraries including missing/corrupt artifacts, mixed dimensions and incomplete metadata. Record owner Windows keyboard, view-state, fullscreen/100%, refresh and external-viewer checks separately.

## Decisions to resolve before relevant implementation

- Whether progressive scrolling or pagination satisfies A9, and which loading limits apply.
- What “size” means for sorting (pixel area, dimensions or file bytes), and deterministic ordering for multiple LoRAs.
- Prompt search matching/case rules; combined filter semantics; date timezone/boundaries and aspect-ratio matching.
- How selection and scroll anchors behave after filtering, sorting, refresh or disappearance of an image.
- Durable ownership/versioning of viewer preferences versus disposable thumbnail/index data.
- Initial discovery source and scope of saved outputs; arbitrary-folder importing is not implicitly selected.

Resolve these in the relevant implementation/design PR, rather than inventing them as already approved behaviour.

## Completion boundary

All selected viewer capabilities above are delivered and locally validated, with bounded loading and no blocked event loop.
Saved originals and durable state remain unchanged by browsing; disposable cache writes and durable viewer preferences are allowed.
No presets, reuse/regeneration, organisation, destructive file actions or cache-management controls are introduced by 8A.
No live inference, training or potentially billable action is needed for viewer acceptance.
Then proceed to [8B preset interchange](2026-10-05-phase-8-gallery-presets.md#8b--draw-things-compatible-presets).

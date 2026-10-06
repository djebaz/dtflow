# Phase 8A — Saved-output viewer

**Date:** 2026-10-05

**Status:** Implemented in PR #34 on top of PR #33's Grid/List/Detail presentation; pending merge. Shared saved-output query/inspection services, CLI access, Qt browsing, durable viewer preferences and asynchronous loading are present. The [preview plan](2026-10-05-phase-8a-ui-preview.md) remains the historical UI-presentation record.

**Position:** First delivery phase within Phase 8, not initial-plan Phase 1.

**Parent:** [Phase sequencing](2026-10-05-phase-8-sequencing.md).

**Acceptance:** `8-O2` and browsing-only `8-O5` from [the initial plan](initial.md#phase-8--gallery-and-presets).

## Purpose

Make saved outputs easy to browse, find and inspect without generating, modifying or deleting them.
This plan preserves all selected viewer features; implementation slices are checkpoints, not a reduced feature list.
Owner Grid, List and Detail mockups define the current layout direction. The [text wireframes](examples/phase-8a-viewer-wireframes.txt) record the latest compact header, navigation and metadata-panel discussion, distinguishing agreed choices from review proposals. Masonry is explicitly removed from scope. Detail contains the right-hand filmstrip; widget choices and unresolved interactions remain open. Follow [DESIGN.md](../../DESIGN.md) for desktop implementation.

## Approved viewer scope

### Selected capabilities

| Area | Selected scope |
| --- | --- |
| Browsing | **A1** uniform thumbnail grid; **A3** compact list with metadata columns; **A4** Detail view with a right-hand filmstrip beside a large selected-image preview; **A5** adjustable thumbnail size/density; **A6** keyboard navigation/selection; **A7** remember scroll position, selection and view settings; **A8** refresh when new outputs are saved; **A9** pagination or progressively loaded scrolling. |
| Search/filter | **B1** positive prompts; **B2** negative prompts through the shared query/CLI service (not exposed in the compact desktop header); **B3** model; **B5** LoRA; **B6** date/date range; **B7** dimensions/aspect ratio. |
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

## Current view-template decisions

- **Modes:** Grid · List · Detail. A2 Masonry is withdrawn; the original feature IDs remain unchanged for traceability.
- **Shared header:** two compact rows; no title/subtitle or filename/tag/note search. View buttons, 25–250% thumbnail-scale control, auto-refresh and refresh sit above a compact positive-prompt search plus model, LoRA, date, exact-dimensions, aspect-ratio and sort controls. Negative-prompt search remains supported by the shared query/CLI service but is intentionally absent from the visible desktop header. With no valid viewer preference, Images opens Grid.
- **Grid activation:** hover outline and distinct keyboard focus; click or Enter opens that image in Detail. Return restores Grid scroll position and originating thumbnail focus. No persistent Grid selection or per-thumbnail action buttons.
- **Detail geometry:** large near-square canvas at the reference window size; right-hand filmstrip extending to the workspace bottom beside the image and its metadata/prompt/inline panels. Overlays have a 10px interior inset. Keep this arrangement stable across image proportions; no automatic portrait-specific sidebar relocation.
- **Image overlays:** position in filtered results and source dimensions. Detail defaults to Fit so the complete image is visible; Cover remains available to fill the canvas proportionally with cropping. Keep pan/zoom, true 100% and fullscreen available. Near-square canvas does not mean square images.
- **Visible metadata band:** model/checkpoint and LoRAs with weights/recorded compatibility first, followed by steps, guidance, sampler and generation date/time. Compact, with wrapping or bounded scrolling for longer content.
- **Prompt band:** positive prompt visible and wrapping, without a redundant visible caption; retain an explicit accessible name. Buttons for Additional details and Raw metadata JSON alongside it replace the earlier two permanently stacked accordion rows.
- **Additional details:** negative prompt, filename, file size, actual resolved seed, provider, recorded duration, image path and copy/open-folder actions; closed until requested.
- **Raw inspection:** full safe metadata JSON, request/attempt IDs, DTFlow version/commit, detailed recorded timings and resolution/preflight provenance; closed until requested. Never expose credentials or tokens.
- **Reference:** latest owner-approved `DTFlow Portrait Gallery Viewer_proc.png`; see the linked text wireframes for the schematic. Desktop mockup paths are discussion references, not repository assets.

The additional/raw reference mockups resolve the buttons to mutually exclusive inline sections beneath the prompt. The preview starts in Cover with Fit available, uses horizontal header/metadata scrolling and omits the sample-count pill and redundant visible search/filter labels. These presentation choices do not resolve saved-output loading or query policy. Long-content limits and smaller-window acceptance still require review. A draggable preview/metadata divider was a suggestion, not an approved requirement. Sorting and dimension filtering remain in scope despite their placement being unresolved.

## Ordered implementation checkpoints

1. **Inspect existing output/provenance contracts.** Identify saved final artifacts, recorded request metadata and available timestamps. Reuse existing persistence and preview paths where appropriate; do not treat logs as output truth.
2. **Add the shared gallery query foundation.** Define stable artifact identity, ordering, search/filter composition and bounded loading. Establish missing/corrupt-file handling and rebuildable thumbnail/index ownership.
3. **Deliver browsing modes and state.** Implement the selected Grid, compact List and Detail-with-filmstrip capabilities, density, keyboard navigation and remembered view/selection/position. Refresh saved-output results without unnecessarily resetting the user's position.
4. **Deliver search/filter/sort.** Apply the selected prompt, model, LoRA, date and geometry queries through shared services. Keep result order deterministic and selection/previous-next navigation consistent with filtered results.
5. **Deliver image inspection.** Add selected-image/fullscreen viewing, fit/zoom/pan, true 100% viewing, external viewer action and dimensions/file size. Reuse shared image-viewing behaviour rather than fork it unnecessarily.
6. **Deliver metadata inspection.** Show the selected recorded provenance and raw JSON. Represent unavailable fields explicitly; preserve original metadata.
7. **Validate and record acceptance.** Test query/provenance semantics at the authoritative service layer and only necessary Qt composition. Use local fixture libraries including missing/corrupt artifacts, mixed dimensions and incomplete metadata. Record owner Windows keyboard, view-state, fullscreen/100%, refresh and external-viewer checks separately.

## Implementation decisions resolved in PR #34

- **Loading:** Qt uses 80-output pages; the shared service hard limit is 200. Grid/List page transitions are bounded and image/thumbnail decoding runs off the GUI thread with six concurrent thumbnail decodes.
- **Thumbnail scale:** the user-facing control is 25–250%; 100% maps to the established 160 logical-pixel standard. Grid, List and Detail filmstrip follow the same percentage.
- **Sorting:** `size` means pixel area with deterministic dimension/tie ordering. Multi-LoRA sort uses a canonical identifier/weight sequence.
- **Search/filter:** prompt matching is case-insensitive literal substring; filters compose with AND. Desktop exposes positive prompt only, while shared service/CLI also supports negative-prompt search.
- **Dates/ratios:** desktop dates use local calendar boundaries normalized to UTC with date-specific DST rules; CLI accepts UTC dates or offset-aware timestamps. Ratios support orientation or exact W:H.
- **Refresh/state:** first-run auto-refresh is on. Detail anchors the selected output; Grid/List anchor the visible output. Viewer preferences are versioned durable state in `viewer.json`; derived thumbnail data remains disposable.
- **Incremental refresh (PR #37):** keep mounted Grid cards, List cells and filmstrip entries by stable output identity through additions/removals/reordering. Update changed metadata in place; retain thumbnails/in-flight decodes unless image path/size/availability changes. Retain the selected Detail pixels and zoom/pan when other outputs change, using separate image/metadata callback revisions. Identical visible pages remain a rendering no-op. See the [initial refresh fix](2026-10-06-images-refresh-flicker.md) and [incremental follow-up](2026-10-06-images-incremental-refresh.md); final tests/smoke remain owner-owned under fast-implement.
- **Discovery:** committed final artifact references in durable attempts are authoritative, including custom output destinations. The read path does not acquire dispatch ownership, reconcile state or treat logs as output truth.
- **Defaults:** without a valid viewer preference Images opens Grid; Detail image viewing defaults to Fit.

## Completion boundary

All selected viewer capabilities above are delivered and locally validated, with bounded loading and no blocked event loop.
Saved originals and durable state remain unchanged by browsing; disposable cache writes and durable viewer preferences are allowed.
No presets, reuse/regeneration, organisation, destructive file actions or cache-management controls are introduced by 8A.
No live inference, training or potentially billable action is needed for viewer acceptance.
Then proceed to [8B preset interchange](2026-10-05-phase-8-gallery-presets.md#8b--draw-things-compatible-presets).


PR #33's preview-only forms and styling remain documented in [the UI preview plan](2026-10-05-phase-8a-ui-preview.md). PR #34 supplies the production saved-output services and controller. On 2026-10-06 the owner reported the full test suite passed after the gallery lifecycle fix; interactive smoke testing also exercised the Images viewer and drove Grid/List thumbnail-sizing fixes. No agent-run inference, training, provider generation or potentially billable action was used for 8A validation. Packaged Windows/native-DPI visual acceptance remains a separate release concern.

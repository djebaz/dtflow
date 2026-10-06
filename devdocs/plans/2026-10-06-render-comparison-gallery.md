# Side-by-side render gallery

Date: 2026-10-06. Development-only extension to the capture tooling in PR #36.

- Generate one portable, offline HTML file from two capture directories.
- Pair PNGs by relative path; retain unmatched images and label missing sides.
- Offer Screens/States filters, search, paired/missing filters and keyboard browse.
- Provide Fit and 100%/shared zoom views without stretching images. Sheet layouts and fixture differences remain visible; no parity score or fabricated alignment.
- Embed original PNG bytes; no image processing, server, uploads or new dependency.
- Show source directories and dimensions. Include capture-manifest limitations when available; do not interpret successful capture as matching appearance.
- Validate pairing/embedding/escaping with compact offline tests, check help and lint/format, generate against the supplied ZIP extracts and existing DS captures, then headlessly smoke-test navigation/filter/zoom and missing sides.
- Preserve unrelated working changes. No provider actions, inference or billing.

## Comparison usability

Use one shared scale in every view, including Fit pair. Keep a common top-left canvas origin and equal canvas extents so linked scrolling stays meaningful even for different image dimensions. Offer per-pair X/Y offsets in source pixels, retained while browsing, with a reset action. State sheets still have different layouts: manual alignment targets a region, not every row/column simultaneously.

## Matched state capture contract

Regenerate Qt states first and export a versioned contract containing sheet rows, state order, fixed capture frames and DPR 1. The web renderer consumes that contract, uses the same sheet compositor and reserves unsupported slots instead of shifting columns. Both keep native component dimensions, position specimens at an 8 px inset, and enlarge cells exactly 2x. Fixed frames must reject overflow rather than crop or resize silently. Record actual Qt DPI/DPR and retain the original ZIP separately. Identical harness geometry is not proof of matching fixtures, component APIs, fonts or visuals.

The gallery exposes per-component/state regions only when both renderers have identical contracts. It displays those regions with CSS clipping of original PNG bytes at one shared scale, not independently resized images. Whole-sheet review remains available.

## Round overlay capture correction

RoundButton uses absolute positioning and must be hosted in a positioned, nonzero-height overlay wrapper. Position the specimen at the shared 8 px inset without altering the exported button CSS or its native 42 px diameter. Its forwarded DOM props support disabled, so capture that state rather than reporting it unsupported. Verify observed component bounds are contained by the screenshot host before capturing, preventing silent clipping from other out-of-flow specimens.

## Icon button capture coverage

Capture Grid, List, Detail, Additional details, Raw metadata JSON, Refresh and Fullscreen using the canonical Qt form and gallery presentation initializer, plus both round directions. Retain each button in its canonical page during capture so scoped ancestor selectors and Designer layout constraints are preserved; capture the widget itself with the common inset. Web specimens use the export's Icon component and the same icon names/sizes/composition as its Images kit, with an explicit local asset base. Wait for image load/decode after the final state transition before screenshots, and fail broken image assets. Verify expanded Detail icons without injecting icons or changing the export. Preserve native renderer sizing and state differences.

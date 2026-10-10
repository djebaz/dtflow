# Plan: Smart zone selection and inpainting

Date: 2026-10-10. Draft PR: #64. Approved interaction design; implementation starts after this plan commit.

## Scope
- In: Generate Inpaint mode, external/saved image input, click/refine smart masks, manual painting, local and direct DT+ request paths, durable inputs and default-on compositing.
- Out: inpainting queues/batches, control/reference combinations, downloads, accuracy claims, automatic live inference or billing acceptance.

## GUI Components Affected
- [ ] Main window: editor wiring and gallery Edit handoff
- [ ] Panels/widgets: Generate inline mask editor, shared canvas extension
- [ ] Designer forms: canonical editor form and Generate placement
- [ ] Shared QSS/tokens: editor controls using existing roles
- [ ] Config files: selector model choices, saved editing settings
- [ ] Packaging: ONNX runtime and new canonical forms, no bundled weights

## Action items
- [ ] 1. Headless mask document and preparation: source-pixel coordinates, EXIF/RGB normalization, binary polarity, fitting, grow/shrink, inward feather and immutable attempt inputs.
- [ ] 2. Inline editor: load/save masks; hover/click replace, Shift add, Alt subtract; brush/erase/rectangle; Undo/Redo/Clear; Space-pan; shared fit/zoom; source/mask/result views.
- [ ] 3. Extract PoC geometry/ONNX adapters without importing tools. Preserve person instances and separate body/clothing layers; ambiguous associations require explicit choice; selected-person clothing crop.
- [ ] 4. Explicit CPU analysis with sequential sessions, cancellation between stages, stale-result suppression, optional model capabilities, no hidden inference/downloads.
- [ ] 5. Generate integration and Images focused-output Edit handoff, without dispatch. Add source/mask request fields and prevent queues/batches from accepting inpaint requests.
- [ ] 6. Extend local and DT+ wire encoding before authorization, preserve seed zero, record effective settings and evidence-gate exact backend/checkpoint combinations.
- [ ] 7. Retain raw provider output and optional composite; immutable source/mask/settings evidence; preserve history and uncertainty, no replay.
- [ ] 8. Sync README/AGENTS/DESIGN, desktop/build guides, architecture, initial plan/status, release notes and PR body with implemented vs pending scope.

## Decisions
- Generate workspace; Images Edit acts on focused output, never checked-image bulk scope.
- Layers: Body / Clothes / Person. Body filters: All pixels / Skin only / Clothing only. Filter is captured per selection action, never rewrites prior edits.
- One chosen person at a time; automatic masks/expansion cannot enter other instances. Suggestions are not ground truth. Preserve separate region masks rather than PoC's mutually exclusive final labels.
- Whole-person requires segmentation; parts/pose/clothing are independently optional; brush-only works without weights. Model files are explicit local ONNX inputs with recorded checksums/contracts. No automatic downloads; review exact licensing before distribution.
- Default core regions; expansion off. Explicit bounded geodesic expansion respects clothing barriers and selected-person ownership.
- White (255) editable / black (0) preserved. EXIF-oriented RGB with white alpha compositing; aspect-preserving generation fitting/padding, nearest-neighbour binary masks.
- Strength 0.75, grow 0, inward feather 0 by default. Provider blur/outset 0 to avoid hidden duplicate adjustments. Preserve outside mask checkbox defaults ON; OFF uses raw provider result. Composite outside support is exact against prepared source.
- One output per submission. Source/mask are snapshotted before submission; durable input artifacts, not cache. New requests use backend-neutral references and operation=inpaint; historic requests remain txt2img.
- DT+ nearby disclosure: source/mask leave the machine on Generate and credits may be consumed. Never silently switch providers or enable PAYG.
- Exact backend/checkpoint support is evidence-gated; no deployed inpainting compatibility established yet. The PR may contain pending slices without advertising them as accepted.
- CPU-only analysis worker separate from GUI and orchestration loop; one session at a time; cancel between stages and report stopping-after-current-stage honestly. No persistent analysis cache initially.

## Open questions
None for starting the offline editor and preparation slice. Backend acceptance/licensing are explicit later gates, not reasons to run unapproved inference.

## Validation
- No tests added/run, validation commands, smoke runs or inference under python-gui-fast-implement.
- [ ] USER: synthetic/service and adapter contracts, source/mask encoding and seed zero
- [ ] USER: Qt coordinates, modifiers, Undo/Redo, multi-person isolation and shutdown
- [ ] USER: source orientation/fitting, exact compositing and historic state compatibility
- [ ] USER: representative real photos and target-CPU responsiveness
- [ ] USER: exact packaged Windows executable and ONNX/forms bundling
- [ ] USER: separately authorize bounded local/DT+ acceptance per advertised checkpoint

## Progress
- Draft PR #64 opened before the plan and runtime changes.
- Plan recorded; no runtime changes yet.

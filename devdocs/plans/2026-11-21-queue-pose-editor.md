# Plan: Queue pose editor (#515)

## Scope

- In: edit the Queue draft's ControlNet checkpoint/quantization, strength, control force and apply range; show its attached map and explicitly replace it with a prepared body map. Support captured poses, loaded templates and Edit as new from recorded queues.
- Out: photo extraction, joint editing, broader model compatibility, provider calls during editing, tests/fixtures/renders, packaging and unrelated branch-cleanup tooling.

## Action items

- [x] Inspect issue/ACs, current main, independent drafts, pose preparation and queue/template persistence paths.
- [ ] Mount a compact canonical Designer form and bind controls to the existing draft representations without modifying Generate or saved queues/templates.
- [ ] Decode previews and prepare replacement maps on workers; reject invalid input and stale deliveries without losing the previous map.
- [ ] Preserve exact edited controls across queue creation and explicit template Save/Update, retaining current validation, size limits and consent boundaries.
- [ ] Update design/user/developer guidance and release notes; source-review the complete change and report static evidence separately from owner/CI acceptance.
- [ ] Commit/push coherent checkpoints and prepare the feature PR for review without manually dispatching CI.

## Decisions

- Preserve both existing draft paths: captured/template poses use immutable PoseJob; recorded queue drafts retain their request and verified map bytes. Control edits must update the matching template projection too.
- Explicit map replacement creates a fresh PoseJob and clears old execution inputs and extraction evidence. It preserves base settings, fixed LoRAs, seeds and plan variations; no staging or provider operation occurs until existing explicit queue actions.
- Numeric editors must not clamp imported values. The existing PoseControl constructor remains the validator; invalid in-progress text blocks queue/template capture rather than falling back to old values.
- Control choices come only from Generate's already cached supported inventory. Retain an unavailable saved identifier visibly, without substituting another control or claiming availability.
- Keep ordinary text-to-image/Inpaint behavior and template/database schema unchanged. Preview/load work is local and bounded; stale results are rejected on draft, settings, navigation or shutdown changes.

## Evidence and acceptance

- Initial state: clean main at `44e43bbb`, equal to current origin/main. Issue #515 and first-comment AC-1 through AC-8 are the scope/acceptance authority.
- No tests, fixtures, renders, inference, uploads, builds or billable operations are authorized. Behavioral Must ACs remain pending appropriate owner/CI evidence; static inspection does not establish Windows/DPI or backend acceptance.

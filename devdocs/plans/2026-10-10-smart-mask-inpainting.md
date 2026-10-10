# Plan: Smart zone selection and inpainting

Date: 2026-10-10. Draft PR: [#64](https://github.com/djebaz/dtflow/pull/64). Base: main `0d50b21`.

## Scope and interaction-design source

This plan implements the owner's approved conversation design, archived below under **Interaction contract**. It deliberately spans Phase 9A (source-image/strength), 9B (existing mask-file inpainting) and 9C (interactive/smart masks), rather than implying 9A/9B already exist. Deliver and document those slices independently, following [initial Phase 9](initial.md#phase-9--advanced-image-operations) and [the release gates](releases.md#later-incremental-image-workflow-and-cloud-releases). No version bump or release acceptance is implied.

- In: shared headless image preparation, source/mask-file requests, local and direct DT+ wire paths, durable input evidence and optional compositing, then Generate editor and local smart analysis.
- Out: image-operation queues/batches, generic CLI image-operation dispatch, control/reference combinations, downloads, automatic inference, PAYG, accuracy claims or unverified advertised support.

## Prerequisites and refusal policies

- Current core and wire accept txt2img only. Build `img2img` before `inpaint`: immutable source references, strength, fitting, image tensor transport, capability checks and persistence are prerequisite work, not incidental UI edits.
- First owner-gated acceptance must exercise one exact checkpoint on one selected backend through the headless path. Implement both backend paths, but defer the editor/ONNX slice until this first acceptance is recorded. Local acceptance never establishes DT+ acceptance.
- DT+ img2img/inpaint requires an exact-file upstream model description with its modifier/objective and required companion files, sent via `override.models`. Do not apply plain txt2img family-derived fallback descriptions. Missing description or missing exact backend/checkpoint/operation evidence refuses before authentication/submission; inventory presence is not evidence.
- Source and mask tensors must be encoded and, where required, content-addressed into `contents` before the cloud request is authorized. Do not reuse LoRA upload semantics for image inputs or assume PNG bytes are wire tensors. Preserve resolved seed zero through direct `GenConfig.to_fbs`.
- Source/mask request changes require request schema v2, attempt metadata schema v2 and execution DB schema v3. Migration preserves historical txt2img requests/outputs/reservations, backups consistent SQLite state before transactional migration, and never makes an uncertain attempt replayable. Older records default to txt2img; older writers must reject DB v3. No bump until that slice changes durable records.
- Queues and planner/batch entry points explicitly refuse non-txt2img. Existing generic CLI generation/config import/export also refuses image operations initially, rather than silently stripping source/mask fields. Shared headless services remain reusable for a later dedicated CLI.
- Keep original/provider-neutral ownership in [runtime layout](../architecture/runtime-layout.md). Durable source/mask/alpha/transform records live in `<state>/inputs/<attempt-id>/`; final/raw images and metadata in `outputs/<attempt-id>/`. Only explicitly registered final gallery artifacts participate in export/recycle. Inputs, raw evidence and draft state are not disposable cache. No broad output/input cleanup is introduced.
- ONNX production extraction waits for the editor slice. Pin the reviewed CPU-only `onnxruntime` runtime version through uv, retain NumPy 2.3.5, update lockfile and package collection together. Owner must establish Pentium E6500 compatibility; installed/importable on a newer CPU is insufficient. No bundled weights or GPU fallback.
- Maintainer/owner reviews AGENTS' Pillow/ONNX boundary when adding new headless image-preparation/analysis services. All Pillow usage remains in those headless services or model integration helpers, never core or Qt. Update the existing authoritative import-boundary contract at the owner's validation stage; do not add tests under fast-implement.

## GUI Components Affected (later slices)
- [ ] Main window: editor wiring and gallery Edit handoff
- [ ] Panels/widgets: Generate inline editor and shared canvas extension
- [ ] Designer forms: canonical editor form and Generate placement
- [ ] Shared QSS/tokens: existing roles, no unrelated theme rewrite
- [ ] Config files: selector model choices and durable input identity
- [ ] Packaging: CPU ONNX runtime/new forms; no bundled weights

## Action items in dependency order
- [ ] 1. Headless preparation: bounded EXIF/RGB decoding, binary polarity, mask-file validation, aspect-preserving fitting/padding, grow/shrink, inward feather and immutable content hashes. Document this slice immediately.
- [ ] 2. Durable source-image path: request/attempt/storage schema changes, immutable input snapshots, strength and capability checks, CLI/queue/planner refusal, reproducible migration and failure evidence. Update architecture/runtime/status/release docs with this slice.
- [ ] 3. Local and DT+ img2img/inpaint wire encoding: exact cloud descriptions, image/mask transport before authorization, verification of effective settings and no unsupported fallback. Update backend docs with code.
- [ ] 4. USER: separately authorize and record one bounded exact checkpoint/backend headless img2img plus mask-file inpaint acceptance. Nothing runs automatically under fast-implement; keep unsupported combinations refused. No editor/analysis promotion before this gate.
- [ ] 5. Raw output plus optional composite: exact prepared-source pixels outside mask support; retain raw result and transform/effective evidence; wire gallery/export/recycle only to registered final images. Update docs with this slice.
- [ ] 6. Inline editor: source/mask load/save, click/modifier selection, brush/erase/rectangle, Undo/Redo/Clear, shared fit/zoom/pan; Images focused Edit -> Generate without dispatch. Canonical Designer/QSS; docs move with behavior.
- [ ] 7. Smart models/analysis: isolated runtime primitives extracted from PoC, separate person instances and layers, explicit ambiguous association, per-person clothing crops, optional capabilities, cancellation and stale-delivery guards. Update dependencies/build/AGENTS and docs in the same slice.
- [ ] 8. Owner acceptance and PR completion: synthetic/service/wire checks, Qt source-coordinate and lifecycle scenarios, real-photo/old-CPU review, exact packaged executable; keep separate local/cloud evidence. No automatic tests/build/inference.

## Interaction contract (approved conversation design)

- Generate workspace; Images Edit acts on focused output, not checked-image bulk scope. Neither import nor Edit dispatches generation. Masks can be saved independently.
- Layers: Body / Clothes / Person. Body filters: All pixels / Skin only / Clothing only. Filter is captured per selection action and never rewrites prior edits.
- Click replaces, Shift-click adds, Alt-click subtracts. Hover previews region/name; searchable region list is keyboard-accessible. Brush, Erase, rectangle, Undo/Redo/Clear. Space-pan; Ctrl-wheel zoom. Manual edits may extend beyond people.
- One chosen person at a time; automatic masks/expansion cannot cross into others. Ambiguous associations require explicit assignment; unassigned regions remain unknown. Keep overlapping body and garment masks independent rather than PoC's mutually exclusive labels.
- Person segmentation enables whole-person selection; parts/pose/clothing independently add capabilities. Brush-only works without weights. Local ONNX files are explicitly selected with recorded checksums/contracts; no downloads. Exact licensing reviewed before distribution.
- Default conservative core regions and expansion OFF. Bounded geodesic expansion is explicit and preserves skin-only clothing barriers and person ownership. Subject-relative pose left/right; no assumed clothing left/right equivalence.
- White 255 editable / black 0 preserved. EXIF-oriented RGB; alpha composited on white. Generation target fit is aspect-preserving with padding; nearest-neighbour masks and no selected padding.
- Strength 0.75, grow 0, inward feather 0 by default; provider blur/outset 0 to avoid hidden duplicate adjustments. Preserve outside mask checkbox defaults ON; OFF returns raw provider result. Source/Mask/Result/comparison views.
- One output per submission. Snapshot source/mask/settings before authorization. Images never reread mutable external files after submission. Durable state, not cache; originals remain untouched.
- Nearby DT+ disclosure: Generate sends source/mask off-machine and may consume credits. Never switch providers or enable PAYG silently.
- Dedicated CPU analysis worker separate from GUI/orchestration loop; sequential sessions, one active analysis. Cancel between stages; report stopping-after-current-stage honestly. No persistent analysis cache initially; no hidden automatic inference.

## Validation

No tests added/run, validation commands, smoke runs, renders, builds or inference under python-gui-fast-implement. Static source reading is not runtime acceptance. CI/owner work remains distinct.

- [ ] USER: service/adapter contracts, polarity, aspect fit and tensor encoding, seed zero
- [ ] USER: request/DB migration, immutable snapshots, unsupported CLI/queue refusal and no uncertain replay
- [ ] USER: local then DT+ acceptance with exact checkpoint description and request evidence
- [ ] USER: raw/composite ownership, exact outside-mask preservation, export/recycle/cache exclusions
- [ ] USER: Qt coordinates/modifiers/history, multi-person ownership and shutdown
- [ ] USER: real photos, NumPy/ORT on Pentium E6500 and exact packaged Windows executable

## Progress

- Draft PR #64 opened before plan/runtime changes; plan committed at `f3658e8`.
- Reviewer identified missing 9A/9B prerequisites and backend-first ordering. This revision supersedes the original action order while retaining the approved interaction contract.
- Uncommitted selector extraction parked under ignored `tmp/smart-mask-deferred-scaffolding/`; it is not production code and is not part of the PR.
- Starting headless preparation only; request schema, adapters, acceptance gate and UI remain pending.

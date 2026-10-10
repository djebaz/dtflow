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
- [x] 1. Headless preparation (source only, unverified): bounded EXIF/RGB decoding, binary polarity, mask-file validation, aspect-preserving fitting/padding, grow/shrink, inward feather and immutable content hashes. Document this slice immediately.
- [x] 2. Durable source-image path (source only; migration/runtime acceptance pending): request/attempt/storage schema changes, immutable input snapshots, strength and capability checks, CLI/queue/planner refusal, reproducible migration and failure evidence. Update architecture/runtime/status/release docs with this slice.
- [x] 3. Local and DT+ img2img/inpaint wire encoding (source only; exact-checkpoint acceptance pending): exact cloud descriptions, image/mask transport before authorization, verification of effective settings and no unsupported fallback. Update backend docs with code.
- [ ] 4. USER: separately authorize and record one bounded exact checkpoint/backend headless img2img plus mask-file inpaint acceptance. Nothing runs automatically under fast-implement; keep unsupported combinations refused. No editor/analysis promotion before this gate.
- [x] 5. Raw output plus optional composite (source only; export/recycle/runtime acceptance pending): exact prepared-source pixels outside mask support; retain raw result and transform/effective evidence; wire gallery/export/recycle only to registered final images. Update docs with this slice.
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

Earlier slices did not add/run tests, validation commands, smoke runs, renders, builds or inference under python-gui-fast-implement. The owner explicitly authorizes one CLI refusal test and offline lock/Ruff/pytest/help checks for the step 4 harness. Live generation remains owner-only. Static source reading and offline checks are not checkpoint acceptance. CI/owner work remains distinct.

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

- First runtime slice adds `core/image_inputs.py` and `services/image_preparation.py`. Optional compositor exists as a pure pixel helper only; output registration/persistence and the rest of slice 5 remain pending. No request/storage schema is bumped yet.
- AGENTS and the existing authoritative import-boundary allowlist are updated narrowly for Pillow in the new headless service. No tests added or run; owner/CI validation remains required. No ONNX production dependency is introduced in this slice.

- Owner is implementing UX/UI in parallel; this continuation does not edit Qt/controllers/forms/styles. Production ONNX/editor promotion remains owner-gated by the plan, regardless of parallel design work.
- Durable input building blocks are source-present: immutable JSON-safe references/input schema v1, worker snapshot/restore services, exclusive attempt-owned directories and last-written manifest, bounded reads/hash checks and pixel/fit/padding/blend verification. Partial sets are preserved, not replayed or cleaned automatically. Step 2 is not complete: resolved request v2, execution DB v3 migration and generation orchestration remain pending.
- Provider-local tensor preparation is source-present: locked SDK RGB float16 and mask 0/2 encoders, header/size/polarity checks, binary SHA-256 references plus contents, and preflight byte caps. Official proto/server/ComfyUI source was inspected; no endpoint was contacted. Step 3 is not complete: operation-specific config, exact-description/evidence gate and adapter/auth integration remain pending; ordinary generation is still txt2img-only.
- No tests, validation commands, builds, renders, inference or billable actions were run for this continuation. Source work is not accepted runtime behavior.
- Request/schema continuation: resolved request v2 now represents managed image inputs and strength with target/mask/single-output invariants. Core authoritative record decoding supports explicit v2 and historical txt2img v1 only. New txt2img metadata uses v2; readers keep old metadata supported.
- Execution DB v3 source migration backs up each v1/v2 upgrade to a fresh consistent UUID-named copy, upgrades request JSON in bounded stable-key batches and retains existing transactional no-replay reconciliation. New image attempts require matching input ownership. Gallery read-only v1/v2/v3 and queue read-only v2/v3 remain separate from writes. Runtime/migration acceptance is not claimed.
- Queues, stored queue decoding, planners and generic CLI/config metadata import/export explicitly refuse image operations. The existing saver also refuses them until raw/final evidence handling is connected. Existing migration contract fixture/assertions adapted only; no new tests or execution.
- Step 2 request/schema/refusal portion is source-present, but capability/evidence and orchestration binding remain pending with step 3. Current providers still advertise txt2img only. No UI files touched in this continuation; no tests/validation/build/inference/billable calls.
- Review follow-up at 91b690c: bounded repeated 3x3 morphology (32 million padded pixel-passes), full-range unsigned 16-bit mask scaling, a 75% normalized selected-area fit guard at preparation/restore, and audited failure/skip of invalid pending queue records without attempt evidence. Associated attempts still fail closed; original JSON remains inspectable. Junction ancestor refusal stays strict with a clearer location hint. No tests/validation/runs; dispatch integration remains separate work in progress.

- Committed unfinished dispatch checkpoint at owner request: scoped permits/exact cloud descriptions, image wire configuration, generation snapshot binding and dedicated raw/final saver source. Default operations remain txt2img; session image entry/loader and output handoff, cloud failure cleanup and raw-copy aggregate budget review remain pending. No UI edits, tests, validation, inference or billable actions; steps 2/3/5 are not declared complete.
- Re-review at bae6b22: output metadata now explicitly records prepared-source RGB8/white-alpha/no-ICC-transform normalization (not original-file pixel preservation); final artifacts have distinct application-derived IDs linked to raw provider evidence. Grow +/-64 is clarified as an absolute bound with a dimension-dependent pixel-pass limit; budget is not raised or silently clamped. Source-only; no tests, validation, runs or inference. Session completion remains pending.

- Session completion: generate_image_files_once prepares on a settled worker; generate_image_once accepts immutable prepared inputs plus explicit owner permit. open_session passes managed loader/input state and durable ownership; run_generation gates exact scope before inventory and returns registered final bytes/effective attempt-bound request. Cloud permit/concurrency preflight precedes auth-client allocation; raw-copy aggregate output limit now matches composite mode. No permits ship, no inference is authorized/run, and 4/6/7/8 remain pending. Generic CLI/queues/plans/config remain txt2img-only; source-only 2/3/5 checkmarks do not claim acceptance.
- Manual UI prerequisites: read-only support status/reason, explicit maximum-edge size suggestion, shared effective grow limit, bounded thumbnail preview/exact pixel coverage and unadjusted mask load/new-file save are source-present. UX alignment addendum records probe consent, prepared-source preservation, OFF result geometry and single-shot refusals; mockup/Qt/ONNX remain untouched. Owner requested Ruff formatting and lint checks for this checkpoint, with formatting/lint-only commit 977ed66 separate from implementation per the owner's final instruction; no tests/builds/inference.

- Owner-requested checks: uv run ruff format . reformatted 15 existing/current files initially; uv run ruff check . found five findings (two import ordering, two call defaults, one exception chaining), all fixed. Final repeat: 306 files unchanged/one formatted on the preceding pass; lint All checks passed. Formatting/lint-only baseline is committed separately; tests/builds/renders/inference/billable actions remain unrun.
- Review clarification: MaskPreview supplies an explicit approximation note (subpixel grow can disappear); size suggestion is a target box, not a no-upscale guarantee, and the editor must show actual ImageFit. Capability status says owner-confirmed and explicitly excludes checkpoint availability; active inventory filtering remains independent. No fit/query authorization behavior changed; no Qt/mockup edits. Ruff formatting/lint are authorized, tests/inference remain unrun.
- Checks for this clarification: uv run ruff format . left all 307 files unchanged; uv run ruff check . passed. No formatter/lint fixes to mix into the implementation commit. No tests, builds, renders, inference or billable actions.

- Step 4 harness: dedicated probe-only `dtflow edit` calls the shared file/session path, records user evidence with `owner_probe`, requires explicit one-invocation consent and exact published cloud description digest, and reports final/metadata paths. One offline CLI refusal test is added by explicit owner request. The owner authorized lock/lint/format/pytest/help checks for this slice; the earlier no-validation rule does not apply to those requested checks. Step 4 remains unchecked: no live img2img/inpaint acceptance has run.
- Harness validation (2026-10-10): `uv lock --check`, `uv run ruff check .`, `uv run ruff format --check .` (307 unchanged) and `uv run dtflow edit --help` passed. `uv run pytest`: 598 passed, 3 live tests skipped, 1 failure in the unchanged Qt overlay lifetime assertion (5900 vs 6000 after visibility wait). Isolated rerun of that exact Qt test passed (1 passed). The new no-probe CLI refusal test passed. The full suite is not claimed green. No live generation, model inference, training or billable action was performed; step 4 live acceptance remains owner-only.

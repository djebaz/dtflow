# Unreleased

## Highlights

- Smart mask selection and inpainting work is starting in a separate draft PR. Headless preparation, durable input snapshot/restore and tensor-encoding helpers are source-present; request v2 and backed-up DB v3 migration are source-present; dedicated provider dispatch/raw-output saving are source-present. Owner passed the bounded step-4 bridge/direct-cloud probe gate; the offline manual editor is in progress, not a usable app image-dispatch feature. Existing generation behavior is unchanged.

## Added

- Source-only manual-editor support: read-only permit status/reason, generation-size suggestion/shared grow limits, bounded thumbnail previews and binary coverage, unadjusted mask load/new-file save. UX alignment clarifies normalized preservation, raw-result geometry, unsupported actions and explicit per-run probe consent. Offline Qt manual-editor wiring is in progress; ONNX and app dispatch remain pending. Bounded owner CLI evidence is recorded separately.

- Dedicated permit-scoped headless single-shot image session with worker file preparation, immutable managed-input loader, durable attempt binding and registered final/effective-request handoff. Cloud allocation preflight and aggregate raw-copy limits are source-present. No default permits, accepted checkpoints, UI/ONNX or generic CLI/queue/config image dispatch; no tests/validation/inference.

- Unfinished headless dispatch checkpoint: exact-scope image permits/cloud description digests, image wire configuration, attempt snapshot binding and raw/final saver source. Session image entry/loader and output handoff remain pending; no default image capability, GUI change or runtime acceptance.

- Durable image-input snapshot/restore helpers with exclusive attempt-owned sets, bounded manifest/content reads, content and pixel/fit verification; provider-local RGB/mask 0/2 tensor preparation with SHA-256 content references. Not integrated with dispatch; no GUI change, runtime acceptance or inference. Request/DB integration progresses separately below.

- Headless image-operation preparation and immutable content/fit evidence: bounded EXIF/RGB normalization, explicit binary masks, joint target fitting, mask adjustment and source-sized compositing. No dispatch/editor integration or acceptance yet; no tests/validation/inference under fast-implement.

- Standalone CPU-only person/rectangle mask PoC under tools/images, with a dedicated uv development group, model/checksum contract, five diagnostic outputs, explicit TorchVision/FastSeg profiles and focused non-inference tests written before owner requested fast-implement. Real model and target-CPU smoke acceptance remain pending; no production inpainting integration.

## Changed

- Explicit resolved-request v2 and new attempt metadata v2; execution DB v3 with fresh consistent v1/v2 backups and transactional historical txt2img request migration. Preserve output files, identities and existing no-replay/uncertainty reconciliation; unsupported schemas/malformed requests fail closed. Source-present only; owner migration acceptance pending.
- Explicit image-operation refusal in queues/plans and generic configuration/CLI resolution/import/export; no silent loss of inputs/strength. Read-only gallery and queue listing retain supported older DB access. Providers still advertise txt2img only; image dispatch/raw-final saving remains pending.

## Fixed

- Manual-editor handoff now explicitly labels approximate previews/subpixel grow, documents possible small-source upscaling and actual fit evidence, and distinguishes owner-confirmed permits from installed checkpoint availability. Inventory filtering remains independent; no fitting algorithm or dispatch authorization change.

- Owner-requested Python-wide Ruff formatting and lint fixes (import order, ResourceLimits defaults created per call, explicit queue exception chaining), in separate formatting/lint-only commit 977ed66. Final Ruff lint passes; no tests or runtime acceptance.

- Image-operation saving records RGB8/white-alpha/no-ICC-transform source normalization and its preservation limits explicitly, with distinct final/raw artifact IDs and a raw-evidence link. Grow +/-64 is documented as an absolute bound constrained by source-size work budget, not universal availability. Source-only; no runtime acceptance.

- Source-only smart-mask review fixes: bounded small-kernel grow/shrink, full-range 16-bit mask thresholding, refusal of over-25% normalized selection loss at fit/restore, and audited invalid pending queue failure/skip while preserving associated-attempt uncertainty. Strict junction/ancestor refusal now explains choosing a non-redirected state location. No tests or runtime acceptance.

## Release audit

- PRs: #63, #64
- Scope: Standalone CPU person-zone mask PoC, headless image preparation, durable input snapshot/restore and provider-local tensor encoding; no image-operation dispatch or production acceptance.








- Added the bounded `dtflow edit` owner-probe acceptance harness (single shot, explicit consent, exact cloud description gate, permit evidence in request/output). Generic generation and queues remain txt2img-only; no live checkpoint acceptance is claimed.
- The edit harness now exposes explicit local-to-DT+ bridge opt-in (`-c/--bridge`), recording unlisted-model authorization in the request without weakening defaults or direct-cloud description checks. Live bridge acceptance remains pending.

- Offline manual-editor checkpoint: joined token-based mode buttons, default strength 0.7, source/mask canvas, bounded undo/redo, worker load/save and inline Clear confirmation. Listed preview fixtures and the desktop font-directory guide accompany the UI; no render/native-DPI acceptance is claimed. Smart-selection and Edit image overlay previews are synthetic presentation fixtures, not implemented operations.
- Inline Inpaint editor now dispatches: **Edit image** runs directly as an owner probe (the click is the consent; the amber capability line discloses the upload and possible cloud credits), prepares the fitted source/mask on a worker and runs one `generate_image_once` attempt through the Generate pipeline; the Result view shows the final image. Adds Grow/Feather, strength default 0.7 (probe evidence: Fill checkpoints work best at 1.0, so raise it for replacement), generation size following the source aspect, a worker-computed capability line (cloud needs an exact published description), an Images **Edit image** handoff, grayscale mask canvas with throttled tint and a Result view. No permits ship, so every edit is a probe; ONNX smart selection and owner acceptance of the app flow remain pending. No tests, renders by the owner, inference or billable actions were run by the author.
- Inpaint editor UX aligned with the mockup: icon Undo/Redo at the canvas top-left, Source | Mask | Result switch top-right, zoom bottom-left, segmented tools with a brush-size slider, collapsed Advanced (Grow/Edge feather), a separate Live mask card with thumbnail, coverage delta and an Edits list, and shorter right-panel text. Fixed the in-place Confirm for Replace/Remove source and Clear mask, which re-ran the action instead of committing. Select mode and Strength/Keep-outside placement in the left Parameters card remain pending.

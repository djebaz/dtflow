# Unreleased

## Highlights

- PR #76 source-present Images split action: choose Trash or Delete permanently, with separate Windows/WSL preferences and fresh inline irreversible-action confirmation every time. No automatic permanent fallback or persisted consent. Guarded WSL removal of Windows-recorded managed originals uses a narrow native Windows lock/handle boundary; unsupported support refuses access. Metadata/history/exports remain intact. Removal ledger upgrades to schema 2, preserving old records while older applications refuse it. Focused disposable-file lock probes only; no tests, renders, builds or real deletion. Owner runtime acceptance remains pending: [implementation plan](../plans/2026-10-17-image-removal-modes.md).

- Source-present WSL gallery read-path resolution and selected-toggle font follow-up:
  [implementation plan](../plans/2026-10-17-wsl-gallery-paths-and-fonts.md).
  Windows references remain unchanged; verified WSL 1 mounts supply read aliases.
  Process-local Windows Segoe UI faces avoid heavier fallback toggles when available.
  Export/edit/reuse/regeneration remain restricted for foreign Windows outputs;
  no schema migration or new destructive support. No agent tests/renders/validation;
  owner reports current-PR smoke tests passed on 2026-10-17. Individual edge cases,
  concurrent cross-platform mutations and packaged acceptance remain unverified.

- Smart mask selection and inpainting work is starting in a separate draft PR. Headless preparation, durable input snapshot/restore and tensor-encoding helpers are source-present; request v2 and backed-up DB v3 migration are source-present; dedicated provider dispatch/raw-output saving are source-present. Owner passed the bounded step-4 bridge/direct-cloud probe gate; the offline manual editor is in progress, not a usable app image-dispatch feature. Existing generation behavior is unchanged.

## Added

- Disposable agent environment setup via `tools/setup-agent.sh`: automatic checkout discovery or `DTFLOW_PROJECT_ROOT`, managed Python from `.python-version`, frozen uv sync, Linux Qt libraries and explicit headless/TLS/link-mode settings. Setup does not run tests, render UI or submit generation; native platform acceptance remains separate.

- Source-only manual-editor support: read-only permit status/reason, generation-size suggestion/shared grow limits, bounded thumbnail previews and binary coverage, unadjusted mask load/new-file save. UX alignment clarifies normalized preservation, raw-result geometry, unsupported actions and explicit per-run probe consent. Offline Qt manual-editor wiring is in progress; ONNX and app dispatch remain pending. Bounded owner CLI evidence is recorded separately.

- Dedicated permit-scoped headless single-shot image session with worker file preparation, immutable managed-input loader, durable attempt binding and registered final/effective-request handoff. Cloud allocation preflight and aggregate raw-copy limits are source-present. No default permits, accepted checkpoints, UI/ONNX or generic CLI/queue/config image dispatch; no tests/validation/inference.

- Unfinished headless dispatch checkpoint: exact-scope image permits/cloud description digests, image wire configuration, attempt snapshot binding and raw/final saver source. Session image entry/loader and output handoff remain pending; no default image capability, GUI change or runtime acceptance.

- Durable image-input snapshot/restore helpers with exclusive attempt-owned sets, bounded manifest/content reads, content and pixel/fit verification; provider-local RGB/mask 0/2 tensor preparation with SHA-256 content references. Not integrated with dispatch; no GUI change, runtime acceptance or inference. Request/DB integration progresses separately below.

- Headless image-operation preparation and immutable content/fit evidence: bounded EXIF/RGB normalization, explicit binary masks, joint target fitting, mask adjustment and source-sized compositing. No dispatch/editor integration or acceptance yet; no tests/validation/inference under fast-implement.

- Standalone CPU-only person/rectangle mask PoC under tools/images, with a dedicated uv development group, model/checksum contract, five diagnostic outputs, explicit TorchVision/FastSeg profiles and focused non-inference tests written before owner requested fast-implement. Real model and target-CPU smoke acceptance remain pending; no production inpainting integration.

## Changed

- Explicit resolved-request v2 and new attempt metadata v2; execution DB v3 with fresh consistent v1/v2 backups and transactional historical txt2img request migration. Preserve output files, identities and existing no-replay/uncertainty reconciliation; unsupported schemas/malformed requests fail closed. Source-present only; owner migration acceptance pending.
- Explicit image-operation refusal in CLI queues/plans (Queues-tab staged edits excepted, see below) and generic configuration/CLI resolution/import/export; no silent loss of inputs/strength. Read-only gallery and queue listing retain supported older DB access. Providers still advertise txt2img only; image dispatch/raw-final saving remains pending.

## Fixed

- Desktop startup tolerates Qt bindings without `QFile.supportsMoveToTrash`:
  Delete remains disabled and Trash source checks refuse safely. No dependency,
  generation or permanent-delete behavior changes; WSL/older-Qt acceptance remains separate.

- Offline fake-backend tests use a temporary deterministic PNG instead of depending on the bundled lighthouse illustration. A shared test-only patch supports explicit guarded CLI subprocess opt-in; ordinary guarded children preserve their import setup. The fitting contract checks centered-crop pixels and unfiltered RGB8 source encoding; desktop simulation artwork is unchanged.

- Agent setup runs APT update/install with `APT::Sandbox::User=root`, avoiding the `_apt` user/group switch rejected by restricted disposable containers. Package-mirror access and installation permissions remain environment requirements; failures still stop setup.

- Manual-editor handoff now explicitly labels approximate previews/subpixel grow, documents possible small-source upscaling and actual fit evidence, and distinguishes owner-confirmed permits from installed checkpoint availability. Inventory filtering remains independent; no fitting algorithm or dispatch authorization change.

- Owner-requested Python-wide Ruff formatting and lint fixes (import order, ResourceLimits defaults created per call, explicit queue exception chaining), in separate formatting/lint-only commit 977ed66. Final Ruff lint passes; no tests or runtime acceptance.

- Image-operation saving records RGB8/white-alpha/no-ICC-transform source normalization and its preservation limits explicitly, with distinct final/raw artifact IDs and a raw-evidence link. Grow +/-64 is documented as an absolute bound constrained by source-size work budget, not universal availability. Source-only; no runtime acceptance.

- Source-only smart-mask review fixes: bounded small-kernel grow/shrink, full-range 16-bit mask thresholding, refusal of over-25% normalized selection loss at fit/restore, and audited invalid pending queue failure/skip while preserving associated-attempt uncertainty. Strict junction/ancestor refusal now explains choosing a non-redirected state location. No tests or runtime acceptance.

## Release audit

- PRs: #63, #64, #73, #75, #76
- Scope: CPU mask PoC, image preparation/editor/owner-probe and queue work, agent setup, Qt Trash capability guard, WSL gallery aliases/fonts and explicit guarded Trash/permanent removal modes; production/platform acceptance remains separate. PR #76 Ruff lint/format passed; no actual removal acceptance is claimed.








- Added the bounded `dtflow edit` owner-probe acceptance harness (single shot, explicit consent, exact cloud description gate, permit evidence in request/output). Generic generation and queues remain txt2img-only; no live checkpoint acceptance is claimed.
- The edit harness now exposes explicit local-to-DT+ bridge opt-in (`-c/--bridge`), recording unlisted-model authorization in the request without weakening defaults or direct-cloud description checks. Live bridge acceptance remains pending.

- Offline manual-editor checkpoint: joined token-based mode buttons, default strength 0.7, source/mask canvas, bounded undo/redo, worker load/save and inline Clear confirmation. Listed preview fixtures and the desktop font-directory guide accompany the UI; no render/native-DPI acceptance is claimed. Smart-selection and Edit image overlay previews are synthetic presentation fixtures, not implemented operations.
- Inline Inpaint editor now dispatches: **Edit image** runs directly as an owner probe (the click is the consent; the amber capability line discloses the upload and possible cloud credits), prepares the fitted source/mask on a worker and runs one `generate_image_once` attempt through the Generate pipeline; the Result view shows the final image. Adds Grow/Feather, strength default 0.7 (probe evidence: Fill checkpoints work best at 1.0, so raise it for replacement), generation size following the source aspect, a worker-computed capability line (cloud needs an exact published description), an Images **Edit image** handoff, grayscale mask canvas with throttled tint and a Result view. No permits ship, so every edit is a probe; ONNX smart selection and owner acceptance of the app flow remain pending. No tests, renders by the owner, inference or billable actions were run by the author.
- Inpaint editor UX aligned with the mockup: icon Undo/Redo at the canvas top-left, Source | Mask | Result switch top-right, zoom bottom-left, segmented tools with a brush-size slider, collapsed Advanced (Grow/Edge feather), a separate Live mask card with thumbnail, coverage delta and an Edits list, and shorter right-panel text. Fixed the in-place Confirm for Replace/Remove source and Clear mask, which re-ran the action instead of committing. Strength and Keep-outside now live in the left Parameters card; Select mode remains pending.
- Select mode in the Inpaint editor: owner-selected local ONNX files (YOLO26 end2end person segmentation, optional SegFormer `segformer_b2_clothes` clothes model; paths and SHA-256 in `<state>/person_models.json`, nothing downloaded or bundled) drive **Find people**, click/Alt-click or list selection of a person's Person, Body and Clothes layers into the same undoable mask. New headless `services/person_selection.py` (CPU only; Pillow/NumPy/onnxruntime allowlisted for that service), `onnxruntime` and the pinned NumPy become runtime dependencies, and the PyInstaller build collects onnxruntime binaries. Not accepted: Windows/Pentium E6500 CPU compatibility, packaged-executable collection, and accuracy (faces include neck/chest; hair/hats/shoes are outside Body and Clothes). The Body filter and pose/part models from the PoC are not included.
- Cloud image edits no longer require an exact published model description (owner decision 2026-10-10, tested working without): when none exists nothing is sent for the file and the capability line warns that the cloud may return no image. Permits that record a digest still must match it; derived or sibling descriptions are never used for image operations.
- Select mode previews the person under the pointer (display-only tint of the chosen layers; never part of the mask or Undo history).
- Select layers are now a tick list: Person plus Body, Clothes and individual parts from the SegFormer classes (upper clothes, pants, skirt, dress, belt, scarf, hair, hat, sunglasses, face, arms, legs, shoes, bag). Part layers need the clothes model; accuracy is the model's, unreviewed.
- Inpaint edits can be queued from the Queues tab: with Generate in Inpaint mode, **Create queue** stages the prepared source and mask once (durable, hash-verified, under `<state>/inputs/`), records the owner-probe consent per item and creates a paused queue. The Plan area gains a **Strengths** field (a new planner dimension for staged edits, in Cross or Pair mode; size cannot vary), and the item table an Edit column. Start runs the items through the same image path as Edit image; unknown outcomes keep their reservation and never replay, and cloud uploads happen only at Start. CLI, plan files and txt2img queues are unchanged. Source-present, not owner-accepted. The Images details panel (Additional) now shows each image's operation and strength. **Use settings** and **Regenerate** are disabled for edited images (they would resubmit the checkpoint as text-to-image without the source and mask; use **Edit image**), and the Images **Edit image** handoff now opens Generate first so its discard confirmation is no longer dismissed by the page switch. A refiner model setting (Draw Things Refiner Model and start %) is not yet supported; it is a candidate follow-up.
- Inpaint **Use result as source** (left Source card) loads the latest saved result as the new source to chain edits, with the usual discard consent for the mask and history.
- The Inpaint mask tint on the source now previews Grow and Edge feather (approximate, from a downsampled grid; the exact mask is still prepared at dispatch).
- Select hover shows each ticked part in its own colour, with the part under the pointer stronger; a click adds or (Alt) removes only that part, while Add to mask / Remove still apply every ticked layer. Layer rows carry a colour swatch and ticked rows are bold on a tint.
- Select: the SegFormer Face class covers neck and chest, so an optional third ONNX file (the 320 px NudeNet-style `FACE_*` detect model, recognised by its contract) limits Face to the head; Face pixels outside each detected face region (up to 15% of the box height below it) become a new `skin` layer that is part of Body. Without that file, Face behaves as before. Checked only for model recognition and a synthetic-image run; real-image quality is not verified.
- The last session now also remembers the Inpaint editor: Text-to-image/Inpaint mode, Select/Paint, the last source image path (reloaded quietly when the file still exists), ticked layers, Strength, Keep-outside, Grow and Feather. The mask and its history are never restored.

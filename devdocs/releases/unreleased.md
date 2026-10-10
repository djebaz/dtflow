# Unreleased

## Highlights

- Smart mask selection and inpainting work is starting in a separate draft PR. Headless preparation, durable input snapshot/restore and tensor-encoding helpers are source-present; request v2 and backed-up DB v3 migration are source-present; provider-dispatch/raw-output/editor integration and owner acceptance are pending. Existing generation behavior is unchanged.

## Added

- Durable image-input snapshot/restore helpers with exclusive attempt-owned sets, bounded manifest/content reads, content and pixel/fit verification; provider-local RGB/mask 0/2 tensor preparation with SHA-256 content references. Not integrated with dispatch; no GUI change, runtime acceptance or inference. Request/DB integration progresses separately below.

- Headless image-operation preparation and immutable content/fit evidence: bounded EXIF/RGB normalization, explicit binary masks, joint target fitting, mask adjustment and source-sized compositing. No dispatch/editor integration or acceptance yet; no tests/validation/inference under fast-implement.

- Standalone CPU-only person/rectangle mask PoC under tools/images, with a dedicated uv development group, model/checksum contract, five diagnostic outputs, explicit TorchVision/FastSeg profiles and focused non-inference tests written before owner requested fast-implement. Real model and target-CPU smoke acceptance remain pending; no production inpainting integration.

## Changed

- Explicit resolved-request v2 and new attempt metadata v2; execution DB v3 with fresh consistent v1/v2 backups and transactional historical txt2img request migration. Preserve output files, identities and existing no-replay/uncertainty reconciliation; unsupported schemas/malformed requests fail closed. Source-present only; owner migration acceptance pending.
- Explicit image-operation refusal in queues/plans and generic configuration/CLI resolution/import/export; no silent loss of inputs/strength. Read-only gallery and queue listing retain supported older DB access. Providers still advertise txt2img only; image dispatch/raw-final saving remains pending.

## Fixed

## Release audit

- PRs: #63, #64
- Scope: Standalone CPU person-zone mask PoC, headless image preparation, durable input snapshot/restore and provider-local tensor encoding; no image-operation dispatch or production acceptance.

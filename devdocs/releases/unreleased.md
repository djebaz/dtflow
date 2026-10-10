# Unreleased

## Highlights

- Smart mask selection and inpainting work is starting in a separate draft PR. Headless preparation, durable input snapshot/restore and tensor-encoding helpers are source-present; request/schema/provider-dispatch/editor integration and owner acceptance are pending. Existing generation behavior is unchanged.

## Added

- Durable image-input snapshot/restore helpers with exclusive attempt-owned sets, bounded manifest/content reads, content and pixel/fit verification; provider-local RGB/mask 0/2 tensor preparation with SHA-256 content references. Not integrated with dispatch; no request/DB migration, GUI change, runtime acceptance or inference in this slice.

- Headless image-operation preparation and immutable content/fit evidence: bounded EXIF/RGB normalization, explicit binary masks, joint target fitting, mask adjustment and source-sized compositing. No dispatch/editor integration or acceptance yet; no tests/validation/inference under fast-implement.

- Standalone CPU-only person/rectangle mask PoC under tools/images, with a dedicated uv development group, model/checksum contract, five diagnostic outputs, explicit TorchVision/FastSeg profiles and focused non-inference tests written before owner requested fast-implement. Real model and target-CPU smoke acceptance remain pending; no production inpainting integration.

## Changed

## Fixed

## Release audit

- PRs: #63, #64
- Scope: Standalone CPU person-zone mask PoC, headless image preparation, durable input snapshot/restore and provider-local tensor encoding; no image-operation dispatch or production acceptance.

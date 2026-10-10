# Unreleased

## Highlights

- Smart mask selection and inpainting work is starting in a separate draft PR. Headless source/mask preparation is source-present; provider/persistence/editor integration and owner acceptance are pending. Existing generation behavior is unchanged.

## Added

- Headless image-operation preparation and immutable content/fit evidence: bounded EXIF/RGB normalization, explicit binary masks, joint target fitting, mask adjustment and source-sized compositing. No dispatch/editor integration or acceptance yet; no tests/validation/inference under fast-implement.

- Standalone CPU-only person/rectangle mask PoC under tools/images, with a dedicated uv development group, model/checksum contract, five diagnostic outputs, explicit TorchVision/FastSeg profiles and focused non-inference tests written before owner requested fast-implement. Real model and target-CPU smoke acceptance remain pending; no production inpainting integration.

## Changed

## Fixed

## Release audit

- PRs: #63, #64
- Scope: Standalone CPU person-zone mask PoC with owner-supplied FastSeg export support; no production image-operation acceptance.

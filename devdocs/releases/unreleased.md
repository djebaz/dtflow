# Unreleased

## Highlights

- Smart mask selection and inpainting work is starting in a separate draft PR. Implementation and owner acceptance are pending; existing generation behavior is unchanged.

## Added

- Standalone CPU-only person/rectangle mask PoC under tools/images, with a dedicated uv development group, model/checksum contract, five diagnostic outputs, explicit TorchVision/FastSeg profiles and focused non-inference tests written before owner requested fast-implement. Real model and target-CPU smoke acceptance remain pending; no production inpainting integration.

## Changed

## Fixed

## Release audit

- PRs: #63
- Scope: Standalone CPU person-zone mask PoC with owner-supplied FastSeg export support; no production image-operation acceptance.

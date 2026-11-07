# Unreleased

Changes through 5.5.0 are consolidated in [DTFlow 5.5.0](v5.5.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

## Changed

## Fixed

- Workflows bind existing prepared custom DT+ LoRA receipts for Generate, Inpaint and Img2img nodes, independently of ordinary Generate's selection. Previously names/weights reached dispatch without their uploaded-content binding, so the cloud request could omit the custom LoRA metadata. No implicit preparation/upload or replay is added; existing account/scope validation stays authoritative. No tests added or run locally at owner direction; CI and real-cloud acceptance remain separate.

## Release audit

- PRs: #238
- Scope: Workflow-only prepared custom cloud LoRA receipt binding at dispatch, shared receipt-helper documentation and workflow guide; no provider uploads or automatic replay.

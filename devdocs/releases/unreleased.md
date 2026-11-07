# Unreleased

Changes through 5.5.0 are consolidated in [DTFlow 5.5.0](v5.5.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

## Changed

## Fixed

- Workflows bind existing prepared custom DT+ LoRA receipts for Generate, Inpaint and Img2img nodes, independently of ordinary Generate's selection. Previously names/weights reached dispatch without their uploaded-content binding, so the cloud request could omit the custom LoRA metadata. Invalid bound receipts stop the affected node before image submission; check the active account and reconcile or explicitly prepare the asset again. Pause is rechecked after receipt binding. No implicit preparation/upload or replay is added.

## Release audit

- PRs: #238
- Scope: Workflow-only prepared custom cloud LoRA receipt binding at dispatch, shared receipt-helper documentation and workflow guide; no provider uploads or automatic replay.

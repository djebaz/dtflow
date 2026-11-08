# Unreleased

Changes through 6.0.0 are consolidated in [DTFlow 6.0.0](v6.0.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Workflows: a global concurrency and an optional per-node concurrency (steps running at the same time), saved in the workflow file and edited in the Workflows inspector. Defaults for DT+, local and segmentation nodes are set in the Connection tab (#286, #287). Smoke-tested by the owner on a real backend; the local default of 2 lanes is the owner's choice and not otherwise verified.

## Changed

## Fixed

## Upgrade and compatibility

- Workflow files gain optional `extra.dtflow.concurrency` and a node property `"DTFlow concurrency"`; files without them load unchanged. `settings.json` gains optional `workflow_cloud`, `workflow_local` and `workflow_segmentation`; an older app version that rewrites `settings.json` drops them.

## Validations

☑️ pytest Full passed
☑️ render_ui passed
☑️ SmokeTests passed

## Release audit

- PRs:
- Scope:

The `## CI build` section (source, artifact, SHA256) is appended by the build-and-release workflow to the draft; keep it in the published body.

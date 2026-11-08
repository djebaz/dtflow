# Unreleased

Changes through 6.0.0 are consolidated in [DTFlow 6.0.0](v6.0.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Workflows: a global concurrency and an optional per-node concurrency (steps running at the same time), saved in the workflow file and edited in the Workflows inspector. Defaults for DT+, local and segmentation nodes are set in the Connection tab (#286, #287). Smoke-tested by the owner on a real backend; the local default of 2 lanes is the owner's choice and not otherwise verified.

- Workflows: Inpaint and Img2img nodes can make several images from each incoming image (1 to 4, "Outputs per image" in the inspector). Each output is its own request with its own seed (node seed + output number), downstream nodes run once per output, and Queues labels them like `image 1.2`. Smoke-tested by the owner on a real backend (#276, #290).
- Workflows: the plan shows the total generations and the saved images separately before Run, and a run over 256 generations in total (all steps count) is refused while it is planned, before anything is submitted. The Queues list, header and progress bar count a workflow run's images instead of one item. Smoke-tested by the owner (#278, #292).

## Changed

## Fixed

## Upgrade and compatibility

- Workflow files gain optional `extra.dtflow.concurrency` and a node property `"DTFlow concurrency"`; files without them load unchanged. Inpaint and Img2img nodes gain an optional node property `"DTFlow outputs"` (1..4, missing = 1); older app versions reject a file that sets it. `settings.json` gains optional `workflow_cloud`, `workflow_local` and `workflow_segmentation`; an older app version that rewrites `settings.json` drops them.

## Validations

☑️ pytest Full passed
☑️ render_ui passed
☑️ SmokeTests passed

## Release audit

- PRs:
- Scope:

The `## CI build` section (source, artifact, SHA256) is appended by the build-and-release workflow to the draft; keep it in the published body.

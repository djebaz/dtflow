# Unreleased

Changes through 6.0.0 are consolidated in [DTFlow 6.0.0](v6.0.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Workflows: Detect face mask now offers Fast NudeNet face ellipse, Balanced NudeNet + YOLO person-clipped connected ellipse, and the two existing Precise face-skin/face+hair methods. New nodes default to Balanced with a 1.6× adjustable face-box expansion. Old workflow files keep their precise method. Model downloads/checks require only the selected roles; downstream Inpaint grow/feather is unchanged (#299, PR #301).

- Workflows: a global concurrency and an optional per-node concurrency (steps running at the same time), saved in the workflow file and edited in the Workflows inspector. Defaults for DT+, local and segmentation nodes are set in the Connection tab (#286, #287). Smoke-tested by the owner on a real backend; the local default of 2 lanes is the owner's choice and not otherwise verified.

- Workflows: Inpaint and Img2img nodes can make several images from each incoming image (1 to 4, "Outputs per image" in the inspector). Each output is its own request with its own seed (node seed + output number), downstream nodes run once per output, and Queues labels them like `image 1.2`. Smoke-tested by the owner on a real backend (#276, #290).
- Workflows: the plan shows the total generations and the saved images separately before Run, and a run over 256 generations in total (all steps count) is refused while it is planned, before anything is submitted. The Queues list, header and progress bar count a workflow run's images instead of one item. Smoke-tested by the owner (#278, #292).
- Queues: selecting an image row of a workflow run and pressing Retry reruns exactly that image's step with its original settings, plus the branches split from it. Sibling images and their saved results stay as they are. The run's summary row still retries its first failed step. Smoke-tested by the owner (#277, #294).
- Queues: a workflow run lists every image it makes under its summary row, and the images a Save node kept are labelled "final" and outlined in blue, so a 2 → 3 → 2 run shows 20 rows with the 12 final ones standing out. Smoke-tested by the owner (#281, #295).

## Changed

- Saved images use readable names in one folder per queue: `outputs/queue-<queue id>/<label>-s<seed>-<attempt id>-1.png` with a matching `<label>-s<seed>-<attempt id>.json` sidecar (label = prompt excerpt, or node and branch in a workflow). Images saved earlier keep their `outputs/<attempt id>/final-1.png` paths and still work in Images, Queues, Export and Trash/Delete; nothing is renamed. See [output layout](../architecture/output-layout.md). Smoke-tested by the owner (#296, #297).

- Generate has one **Images** total. Above 1, Generate image creates a queue of single-image items (seed, seed + 1, ...) so every picture has its own Queues row with thumbnail, seed, status and Retry; behind running work it is added up next. The Queues composer readouts say "N images, N requests". Inpaint ignores the control (#279, #289). Smoke-tested by the owner.

## Fixed

## Upgrade and compatibility

- No stored record changes for the new output layout: attempt records, gallery identity and recycle records are unchanged. Output folders written by this version are not understood as managed originals by an older app version (Trash/Delete refuses them).

- Workflow files gain optional `extra.dtflow.concurrency` and a node property `"DTFlow concurrency"`; files without them load unchanged. Inpaint and Img2img nodes gain an optional node property `"DTFlow outputs"` (1..4, missing = 1); older app versions reject a file that sets it. `settings.json` gains optional `workflow_cloud`, `workflow_local` and `workflow_segmentation`; an older app version that rewrites `settings.json` drops them.

## Validations

☑️ pytest Full passed
☑️ render_ui passed
☑️ SmokeTests passed

## Release audit

- PRs:
- Scope:

The `## CI build` section (source, artifact, SHA256) is appended by the build-and-release workflow to the draft; keep it in the published body.

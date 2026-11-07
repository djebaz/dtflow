# Workflows

Workflows chain generation and local image analysis automatically, as a graph of nodes saved in a ComfyUI-shaped file.
They live in their own **Workflows** tab between Generate and Queues. Issue [#223](https://github.com/djebaz/dtflow/issues/223)
is the V1 specification; the file contract is [architecture/workflows.md](../architecture/workflows.md).

## Building a workflow

- **Library (left):** New starts from the reference workflow (Generate → Detect face mask → Inpaint → Img2img refine →
  Save). Open, Save, Rename, Duplicate and Delete act on `<state>/workflows/library/<name>.json`. Import… accepts only a
  file DTFlow fully understands; anything else is rejected whole with one reason. Export… writes the same ComfyUI v1.0
  structure. The palette adds any node type.
- **Library toggle:** the **Library** button at the start of the canvas toolbar (Ctrl+L) folds the left side (library,
  Add node and Branches) at any window width, so the canvas gets the room; the Generate / Workflows / Queues
  navigation then sits above the toolbar. It is open by default from 1100 px window width and folded below; once you
  press it, DTFlow remembers your choice (`<state>/workflows/workspace.json`). Ctrl+O and Import… show the library
  again. In a small window with the library open, the workflow name moves above the toolbar buttons.
- **Canvas (centre):** wheel zooms, left-drag pans, Shift-drag selects, F fits, Ctrl+Z/Ctrl+Y undo/redo. Fit never
  zooms out below 75 %, so node text stays readable; a larger workflow then shows from its left end (the wheel zooms
  further out). Double-click empty canvas, or press Tab, to open a searchable list of node types; Enter or a click adds
  the chosen node at that point, Esc closes the list. Only matching IMAGE/MASK ports connect, and connections that
  would make a cycle are refused.
- **Inspector (right):** the selected node's settings. It stays visible at every window width. Every generative node
  has its own checkpoint, LoRAs, prompts, seed, steps, guidance and sampler. A value outside the contract (from an
  imported file) is shown read-only with its reason, never clamped. While this workflow's run is live its settings are
  read-only: the run keeps the settings it started with.
- Before running, the inspector shows what is missing or the plan, for example "Ready: 6 generations and 2 local face
  detections". Batches fan out: each Generate image is its own branch and every downstream node runs once per branch.
- **Checkpoints:** the reference workflow's `PLACEHOLDER_…` checkpoints block Run with "Choose a checkpoint for
  <node>". A checkpoint the model list already loaded on Generate does not contain only warns (Run stays available);
  no backend is contacted for this check.

## Running

- **Run** uses the current connection (local, DT+ Cloud or the fake provider). It creates one queue item of kind
  `workflow` and starts it like any queue; while another queue or generation runs it waits its turn. Image steps need
  no extra confirmation: each Inpaint/Img2img node probes its exact checkpoint, and an unsupported checkpoint just fails
  that node with the backend's reason. Cloud generations may use credits.
- **Custom cloud LoRAs:** each Generate, Inpaint and Img2img node binds its selected LoRAs to existing confirmed local preparation receipts at dispatch, independently of Generate's current LoRA selection. Prepare missing custom files explicitly through the existing asset controls; running a workflow never uploads them. Dispatch still validates the active cloud account and receipt availability. A bound receipt that is no longer valid or belongs to a different backend scope fails that node before image submission rather than silently dropping the LoRA. Check the active account and use the existing asset reconciliation or explicit Prepare action to recover; DTFlow does not re-upload or retry automatically. Workflow files store identifiers and weights, not private receipts.
- **LoRA warnings:** when a completed cloud step's preflight could not confirm a selected LoRA's availability and it has no bound preparation receipt, a small amber overlay reports "DT+ availability unconfirmed" and suggests Prepare only if this is a custom LoRA. The warning stays in workflow outputs and the node's run details, and in the Queues right-panel activity log after the overlay expires. Overlays appear while following live or newly started runs, including their completion; opening a historical finished run keeps the retained warnings without replaying the overlay. These are advisories, not proof that a LoRA was ignored or is custom; built-in cloud LoRAs do not require a custom receipt, and Echo may not list them. Warnings never change saved outcomes, automatically prepare assets or retry generation. Diagnostic logs record only the warning code/count and node type, not private receipt identifiers or LoRA names.
- **Detection models:** the face-mask node needs the person segmentation, clothes and faces models. If any is missing,
  Run first shows one confirmation listing each model and its licence, then downloads them from the publisher's pinned
  URL, checks their SHA-256 and ONNX contract and records them like a chosen model. Nothing is downloaded during a run;
  a run with missing models fails before its first submission. Generate's Inpaint editor offers the same
  **Download models…** action.
- **Progress:** the shared activity bar shows the run; each node shows its state, stage and latest image, with
  "n/m images" when it has several branches. Stop (here, in the activity bar or Cancel in Queues) ends the whole run
  at once: DTFlow stops waiting on running steps instead of letting them finish. A step already sent to the backend
  ends unknown, because its image may still complete on the server (a DT+ request may still be billed); it is never
  replayed and Retry sends it again. Steps not yet sent end cancelled. The run settles within a few seconds, even when
  a step is stuck, so the queue can then be removed. Images already saved are kept. There is no stall timeout: a
  stuck step waits until you press Stop.
- **No face detected:** a face-mask node that finds no person, no face box or an empty region pauses only that image
  branch and asks "No face detected. Continue anyway?". Continue skips that branch's inpainting and passes the image on;
  Stop ends that branch. Other branches keep running; an unanswered branch stays paused and submits nothing.
- **Failures:** a failed or unknown step stops only its own branch; the run ends partial. Retry reruns the step with its
  original settings, then its downstream branch (after the run ended, Retry starts a new workflow queue seeded with that
  branch's saved images). Retry is in the Run panel under the canvas, in the selected node's inspector, and in Queues
  on the workflow item (it retries the first failed, then unknown, step). An unknown remote outcome is shown as unknown
  and never replayed.
- **Automatic retry (#241):** before a step is marked failed, it gets one automatic retry a few seconds later, with
  the same settings and seed, when the first try definitely made nothing: a connection, timeout or protocol failure
  where nothing was started or the backend reported the failure, or the Draw Things+ case where the server ended the
  call itself with "unknown error processing request" and no image. The node shows "Retrying automatically" while it
  waits; Stop during the wait sends nothing more. Every other unknown outcome, credentials, quota, validation and
  rejections are never retried automatically. The rule is shared with Generate, Queues and Scripts
  (`src/dtflow/core/retry_policy.py`). A run never submits more than twice its planned total (one automatic retry
  per planned submission) plus explicit Retries.
- **Earlier runs:** the Run panel's **Earlier runs** list shows recent workflow runs; choosing one follows it again
  (steps, Retry, waiting branches), also after a restart. Queues → Open in Workflows does the same for a workflow
  item. The run's own workflow is put on the canvas unless that workflow is already open, or the open one has unsaved
  changes (then only the Run panel follows the run).
- **Outputs:** every step's image is an ordinary saved output in Images with its settings; Save nodes mark the results
  shown in the run card. Masks are kept under `<state>/workflows/runs/<run id>/`.

## Command line

- `dtflow workflow check FILE` validates a file and prints its nodes, links, branches and planned generations, or the
  single rejection reason (exit code 2).
- `dtflow workflow queue FILE -b BACKEND [-e HOST:PORT] [-n NAME]` saves a paused workflow queue; start it with
  `dtflow queue start`.

## Limits

- Structure compatibility with ComfyUI is not execution compatibility: DTFlow nodes do not run in ComfyUI and ComfyUI
  workflows do not import. No ComfyUI frontend release has been pinned or round-trip tested yet.
- Image steps run at the source image size (multiples of 64 within 128–2048).
- The fake provider runs text-to-image only, so image steps fail there with a plain message.
- Detection model licences: YOLO26n is Ultralytics AGPL-3.0, NudeNet 320n is MIT, and the SegFormer B2 clothes model
  is under NVIDIA's SegFormer licence for research use only. SegFormer is planned to be replaced by a model with a
  licence that allows normal use; until then the download confirmation shows its research-only licence.

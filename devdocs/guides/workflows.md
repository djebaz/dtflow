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
  further out). Select a node and press Delete to remove it and its connections; Ctrl+Z restores the deletion.
  Double-click empty canvas, or press Tab, to open a searchable list of node types; Enter or a click adds
  the chosen node at that point, Esc closes the list. Only matching IMAGE/MASK ports connect, and connections that
  would make a cycle are refused.
- **Inspector (right):** the selected node's settings. It stays visible at every window width. Every generative node
  has its own checkpoint, LoRAs, prompts, seed, steps, guidance and sampler. A value outside the contract (from an
  imported file) is shown read-only with its reason, never clamped. While this workflow's run is live its settings are
  read-only: the run keeps the settings it started with.
- Before running, the inspector shows what is missing or the plan, for example "Ready: 6 generations and 2 local face
  detections". Batches fan out: each Generate image is its own branch and every downstream node runs once per branch. Batch size × batch count only sets how many images a Generate makes (2 × 1 and 1 × 2 are the same): each image is sent as its own single-image request with seed = the node's seed + the image's index (image 1 uses the node's seed; the sum wraps at 4294967295).
- **Wildcard prompts (#317):** a Generate, Inpaint or Img2img node can treat its prompt as a template with the same
  syntax as Queues: `{red|blue}` picks one, `{2$$a|b|c}` picks two, `{3::red|1::blue}` weights, and `__name__` takes a
  line of `NAME.txt` (one entry per line, `#` comments) from the `wildcards` folder of the state directory. Select the
  node, set **Wildcard prompt** to *Random sample* (and how many prompts) or *Every combination*; **Show wildcards
  folder** opens the folder, and the preview lists a few draws with the varied parts highlighted. On Generate the
  prompt count is the number of images (it replaces batch size × count; image `i` still uses the node seed + `i`); on Inpaint and Img2img each incoming image
  makes that many images (up to 16, replacing *Outputs per image*), each incoming image with its own random draw. The
  draws are seeded, so the same workflow and seed give the same prompts, and Retry reuses them even if a list changed
  afterwards: creating the run stores a copy of the lists it uses. The images count against the 256 generation limit
  before the run. Each result keeps its resolved prompt and the list versions (SHA-256) it used. A missing list
  or too few variants is refused when the run is created, naming the node and the list.
- **Checkpoints:** the reference workflow's `PLACEHOLDER_…` checkpoints block Run with "Choose a checkpoint for
  <node>". A checkpoint the model list already loaded on Generate does not contain only warns (Run stays available);
  no backend is contacted for this check.

## Starting from a file

**Load image**, **Load image (as mask)** and **Upscale image** (ComfyUI's `LoadImage`, `LoadImageMask`, `ImageScale`)
are in the node library. Choose a PNG, JPEG or WebP file in the node's inspector (**Choose file…**; the node shows the file as its preview until a run replaces it): DTFlow copies it
into `<state>/workflows/inputs/` and the node, and the saved workflow, keep only the file name. Moving the workflow to
another computer shows the name there; choose the file again before running. Mask channels `red`, `green` and `blue`
are used as they are (white = inpaint), `alpha` is inverted (transparent = inpaint), and Inpaint needs a mask the same
size as its image. Upscale image resizes to Width × Height (0 keeps the aspect ratio). Inpaint and Img2img accept any
size: a source that is not a multiple of 64 within 128–2048 is padded into the nearest valid size for generation and the
result is cropped back to the source frame (nothing is trimmed); add an Upscale image node to choose the size yourself.
To resize a mask file the same way, chain Convert mask to image → Upscale image → Convert image to mask (red) with the
same settings as the photo. A file that cannot be loaded, or a mask that selects nothing (an opaque image's own MASK,
the wrong channel), ends the run before anything is submitted. See the
[contract](../architecture/workflows.md#file-nodes-316).

## Running

When editing Generate width/height, arrows step by 64 pixels. Typed values snap to the nearest multiple of 64 on focus loss or before Run (ties upward: 800 becomes 832), within the existing bounds. Imported/saved invalid values remain read-only with their validation reason; they are not silently rewritten.

- **Run** uses the current connection (local, DT+ Cloud or the fake provider). It creates one queue item of kind
  `workflow` and starts it like any queue; while another queue or generation runs it waits its turn. Image steps need
  no extra confirmation: each Inpaint/Img2img node probes its exact checkpoint, and an unsupported checkpoint just fails
  that node with the backend's reason. Cloud generations may use credits.
- **Custom cloud LoRAs:** each Generate, Inpaint and Img2img node binds its selected LoRAs to existing confirmed local preparation receipts at dispatch, independently of Generate's current LoRA selection. Prepare a converted custom file explicitly once through the existing asset controls; DTFlow keeps a verified durable local copy. At dispatch, a workflow validates the active cloud account and each bound receipt. If DT+ positively reports the file missing, DTFlow automatically restores the managed copy once before submitting that node, without repeating a generation. If DT+ verifies identical content under a new reference, DTFlow rebinds without uploading. Unknown/authentication errors, unavailable local bytes and backend-scope changes block before submission with an actionable result. Workflow files store identifiers and weights, not private receipts.
- **LoRA warnings:** when a completed cloud step's preflight could not confirm a selected LoRA's availability and it has no bound preparation receipt, a small amber overlay reports "DT+ availability unconfirmed" and suggests Prepare only if this is a custom LoRA. The warning stays in workflow outputs and the node's run details, and in the Queues right-panel activity log after the overlay expires. Overlays appear while following live or newly started runs, including their completion; opening a historical finished run keeps the retained warnings without replaying the overlay. These are advisories, not proof that a LoRA was ignored or is custom; built-in cloud LoRAs do not require a custom receipt, and Echo may not list them. Warnings never change saved outcomes, automatically prepare assets or retry generation. Diagnostic logs record only the warning code/count and node type, not private receipt identifiers or LoRA names.
- **Face-mask method (#299):** the existing Detect face mask node has one selector: **Fast — Face ellipse**
  (NudeNet only), **Balanced — Person-clipped ellipse** (NudeNet + YOLO person segmentation),
  **Precise — Face skin** or **Precise — Face skin + hair** (existing NudeNet + YOLO + SegFormer).
  New nodes and new library workflows select Balanced by default; saved older workflows retain their
  original precise selection. Fast and Balanced show **Face box expansion** (default 1.6×, range
  1.0–2.5×), scaling the detected face box before drawing an ellipse. Balanced intersects each ellipse
  with its matching person's silhouette, retains the face-connected component and fills enclosed holes.
  **Face** selects Strongest/All NudeNet faces in Fast; **Person** selects Strongest/All YOLO people
  in the other methods. These methods make the same `MASK` output for Inpaint.
- **Mask finishing:** expansion belongs to Detect face mask, while **Mask grow**, **Mask feather**,
  and **Keep outside mask** remain in the downstream **Inpaint** node. They are not applied at detection.
- **Detection model downloads:** each workflow needs only the model roles selected by its Detect
  nodes: Fast → NudeNet; Balanced → NudeNet + YOLO; Precise → NudeNet + YOLO + SegFormer. If any
  required model is missing, Run shows one confirmation with the necessary downloads and licences;
  checks their pinned SHA-256 and ONNX contract and records them. Nothing downloads mid-run; runner
  preflight verifies required roles before any image generation. Generate's Inpaint editor still has
  its separate **Download models…** action.
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
- **Concurrency:** in the inspector, with no node selected, Global concurrency sets how many steps of the workflow run at the same time, all nodes together; each node has its own Concurrency, which can only lower its share. The field shows "Default (...)" until you set a number, and the defaults (DT+ 10, local 2, segmentation nodes such as Detect face 2) are set in the Connection tab under Workflow concurrency defaults. Values you set are saved in the workflow file. DT+ may fail some jobs when many run together; the one automatic retry applies.
- **Failures:** a failed or unknown step stops only its own branch; the run ends partial. Retry reruns the step with its
  original settings (a Generate image reruns alone, with the seed it first used; its sibling images are kept), then its downstream branch (after the run ended, Retry starts a new workflow queue seeded with that
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
  shown in the run card. Face masks are kept under `<state>/workflows/runs/<run id>/`; loaded and converted images once per content under `<state>/workflows/snapshots/`.

## Command line

- `dtflow workflow check FILE` validates a file and prints its nodes, links, branches and planned generations, or the
  single rejection reason (exit code 2).
- `dtflow workflow queue FILE -b BACKEND [-e HOST:PORT] [-n NAME]` saves a paused workflow queue; start it with
  `dtflow queue start`.

## Limits

- Structure compatibility with ComfyUI is not execution compatibility: DTFlow nodes do not run in ComfyUI and ComfyUI
  workflows do not import. No ComfyUI frontend release has been pinned or round-trip tested yet.
- Image steps run at the source image size; a source that is not a multiple of 64 within 128–2048 is padded into the nearest valid size for generation and the result is cropped back to the source frame, so its size and every pixel of it are kept.
- The fake provider runs text-to-image only, so image steps fail there with a plain message.
- Detection model licences: YOLO26n is Ultralytics AGPL-3.0, NudeNet 320n is MIT, and the SegFormer B2 clothes model
  is under NVIDIA's SegFormer licence for research use only. SegFormer is planned to be replaced by a model with a
  licence that allows normal use; until then the download confirmation shows its research-only licence.

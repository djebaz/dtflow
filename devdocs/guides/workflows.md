# Workflows

Workflows chain generation and local image analysis automatically, as a graph of nodes saved in a ComfyUI-shaped file.
They live in their own **Workflows** tab between Generate and Queues. Issue [#223](https://github.com/djebaz/dtflow/issues/223)
is the V1 specification; the file contract is [architecture/workflows.md](../architecture/workflows.md).

## Building a workflow

- **Library (left):** New starts from the reference workflow (Generate → Detect face mask → Inpaint → Img2img refine →
  Save). Open, Save, Rename, Duplicate and Delete act on `<state>/workflows/library/<name>.json`. Import… accepts only a
  file DTFlow fully understands; anything else is rejected whole with one reason. Export… writes the same ComfyUI v1.0
  structure. The palette adds any node type.
- **Canvas (centre):** wheel zooms, left-drag pans, Shift-drag selects, F fits, Ctrl+Z/Ctrl+Y undo/redo. Only matching
  IMAGE/MASK ports connect, and connections that would make a cycle are refused.
- **Inspector (right):** the selected node's settings. Every generative node has its own checkpoint, LoRAs, prompts,
  seed, steps, guidance and sampler. A value outside the contract (from an imported file) is shown read-only with its
  reason, never clamped.
- Before running, the inspector shows what is missing or the plan, for example "Ready: 6 generations and 2 local face
  detections". Batches fan out: each Generate image is its own branch and every downstream node runs once per branch.

## Running

- **Run** uses the current connection (local, DT+ Cloud or the fake provider). It creates one queue item of kind
  `workflow` and starts it like any queue; while another queue or generation runs it waits its turn. Image steps need
  no extra confirmation: each Inpaint/Img2img node probes its exact checkpoint, and an unsupported checkpoint just fails
  that node with the backend's reason. Cloud generations may use credits.
- **Detection models:** the face-mask node needs the person segmentation, clothes and faces models. If any is missing,
  Run first shows one confirmation listing each model and its licence, then downloads them from the publisher's pinned
  URL, checks their SHA-256 and ONNX contract and records them like a chosen model. Nothing is downloaded during a run;
  a run with missing models fails before its first submission. Generate's Inpaint editor offers the same
  **Download models…** action.
- **Progress:** the shared activity bar shows the run; each node shows its state, stage and latest image, with
  "n/m images" when it has several branches. Stop ends the whole run; images already saved are kept.
- **No face detected:** a face-mask node that finds no person, no face box or an empty region pauses only that image
  branch and asks "No face detected. Continue anyway?". Continue skips that branch's inpainting and passes the image on;
  Stop ends that branch. Other branches keep running; an unanswered branch stays paused and submits nothing.
- **Failures:** a failed or unknown step stops only its own branch; the run ends partial. Retry reruns the step with its
  original settings, then its downstream branch (after the run ended, Retry starts a new workflow queue seeded with that
  branch's saved images). An unknown remote outcome is shown as unknown and never replayed. A run never submits more
  than its planned total plus explicit Retries.
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
- No automatic retry yet; it will follow the shared cap decided in #220.

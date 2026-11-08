# Native workflows: file contract v1 (#223)

Status: implementation contract for the V1 workflow feature tracked in [#223](https://github.com/djebaz/dtflow/issues/223).
Decision rationale lives in [the integration audit, section 10](../research/2026-11-04-comfyui-draw-things-integration-audit.md#10-owner-decisions-and-current-v1-findings-2026-11-04).
The code authority is [`core/workflows.py`](../../src/dtflow/core/workflows.py); the reference fixture is
[`resources/workflows/reference_workflow_v1.json`](../../src/dtflow/resources/workflows/reference_workflow_v1.json).

## File shape

DTFlow reads and writes the **ComfyUI saved-workflow JSON v1.0 structure** ([schema](https://github.com/Comfy-Org/docs/blob/main/specs/workflow_json.mdx)),
not ComfyUI's API/prompt format. This is structure compatibility only: DTFlow node types do not run in stock ComfyUI,
and stock ComfyUI workflows do not import into DTFlow.

- Top level: `version` (1), `state` (`lastNodeId`, `lastLinkId`, `lastRerouteId`, `lastGroupid`), `nodes`, object
  `links`, `extra`. `id`, `revision`, empty `groups`, `reroutes`, `config` and `models` are accepted; non-empty ones
  are rejected. Subgraphs (`definitions`) are rejected.
- `extra.dtflow` = `{"schema": "dtflow-workflow", "version": 1}` versions the DTFlow parameter order. A missing,
  different or newer version rejects the file. `extra.ds` (canvas view) and `extra.frontendVersion` are kept, never
  interpreted. `extra.dtflow.concurrency` is optional (#286): an integer 1..10, the most steps of the workflow that
  run together; missing means the backend default (DT+ 10, local 2).
- A node has `id`, `type`, `pos`, `size`, `flags` (`{}` or `collapsed`), `order`, `mode` (0 only), `properties`
  (`{}` or `{"Node name for S&R": type}`, plus an optional `"DTFlow concurrency"` integer 1..10, #286, and on Inpaint and Img2img nodes only an optional
  `"DTFlow outputs"` integer 1..4, #276), `inputs`/`outputs` exactly as the catalogue lists them and
  `widgets_values` as an **array** in the fixed order below (D1). Custom `title`/`color`/`bgcolor` are rejected.
- Links are v1.0 objects; `type` must equal both ports' type; each input has at most one link; both ends must
  reference the link. Array (0.4) links and reroute `parentId` are rejected.

**Concurrency (#286).** The *global concurrency* is the most steps of a run that are in flight together, all nodes and
kinds counted (generations, Detect face, image operations, saves). It is the workflow's `concurrency`, else the default
from the app settings (`<state>/settings.json`: `workflow_cloud` 10, `workflow_local` 2, `workflow_segmentation` 2, edited in
the Connection tab; the code constants in `core/workflows.py` are only the first-run values). A Draw Things session is
widened to that many lanes for the run, so Queues' saved DT+ maximum does not apply to workflows. A node's own
`DTFlow concurrency` can only lower its share (segmentation nodes (Detect face today) default to the segmentation setting, other nodes to the global
value); a step takes its node slot and then one global slot. Both are edited in the Workflows inspector (no node
selected: Global concurrency; a node: that node) and saved with the file; "Default (...)" shows the current default.
Seeds, the loop guard and Retry do not depend on the order outputs finish in. The Queues Concurrency control of a
workflow queue is not used. Whether the local Draw Things app handles two lanes at once is unverified, and DT+ may reject
many parallel jobs (INTERNAL "unknown error processing request").

**Outputs per image (#276).** An Inpaint or Img2img node may make several images from each incoming image. The count is
the optional node property `"DTFlow outputs"` (integer 1..4); a missing property means 1, so every existing V1 file
loads and runs unchanged, and the file omits it when it is 1. It is not part of `widgets_values`, the contract version
stays 1, and it is rejected on any other node type. Each output is its own single request with seed = node seed + output
number (0-based, wrapped at the seed limit); Retry reuses it. Every downstream node runs once per output, and converging
inputs still must come from the same branches. The planned total counts all generations of all stages against the
256 limit before anything is submitted (#278): the check runs as each node is planned, before its branches are built, so
an oversized multiplication is refused without building it. The plan shows the generations and the saved images (the
branches reaching Save nodes) separately, e.g. "20 generations making 12 saved images"; Retry attempts are never part of
the planned total. Queues counts a workflow run's images, not its one queue item, in the list, header and progress bar.

*Branch identity.* The stored branch stays a `[Generate node id, integer]` pair, so run records, controls and Retry
seeds keep their shape. The integer is a mixed-radix path: digit 0 (radix 16) is the Generate image index, each further
digit (radix 5) is the 1-based output number of one multiplying node, so a child never collides with a Generate image or a
sibling at another depth, and a workflow without multiplying nodes keeps plain indexes. The label reads
`Generate #1 image 2.3` (image 2, output 3). The only additive record change is an optional `"branch"` on the values
of a Retry seed (an ancestor branch; missing means the retried branch). Retry of a step reruns that branch and the
branches split from it; for a Generate output that is the one image and what it feeds, also after the run has ended
(sibling outputs are never resubmitted, and the new run plans only the generations in that scope).

A branch names no node, so two unrelated Inpaint/Img2img nodes that both make several outputs from the same images
would produce identical child branches (shared scheduling, outputs paired across nodes). `plan_workflow` refuses that
combination with a clear reason; a multiplying node placed after another one has a longer path and stays valid. No stored
record changes.

In Queues a step that "Continue without a mask" passed through (Inpaint skipped, source forwarded) is a succeeded image
row labelled "passed through, no mask" in the rows, counts, filter chips and log; only the original image row is marked
final. A row's operation comes from its node type, and a seed that was never resolved shows as unknown, not 0. In Queues, Retry acts on the selected workflow image row (a failed or unknown step whose branch is idle); the run's summary row retries its first failed step (#277).

Import is all-or-nothing: the first problem rejects the whole file with one reason. Nothing is repaired, dropped,
renamed or guessed. Values outside a range are rejected, never clamped. A ComfyUI frontend release has **not** been
pinned yet; round-trip behaviour in the ComfyUI editor (unknown node types, `widgets_values_named`) is unverified.

## Node catalogue (contract version 1)

Ports are typed `IMAGE` or `MASK`. Parameter order is the `widgets_values` order.

| Type | Inputs | Outputs | `widgets_values` order |
| --- | --- | --- | --- |
| `DTFGenerate` | — | `IMAGE` | checkpoint, loras, prompt, negative_prompt, seed, steps, guidance, sampler, width, height, batch_size, batch_count |
| `DTFDetectFaceMask` | `image:IMAGE` | `MASK` | mask_region (`face_skin` \| `face_skin_and_hair`), person (`strongest` \| `all`) |
| `DTFInpaint` | `image:IMAGE`, `mask:MASK` | `IMAGE` | checkpoint, loras, prompt, negative_prompt, seed, steps, guidance, sampler, strength, grow, feather, preserve_outside |
| `DTFImg2Img` | `image:IMAGE` | `IMAGE` | checkpoint, loras, prompt, negative_prompt, seed, steps, guidance, sampler, strength |
| `DTFSaveImage` | `image:IMAGE` | — | label |

- `checkpoint` is the exact model file on the run's backend; `loras` is a list of `[file, weight]` pairs, weight
  0.0–3.0, no duplicates; `sampler` is a DTFlow sampler id (`dpmpp_2m_karras`, `euler_a`, …). Ranges and semantics
  (dimensions 128–2048 in steps of 64, at most four outputs, steps 1–150, guidance 0–50) come from
  `ResolvedGenerationRequest`, so a workflow node and Generate share one validator.
- Every generative node owns its own checkpoint, LoRAs, prompts and sampling settings; there is no workflow-wide model.
- Image operations run at the **source image size**. Inpainting hands on the source-sized composite when
  `preserve_outside` is on, so a following refine node receives the generate node's dimensions.
- `DTFDetectFaceMask` requires the segmentation, clothes and faces models (D4). `face_skin` is the selected person's
  SegFormer `Face` pixels (constrained by face boxes); `face_skin_and_hair` adds `Hair`. `strongest` is the highest
  segmentation score; `all` unions every person.
- `DTFSaveImage` marks its image as a workflow result. Every step's image is already a saved DTFlow output.

## Graph and fan-out (D3)

- Graphs are acyclic, of any length or branching; files hold at most 200 nodes and a run at most 256 planned
  generations (practical bounds, not chain limits).
- Only `DTFGenerate` produces several images (`batch_size × batch_count`). Each image is a **branch**
  (`Generate #N image i`); every downstream node runs once per branch. Converging inputs (Inpaint's image and mask)
  must come from the same branches; images from different Generate nodes are never paired.
- A Generate output is its own single-image request (batch 1 × count 1) with seed = node seed + output index, wrapped
  within 0..4294967295 (#275). Outputs run concurrently within the backend's lane limit; a failure stays on its
  output and Retry reruns that output alone with the same seed.
- `plan_workflow` validates a graph for running (all inputs connected, checkpoints chosen, a Save node present,
  settings valid) and returns the planned generation total shown before the run. That total plus the retry allowance
  is the runtime submission guard.

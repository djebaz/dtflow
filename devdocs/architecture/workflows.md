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
  (`{}` or `{"Node name for S&R": type}`, plus an optional `"DTFlow concurrency"` integer 1..10, #286), `inputs`/`outputs` exactly as the catalogue lists them and
  `widgets_values` as an **array** in the fixed order below (D1). Custom `title`/`color`/`bgcolor` are rejected.
- Links are v1.0 objects; `type` must equal both ports' type; each input has at most one link; both ends must
  reference the link. Array (0.4) links and reroute `parentId` are rejected.

**Concurrency (#286).** The *global concurrency* is the most steps of a run that are in flight together, all nodes and
kinds counted (generations, Detect face, image operations, saves). It is the workflow's `concurrency`, else the default
from the app settings (`<state>/settings.json`: `workflow_cloud` 10, `workflow_local` 2, `workflow_detect` 2, edited in
the Connection tab; the code constants in `core/workflows.py` are only the first-run values). A Draw Things session is
widened to that many lanes for the run, so Queues' saved DT+ maximum does not apply to workflows. A node's own
`DTFlow concurrency` can only lower its share (Detect face defaults to the Detect setting, other nodes to the global
value); a step takes its node slot and then one global slot. Both are edited in the Workflows inspector (no node
selected: Global concurrency; a node: that node) and saved with the file; "Default (...)" shows the current default.
Seeds, the loop guard and Retry do not depend on the order outputs finish in. The Queues Concurrency control of a
workflow queue is not used. Whether the local Draw Things app handles two lanes at once is unverified, and DT+ may reject
many parallel jobs (INTERNAL "unknown error processing request").

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
- `plan_workflow` validates a graph for running (all inputs connected, checkpoints chosen, a Save node present,
  settings valid) and returns the planned generation total shown before the run. That total plus the retry allowance
  is the runtime submission guard.

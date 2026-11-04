# ComfyUI + Draw Things: integration and reuse audit

**Date:** 2026-11-04. **Status:** Preliminary, source-backed research; further investigation stopped at the owner's request to preserve the weekly token allowance. This is not the originally requested complete audit or an implementation decision.

**Update (2026-11-04):** Subsequent owner discussion resolved the product direction and V1 boundaries. [Section 10](#10-owner-decisions-and-current-v1-findings-2026-11-04) records those **decisions and current DTFlow source observations**, separate from the original static audit and its still-unverified upstream behavior. See [issue #223](https://github.com/djebaz/dtflow/issues/223) for implementation tracking. The older options and open decision gate below are preserved as historical research.

**Question:** Should DTFlow reuse the official Draw Things ComfyUI extension or ComfyUI execution machinery to support multistep image workflows instead of reproducing Draw Things' implicit canvas/workspace scripting semantics?

## Executive assessment

The official extension establishes a concrete architectural option: ComfyUI owns a graph and Draw Things performs generation through gRPC. Its sampler accepts explicit image/mask inputs and returns images that can feed later operations. This matches the desired direction better than emulating a moving, zoomable virtual canvas. However, the inspected extension is **not a drop-in replacement for DTFlow's generation services**: automatic generation retries, partial-result handling, optional error images, global cancellation state and absent direct DT+ authorization are material differences.

**Recommendation, not an approved plan:** retain DTFlow's generation, authentication, attempt, input-snapshot and saving authorities. Consider either a narrow ComfyUI-to-DTFlow adapter or native explicit image/mask workflow operations. Do not copy the extension wholesale or fork ComfyUI's executor before deciding whether existing ComfyUI graph/custom-node compatibility is actually required. The useful discovery is an existing integration to study, not proof that adopting its whole runtime is the lowest-cost solution.

## 1. Evidence, pins and scope

| Source | Audited identity | Scope |
| --- | --- | --- |
| Official extension | [`drawthingsai/draw-things-comfyui`, `91fe91bc4eb849bc2cadde14e320d274499df45f`](https://github.com/drawthingsai/draw-things-comfyui/tree/91fe91bc4eb849bc2cadde14e320d274499df45f), metadata version **1.14.0**, commit timestamp 2026-10-24T15:21:57-07:00 | Static inspection of handwritten backend/frontend paths, metadata, examples, tests and workflows; generated bindings were not exhaustively audited. |
| ComfyUI core | [`Comfy-Org/ComfyUI`, `5f415e31abfd837870bbc733ba1e158de71ed985`](https://github.com/Comfy-Org/ComfyUI/tree/5f415e31abfd837870bbc733ba1e158de71ed985), commit timestamp 2026-11-04T13:00:11Z | Selected executor, graph, server, requirements and licence sources only; not a complete ComfyUI audit. |
| DTFlow | [`6d8401f022df3ffa209cdfb0d077b700986f9e1d`](https://github.com/djebaz/dtflow/tree/6d8401f022df3ffa209cdfb0d077b700986f9e1d) | Root instructions, roadmap/status/release documents, image/backend/cloud architecture and selected current generation/script/session implementations. Historical prose is not current acceptance evidence. |

The extension was retrieved as source into ignored `tmp/research/draw-things-comfyui-audit-20261104/`; selected ComfyUI files are under ignored `tmp/research/comfy-core-5f415e31/`. These scratch directories are not durable evidence dependencies: the immutable links in this report identify upstream sources. Extension README was read; AGENTS.md and HOWTO.md were absent. Context7 supplied general ComfyUI API documentation, but did not resolve an extension-specific documentation entry; the extension findings below rely on its pinned source.

**Performed:** source retrieval, static reading/search, GitHub metadata inspection and documentation authoring. **Not performed:** dependency installation, application launches, tests, renders, model downloads, uploads, provider requests, generation, training, performance benchmarks or packaged acceptance. No credentials or private runtime state were needed. No billable action was performed.

## 2. What the official extension actually provides

The [pinned README](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/README.md) describes a ComfyUI custom-node extension connecting to the Draw Things application or `gRPCServerCLI`. Its cloud route is the application's **DT+ Bridge Mode**, not DTFlow's native direct-cloud route.

The [sampler interface](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/nodes.py#L50-L159) exposes explicit image and mask inputs, prompts, model, seed, dimensions, strength, LoRA, ControlNet, upscaler, refiner and hints, returning IMAGE/AUDIO ports. This permits graph-level image handoff without upstream JavaScript canvas emulation. It does not establish arbitrary model compatibility, durable step recovery or correctness of every exposed setting.

The three bundled [example workflows](https://github.com/drawthingsai/draw-things-comfyui/tree/91fe91bc4eb849bc2cadde14e320d274499df45f/example_workflows) cover text-to-image, inpainting, and LoRAs/upscaler/refiner. Each contains one DrawThingsSampler. They are examples of ports/composition, **not a supplied, accepted two-generation output-to-input chain**. Upscaler/refiner fields are generation configuration modifiers, not evidence of independently callable image-processing services ([configuration mapping](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/config.py#L153-L174)).

## 3. Fit against DTFlow's needs

| Need | Source finding | Assessment / required boundary |
| --- | --- | --- |
| Explicit multistep image handoff | IMAGE/MASK input and IMAGE output ports exist. | Good architectural fit; a two-step workflow still needs selected offline and owner-authorized live evidence. |
| Direct DT+ without a running Apple app | Inspected transport supplies gRPC/TLS but not DTFlow's token exchange, per-generation authorization or credential selection. Public cloud inventory access is not generation authentication. | Not provided by this extension path; retain DTFlow's direct-cloud adapter. |
| No uncertain automatic replay | GenerateImage retries INTERNAL/UNAVAILABLE once when no image has arrived. | Blocking mismatch: absence of an image does not prove absence of remote execution. |
| Honest terminal completion | INTERNAL/UNAVAILABLE after receiving images can return those results. | Blocking mismatch: partial output plus terminal failure must not become successful durable output. |
| Failure distinct from image data | Optional `blank_on_error` returns a white image containing exception text. | Must not enter successful image chains, galleries or exports. Default false does not remove the integration risk. |
| Per-attempt Stop and concurrency | Cancellation/settings are module-global; cancellation resets before streaming. | Not an adequate substitute for DTFlow's owned queue/attempt controls; concurrent behavior unverified. |
| Durable attempts and provenance | Reviewed extension submission path lacks DTFlow's pre-submission attempt/input commit and final-save transaction. | Comfy output/history is not a replacement for canonical DTFlow execution records. |
| Resource bounds and deadlines | gRPC send/receive message limits are unlimited; inspected Echo/Generate calls have no explicit deadline. | Retain bounded DTFlow policies; do not inherit these transport defaults. |
| Offline authoring and inspect | Frontend performs automatic connection checks; menus can fetch external catalogues. | Importing/configuring a graph is not offline by default, although these paths are not generation. |
| Existing Windows/older-CPU footprint | Extension adds torch/torchvision and another gRPC stack. | Compatibility, startup, memory, executable size and native wheels remain unmeasured. |

Evidence for retry/partial/deadline behavior: [transport and generation loop](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/draw_things.py#L63-L283). Evidence for error-image behavior: [sampler failure path](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/nodes.py#L168-L200). Global ownership: [initialization](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/__init__.py#L1-L20), [settings defaults](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/util.py#L1-L15). These are static findings, not reproduced incidents.

DTFlow's current reference boundaries are [session image dispatch](https://github.com/djebaz/dtflow/blob/6d8401f022df3ffa209cdfb0d077b700986f9e1d/src/dtflow/services/session.py#L367-L436), [pre-submission state through durable success](https://github.com/djebaz/dtflow/blob/6d8401f022df3ffa209cdfb0d077b700986f9e1d/src/dtflow/services/generation.py#L275-L337), [cloud integration](../architecture/cloud-integration.md) and [image preparation/snapshots](../architecture/image-operations.md). Existing protections must remain authoritative; their presence does not certify a new graph integration.

## 4. Image, mask and configuration differences

- **Fitting differs:** the extension resizes to fill the target and center-crops. DTFlow's preparation contract uses recorded centered aspect-preserving fitting/padding and source-grid masks. Directly swapping encoders can change regions and composition ([extension fitting/encoding](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/image_handlers.py#L415-L509), [DTFlow contract](../architecture/image-operations.md#preparation-contract-source-implementation)).
- **Mask threshold differs:** extension byte values below 50 become 0; others become wire value 2 after fitting. DTFlow thresholds luminance above 127 into binary source masks before controlled fitting. Both using 0/2 on the wire does not make their preparation semantics equivalent ([mask encoding](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/image_handlers.py#L512-L574)).
- **Batch handoff is not arbitrary mapping:** ordinary source/mask encoding uses the default batch index 0; a Comfy IMAGE batch must not be assumed to generate independently for every input image ([source preparation](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/draw_things.py#L105-L125)).
- **Seed fidelity needs review:** `int(seed % 4294967295)` maps the upper uint32 value to zero and negative one to 4294967294. This is a source-arithmetic observation, not a run result ([seed mapping](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/config.py#L247-L248)).
- **Falsy defaulting changes explicit zero:** `refiner_start or 0.7` substitutes 0.7 for zero; do not mechanically inherit this configuration mapping ([mapping](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/config.py#L153-L174)).

A DTFlow workflow should record exact parent output identity, selected output index, normalized input identity, fit/crop transform, mask identity, effective settings and child attempt. That is a proposed requirement, not an existing general workflow schema. Current script request construction still rejects masks and constructs the supported txt2img subset ([ScriptHost](https://github.com/djebaz/dtflow/blob/6d8401f022df3ffa209cdfb0d077b700986f9e1d/src/dtflow/scripting/host.py#L244-L314)); this audit does not enable image chaining by itself.

## 5. Frontend, networking and trust

Node addition/configuration/endpoint changes trigger connection checks ([frontend lifecycle](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/web/src/dtModelNodes.ts#L119-L131)). A route fetches the external `kcjerrell.github.io/dt-models/combined_models.json` catalogue ([route](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/routes.py#L65-L77)). Bridge/combined model catalogues have global caches without an expiry/account key in the reviewed path ([catalogue code](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/web/src/models.ts#L349-L430)). These are not sufficient evidence for credential-scoped fresh dispatch authority.

Preserving selected models on disconnect and coalescing metadata queries are useful UX references, not reasons to copy network or ownership policy. A future integration needs explicit permission boundaries for authoring-time network access, source/mask transfer, generation and custom-node code. A workflow/custom-node installation must not be treated as harmless declarative data or as equivalent to DTFlow's bounded JavaScript subprocess.

## 6. Can we reuse ComfyUI's engine itself?

The selected current [executor](https://github.com/Comfy-Org/ComfyUI/blob/5f415e31abfd837870bbc733ba1e158de71ed985/execution.py) imports torch, model/memory management, node registries, caching and application components. The [graph implementation](https://github.com/Comfy-Org/ComfyUI/blob/5f415e31abfd837870bbc733ba1e158de71ed985/comfy_execution/graph.py) also depends on Comfy node interfaces. Therefore extracting a standalone scheduler is an engineering/refactoring project, not a verified lightweight package substitution.

The selected `PromptQueue` implementation stores its queue/history in process lists/dictionaries ([source](https://github.com/Comfy-Org/ComfyUI/blob/5f415e31abfd837870bbc733ba1e158de71ed985/execution.py#L1310-L1456)). This observation is limited to that queue implementation; it is not a claim that every Comfy subsystem lacks persistence. Its history API must not be assumed to supply DTFlow's crash-safe attempt or no-replay guarantees. API submission/progress/history documentation describes integration mechanics, not acceptance of our recovery contract ([official examples](https://github.com/Comfy-Org/docs/blob/main/development/comfyui-server/api-examples.mdx)).

### Additional static findings retained before stopping

- The extension registers routes on ComfyUI's PromptServer singleton, so it is host-dependent rather than a standalone workflow library ([routes](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/routes.py#L7-L13)). Its browser modules depend on Comfy/LiteGraph globals rather than Qt ([frontend integration](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/web/src/util.ts#L121-L139)).
- The inspected request does not opt into chunked outputs; response collection does not assemble chunkState fragments. This is an unsupported optional path, not proof that the default nonchunked path fails ([collection](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/draw_things.py#L185-L258)).
- Final tensor decoding trusts header dimensions/channels and has no DTFlow-equivalent bounded decompression budget in the reviewed helper; stronger preview checks do not establish final-output safety ([decoder](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/image_handlers.py#L41-L72)).
- The node fingerprint represents tensors by shape/dtype, not content. Comfy's graph caching also participates, so this alone does not prove same-shaped images are incorrectly cached. It must not become durable content identity ([fingerprinting](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/src/nodes.py#L35-L51)).
- Code-generation tooling fetches floating upstream content. Any selective reuse needs the committed generated files and their provenance pinned, not an unreviewed regeneration from current main ([generator](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/scripts/genCode.mjs#L5-L49)).

## 7. Dependencies, licensing and validation evidence

The extension declares torch, torchvision, numpy, Pillow, grpcio, FlatBuffers, protobuf, typing_extensions and fpzip. Its metadata has no `requires-python`, and the inspected tree has no Python lockfile ([metadata](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/pyproject.toml)). DTFlow currently uses Python 3.13, uv, a locked dependency set, grpclib and a deliberate NumPy 2.3.5 older-CPU override ([project configuration](../../pyproject.toml)). No compatible combined environment or packaged footprint was established.

The extension root [LICENSE](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/LICENSE) and Python metadata identify GPLv3, while [package.json](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/package.json) says ISC. DTFlow is GPL-3.0-only. This makes reuse worth investigating, but the metadata discrepancy, per-file notices, frontend assets and dependency distribution obligations need resolution before copying/shipping. This is a recorded licensing investigation, not legal clearance.

The extension's checked-in [validation workflow](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/.github/workflows/validate.yml) invokes `comfy-org/node-diff@main`; it is not a Python/Playwright runtime suite. The successful [registry publication run](https://github.com/drawthingsai/draw-things-comfyui/actions/runs/40138246165) does not establish generation acceptance. [Generation tests](https://github.com/drawthingsai/draw-things-comfyui/blob/91fe91bc4eb849bc2cadde14e320d274499df45f/tests/gen.spec.ts#L13-L87) are explicitly gated by RUN_GEN_TESTS and require running services/models; the img2img case is commented out with a Bridge Mode limitation. Other configuration/preview/frontend tests exist, but were read rather than executed. No current Windows, direct-cloud or two-stage compatibility claim follows.

## 8. Decision options

| Option | Value | Main cost/risk | Preliminary verdict |
| --- | --- | --- | --- |
| External ComfyUI + official extension unchanged | Existing graph editor/ecosystem; DT gRPC generation. | Bypasses DTFlow's execution authority and direct-cloud path; source safety mismatches above. | Useful comparison architecture; not an accepted DTFlow integration. |
| External ComfyUI + a narrow DTFlow-owned generation node | Graph authoring while DTFlow owns authentication, inputs, attempts and outputs. | New versioned local service boundary, client identity, stop/uncertainty semantics and image transfer. Ordinary custom nodes remain a separate trust domain. | Strong candidate if Comfy workflow compatibility is the goal; not yet designed. |
| Native DTFlow explicit image/mask operations | Reuse current script/editor/service foundations without a second heavyweight runtime. | Must design dataflow, branching, durable dependencies and eventual visual authoring; no automatic Comfy node compatibility. | Strong candidate if multistep DT workflows, rather than ecosystem compatibility, are the goal. |
| Fork/embed ComfyUI executor | Potential reuse of graph scheduling machinery. | Dependency extraction, maintenance fork and persistence/safety adaptation. | Do not choose without a bounded dependency/ownership feasibility study. |

## 9. Unfinished audit and next decision gate

The owner stopped further research before a complete review of transport codecs/chunk assembly, TLS/certificate and shared-secret behavior, arbitrary workflow/custom-node trust, route security, every frontend setting/migration, dependency closure/licence notices, and full Comfy execution/interrupt/cache semantics. No exploitability, exhaustive correctness or total implementation effort is asserted. A source finding about one path must not be generalized into acceptance or a claim that every backend shares the limitation.

Before implementation, choose **Comfy ecosystem compatibility** versus **DTFlow-native composable workflows**. Then define one minimal contract: generate A, consume a selected committed output in image operation B, retain both attempts and their input relationship, stop dependent admission on failure/uncertainty, and never regenerate A implicitly. Follow with bounded offline fixtures and separately authorized exact-backend/checkpoint live evidence; no such work was run here.

This report does not amend the scripting epic, close Tier B/C issues, change the [TanqueStudio parity matrix](../plans/tanquestudio-parity.md), or approve a replacement engine. It supplies an evidence-backed starting point for that decision while preserving existing work.

## 10. Owner decisions and current V1 findings (2026-11-04)

**Status and provenance:** This is a dated addendum to the preliminary audit above, not a retroactive claim that its upstream investigations were completed. Product behavior described as *agreed* below was chosen in the owner's subsequent discussion; a source observation is identified separately; implementation and compatibility remain unverified unless explicitly supported. [Issue #223](https://github.com/djebaz/dtflow/issues/223) tracks the work. The open choice in Section 9 and the option comparisons in Section 8 describe the **earlier** research state; the choice is now resolved as follows.

### 10.1 Direction: native workflows, ComfyUI-style saved files

- **Agreed:** Build DTFlow's own **visual node editor and execution system** on existing DTFlow services. Do not embed/fork the ComfyUI executor, make ComfyUI an execution dependency, or replace DTFlow's local/DT+ authorization and output ownership with the official extension.
- **Agreed:** Use the **ComfyUI saved/editor workflow JSON v1.0 structure** as the starting file contract: node identity/type, layout, typed links and per-node values. The [official ComfyUI workflow JSON schema](https://github.com/Comfy-Org/docs/blob/main/specs/workflow_json.mdx) is the format reference. It is **not** the separate ComfyUI API/prompt submission format. The precise DTFlow node catalogue, widget/value mapping and import/export semantics need a reviewed reference fixture and schema validation.
- **Agreed V1 compatibility rule:** DTFlow accepts imports **only when the entire file is understood and compatible** with its supported node/port/configuration subset. Reject the whole import with an actionable explanation for any unknown or unsupported node, edge, value or requirement; do not partially import, silently discard data, repair, infer an alternative operation or translate arbitrary Comfy graphs. Exports must follow the chosen saved-workflow structure.
- **Boundary:** A syntactically valid ComfyUI-format document with **DTFlow-specific node types** is **not thereby executable in stock ComfyUI**. Nor is every stock ComfyUI workflow importable into DTFlow. No cross-application execution/round-trip compatibility has been demonstrated.
- **Agreed:** Support long **acyclic** chains and branching graphs, without arbitrary two-, five- or ten-node ceilings. Use typed IMAGE and MASK ports, explicit dependencies and stable per-node/output identities. Resource and concurrency limits are separate practical concerns. The early two-step example was a test case, **not** a product cap.

### 10.2 Reference V1 dataflow and settings

The agreed reference is **generate image → detect face / create its mask → inpaint → img2img refinement → save image**. The generated image is passed to both the detector and the inpainting node; the detector's MASK and the original generated IMAGE converge on inpainting. The inpainting IMAGE is then passed to img2img refinement. This is a representative workflow, not a prescribed maximum length or mandatory node sequence.

```text
Generate [checkpoint A, LoRA set A]
   ├──────────── IMAGE ───────────────┐
   └── IMAGE → Face detection → MASK ─┤
                                     ▼
                          Inpaint [checkpoint B, LoRA set B]
                                     │ IMAGE
                                     ▼
                          Img2img refine [checkpoint C, LoRA set C]
                                     │ IMAGE
                                     ▼
                                  Save
```

- **Confirmed segmentation semantics:** The source [class labels and selectors](../../src/dtflow/services/person_selection.py) define `Face` and `Hair` as **separate SegFormer classes** (`face: (11,)`, `hair: (2,)`). A face-layer mask therefore selects **facial skin classified as Face**, **not hair**. The optional bounding-box face detector constrains/corrects `Face` versus `Skin` classifications; it does not merge hair into the face region. The workflow's default `Face mask` operation must preserve this meaning; face+hair would require an explicit separate composition/selection. Model accuracy and exact visual coverage are not proven by the class names alone.
- **Critical dependency:** The mask **cannot be supplied upfront as though it matched a not-yet-generated image**. It is computed against the actual Step 1 output, after that output exists. Pre-uploaded or manually painted masks for an intermediate generated image are **not** the primary automated V1 path.
- **Agreed:** Each *generative/editing* node—including **inpainting**—has its **own** exact checkpoint, optional multiple LoRAs with individual weights, prompt/negative prompt, seed, steps, guidance, sampler and relevant dimensions/strength/denoising options where supported. These are node-owned settings, not a global workflow model/LoRA set. Validate the exact backend/checkpoint/operation combination instead of assuming compatibility.
- **Agreed:** Carry actual image/mask data and lineage between operations; keep saved outputs inspectable and record the relevant effective settings. Image/mask alignment and transformations matter, given the fitting/polarity differences recorded in Section 4.
- **Source boundary:** Current DTFlow has image preparation, img2img/inpaint dispatch paths and local person-selection services; these are **not evidence** that a native graph executor, arbitrary img2img chain or every model/LoRA/backend combination already works. Retain existing image-operation evidence/permit constraints and verify advertised combinations separately.

### 10.3 Local automatic mask detection and model provisioning

- **Agreed:** Local analysis is acceptable even when generative nodes run on **DT+ Cloud**. Prefer reusing DTFlow's existing CPU-only person/face/body/clothing-selection implementation rather than adding a second detector engine or running ComfyUI.
- **Current DTFlow source observation:** [`services/person_selection.py`](../../src/dtflow/services/person_selection.py) operates on owner-selected ONNX model files. [`persistence/person_models.py`](../../src/dtflow/persistence/person_models.py) stores local paths and SHA-256 hashes, explicitly stating that model weights are **not bundled or downloaded**. The release notes describe local person/body-part/clothing/optional face detection. The equivalent JavaScript [detection hooks](../../src/dtflow/scripting/hooks.py) are still marked **planned** under [#117](https://github.com/djebaz/dtflow/issues/117); do not equate hook declarations with an end-to-end script or workflow implementation.
- **Agreed V1 enhancement shared with Generate:** Provide an approved catalogue of supported detection models and a common mechanism to detect missing local weights, offer/download them from trusted, pinned sources, verify integrity, install/reuse them locally and automatically **load** them when needed. Preserve existing user-selected local model paths. Do **not** invent model URLs, hashes, licences or file sizes; choose and verify them during implementation.
- **Before a workflow starts:** Preflight required detection models and other required assets, then present **one confirmation** for missing-model downloads. Do not begin potentially billable DT+ generation before required local detectors are ready. During ordinary execution, model loading is automatic and no download prompt should interrupt the run.
- **In Generate's native detection feature:** Offer the same missing-model **download/install** action, rather than requiring the user to source ONNX files manually. The common provisioning service should not diverge between Generate and Workflows.
- User-confirmed downloads **before execution** are in scope; silent network downloads mid-workflow, installing arbitrary untrusted models and an extensive model-management platform are not.

### 10.4 Runtime behavior and exceptional human decision

- **Agreed:** V1 is automatic under normal conditions. No ordinary mid-run manual mask painting, upload or other interactive authoring node.
- **Agreed detection exception:** If face/person/body-part detection returns no suitable target, **pause the affected workflow** and show an overlay such as **“No face detected. Continue anyway?”**. **Continue** bypasses mask-dependent inpainting and forwards the unchanged source image only through connections where that substitution is type/semantically valid, with a clear skipped-step indication. **Stop** ends that execution and retains already produced outputs. If nobody responds, it **stays paused**, issuing no further remote generation requests.
- This is a **small exceptional pause/continue requirement**, not a general interactive workflow engine. Failure/uncertainty must be reported against the appropriate node; remote ambiguous outcomes must not be silently replayed.
- **Deferred:** Rich selective re-execution, granular recovery/caching, general interactive pauses for drawing/uploading masks, and extensive conditional logic. Future manual steps should be possible conceptually without being built in V1.

### 10.5 Standalone upscaling, research gaps and evidence boundaries

- **Current source finding:** DTFlow's Draw Things configuration/scripting models expose `upscaler` / `upscalerScaleFactor` fields, and the ComfyUI extension has upscaler/refiner settings, but **no independently callable DTFlow upscale operation has been established**. A sampler configuration modifier is not a standalone workflow operation. Therefore the V1 reference ends with **img2img refinement**, not an asserted standalone upscale step; evaluate upscaling separately after verifying the actual backend and operation.
- **Not yet established:** A reviewed, schema-validated and semantically accepted DTFlow ComfyUI-format fixture; interoperability with real ComfyUI; a built native editor/executor; exact backend/LoRA/inpaint/refinement acceptance; safe external download sources and supported detector accuracy; Windows/package behavior; cloud costs, performance or actual end-to-end image runs.
- **Recommended next research/implementation evidence:** Start with the V1 saved-workflow JSON reference and strict supported-node contract; verify model/LoRA/image-mask handoff against existing services; select licensed/pinned/hash-verified detector artifacts; use focused offline graph/compiler, fake-backend, download and UI-overlay tests. Real local/DT+ generation must be separately authorized and measured. No inference, training, model download or billable provider call was performed for this addendum.
- **Documentation authority:** This audit plus dated findings is the **current research record for the ComfyUI/native-workflow decision**. [`devdocs/plans/initial.md`](../plans/initial.md) contains historical Phase 10A/10B planning context, **not the governing current V1 specification**. [#223](https://github.com/djebaz/dtflow/issues/223) is the actionable product issue; implementation PRs may update operational documents as appropriate without rewriting the historical master plan just to mirror this research update.

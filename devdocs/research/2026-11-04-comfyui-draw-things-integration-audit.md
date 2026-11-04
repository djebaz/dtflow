# ComfyUI + Draw Things: integration and reuse audit

**Date:** 2026-11-04. **Status:** Preliminary, source-backed research; further investigation stopped at the owner's request to preserve the weekly token allowance. This is not the originally requested complete audit or an implementation decision.

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

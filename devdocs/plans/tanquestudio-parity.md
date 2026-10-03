# TanqueStudio port parity matrix

**Status:** Planned inventory; no row claims an implemented DTFlow feature.  
**Purpose:** Track the intended full port without making every feature a prerequisite for Qt.  
**References:** [roadmap](initial.md), [Phase 2](phase-2-backends.md), [Phase 3](phase-3-cli.md), [ecosystem research](../research/dtflow-ecosystem-research-report.md).

## Evidence and maintenance

This is an initial feature-family matrix based on the report's selective upstream inspection, not a claim that every TanqueStudio function has been enumerated. During Phase 2, reconcile the upstream source/UI/docs into a version-pinned feature inventory, adding missing rows before claiming full parity. Record exclusions and their rationale explicitly.

Evidence below refers to report Sections 5.1–5.7 or official backend sources in its appendix. Planned service names describe ownership, not existing Python APIs. App means Draw Things gRPC/Bridge; cloud means direct DT+; fake coverage is offline simulation. Backend support is unknown unless the report records stronger evidence.

A row advances to accepted only with implementation references, actual validation evidence and any remaining limitations. Update this matrix in the PR implementing each feature. Do not silently drop a row because an upstream API is missing: retain a blocked/conditional status and the evidence needed.

## Feature milestones

| Feature | Upstream evidence | Shared service | CLI exposure | Qt interaction | Backend support | Target phase | Acceptance criteria |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Connections, health and auth status | Tanque reliability spec; report 5.5; existing probe | Connection service | Check/select backend | Connection selector/status | App/cloud recorded probe paths | 2 adapters; 3 CLI; 4 Qt | Bounded checks; reliable cleanup; health/auth/inventory distinct |
| Model/file inventory | Echo protocol; client get_models; report 5.6 | Inventory service | List and explicit refresh | Browser/refresh | App/cloud discovery; metadata reliability varies | 3; 4 Qt | Backend/server provenance and timestamp; classify only reliable metadata; unknown files unclassified |
| Configuration fidelity, samplers and dimensions | Tanque gRPC parity spec; report 5.6 | Request resolver | Config/preset plus validated overrides | Parameter forms | Per-backend supported fields only | 2 mappings; 3 CLI; 5 Qt | Defaults, seed zero, sampler enums, dimension units and effective wire parameters tested; unsupported values rejected |
| Single txt2img and output metadata | Existing DTFlow probe; SDK client | Generation/artifact services | Generate/save | Generation panel | App/cloud recorded controlled workload | 2; 3; 5 Qt | Terminal status plus validated artifacts; saved metadata before success |
| Progress and preview handling | Streaming sources; report 4.3 | Event/artifact services | Progress and diagnostic output | Preview/progress panel | Backend signposts; no universal percent guarantee | 2; 3; 5 Qt | Actual events; failed-generation previews labeled diagnostic, never final |
| Interruption/cancellation | Reference server hooks; report 10.5 | Execution service | Interrupt/cancel as supported | Cancel action with honest status | Remote effect unverified | 2 contract; 3; 5 Qt | Local stop distinguished from remote confirmation; unknown outcome retained |
| Durable request/attempt record | DTFlow safety requirements; report 10.6 | Attempt store | Saved record inspection | Later run details | Local durability for all; no remote reattach | 3 | Atomic record before submit; potential execution recorded first; crash tests; no automatic ambiguous retry |
| Basic LoRA selection and weights | Tanque queue axes/parity; report 5.1/5.6 | Request/preflight services | Compatible identifiers and explicit weights | LoRA selector/weights | Combination-specific verification required | 5 | Exact identifiers/weights survive encoding and metadata; incompatible/unknown selections handled explicitly |
| Saved presets and metadata reuse | Tanque config fidelity; report 5.6 | Preset service | Import/export/reuse | Preset browser and reuse | Backend constraints preserved | 8 | Versioned config; no hidden defaults or leaked prior state |
| Gallery, provenance and regeneration | Artifact design; report 4.5; full Tanque gallery inventory pending | Artifact/query services | Inspect/reuse/export | Visual gallery | Local saved artifacts | 8 | Parent/request metadata retained; previews distinguishable; safe regeneration |
| Durable jobs and queue lifecycle | Tanque queue/engine; report 5.1/5.4 | Job/queue services | Submit/list/control | Queue panel | App one effective lane; cloud separately governed | 6 | Persisted transitions; restart paused; explicit Resume/Cancel; resume only definitely unsubmitted eligible jobs; ambiguous submissions never replayed |
| Pair/cross-product batch expansion | RenderQueueExpander; report 5.1 | Pure queue planner | Plan/dry run then enqueue | Matrix controls | Resolved backend-compatible requests | 6 planner; 7 execution | Bounded expansion; stable ordering; pair mismatch warning; explicit resolved requests |
| Backend-specific scheduling | DTFlow measured concurrency; report 3 | Scheduler service | Cloud maximum configuration; later scheduling | Shared maximum; subtle color above two | App/cloud policies separate; cloud default four is user-selected | 3 configuration; 4/5 Qt; 7 scheduling | Positive-integer validation; effective execution mode honest; cap enforced by scheduler; no entitlement assumption; cancellation/uncertainty tested |
| Img2img and canvas fitting | Expander pixel-budget fitting; client init image | Image/request services | File inputs and fit policy | Source preview/canvas | Client expressible; combinations unverified | 9 | Controlled input/model checks; dimensions and preprocessing recorded |
| Inpainting and masks | SDK binary-mask builder; Apple engine behavior distinct | Mask/request services | Existing mask file, polarity/threshold | Interactive mask editing | Per-model/backend verification | 9 | Polarity and alpha semantics tested; drawing stays Qt; no assumed Apple Vision parity |
| Controls and reference/moodboard inputs | SDK hints; Tanque reference state; report 5.2 | Conditioning/preflight services | File references and weights | Reference/control editor | Per-model/backend verification | 9 | Known control types/limits; weighting fidelity; unsupported combinations rejected |
| Upscaling/restoration and chained image operations | Headless capability reference; full Tanque operation inventory pending | Operation services | Verified operations from file inputs | Result comparison and controls | Availability unverified | 9 operations; 10 chains | Verify operation/model contract before exposing; persist inputs/effective params |
| Workflow compilation and preflight | StoryFlowCompiler/RunPreflight; report 5.2/5.3 | Compiler/preflight services | Validate/compile/run files | Workflow editor | Step capability checks | 10 | Clear unsupported instructions; no silent material skips; explicit plan |
| Workflow state, loops, wildcards and approvals | StoryFlowEngine; report 5.4; semantics need pinning | Workflow execution service | Headless execution/control where meaningful | Editor, prompts and visual inspection | Generation steps depend on adapters | 10 | Versioned semantics; scene state isolated; bounded loops; persisted provenance and recovery limits |
| Interactive visual editing and navigation | Tanque Apple UI/framework boundaries; report 5 | Image services for transformations only | File inputs/outputs when useful | Canvas, drag/drop, selection, zoom, layout | Local UI; no server inference implied | 4 shell; 8/9/10 components | Business transforms callable without Qt; UI behavior tested in Qt; not a pre-Qt CLI requirement |
| Cloud LoRA/asset upload | Official MediaGenerationKit LoRAStore guide; endpoint/format details not audited here | Separate asset service | Conditional upload/list | Asset browser/upload | Conditional on verified cloud contract | 11 | Verify upload format/auth/ownership and failure semantics first; do not equate upload with model compatibility |
| Video/audio support | SDK result support; report 10.2; Tanque parity inventory pending | Media generation/artifact services | Verified media jobs/export | Playback/results | Unverified combinations and metadata | 11 capability expansion | Explicit fps/audio metadata; no count/rate heuristics as truth; format compatibility measured |
| Cache visibility and cleanup | DTFlow contract; dts-utils lessons, report 4.5 | Cache service | Inspect/purge/bypass | Cache settings | Local cache distinct from durable artifacts | 3 bypass; 8 management | No-cache disables reads/writes; cleanup never removes durable outputs/models |
| Agent automation/MCP | Ecosystem report 7 | Existing application services | CLI remains supported | Optional integration settings | Same underlying adapters | After 10; explicit demand | No separate execution engine; same validation, billing and persistence rules |

## Phase gates and dependencies

Phase 2 completes the inventory and shared contracts while implementing only the narrow initial adapters and test fixtures. Phase 3 delivers the usable txt2img CLI and minimum durable attempt record. Phase 4 starts Qt promptly; full port parity is not its entry gate.

Later shared-service features should gain CLI exposure alongside implementation where meaningful. Visual painting, canvas manipulation, drag/drop and layout belong in Qt; a CLI should accept the resulting files/configuration rather than imitate interactive gestures.

Cloud uploads and unverified media operations are conditional milestones, not promises of current support. Phase 11 extends the token, secret, billing and error-handling foundations already required in Phase 2.

# Local Compute Units estimate in Generate

**Status:** implementation plan for an unmerged PR. **Scope:** a read-only local estimate; no billing, entitlement, upload, generation or catalogue changes.

## Source of truth and limitations

- The upstream Draw Things community sources are `Libraries/ModelZoo/Sources/ComputeUnits.swift` (blob `b4d64fd012d3d603c04a623fa18943c9fdd15307`), the architecture-specific `Libraries/SwiftDiffusion/Sources/Models/InstructionCount/*.swift` functions and `Sampler.swift` for CFG channels.
- The estimator counts Dense / attention / convolution operations, uses a calibration factor `5.14816e-12` and a model usage policy multiplier. **Do not** substitute `default_scale`, filename quantization, `hires_fix_scale` or an arbitrary per-model coefficient.
- Resolve model technical metadata **only from the existing exact-file `community_model_specs.json`**. `Models_prime.json`, `Models_niche.json` and `model_profiles.json` remain independent. The profile describes DTFlow's actual wire defaults, not cloud availability.
- DTFlow emits a selected subset of Draw Things generation options. Display a **local estimate, not verified server cost**: server policy, versions, final effective configuration and quota may differ.

## Implementation

1. Add an isolated, dependency-free `core/compute_units.py` with the upstream instruction primitives and a **bounded, named set** of currently relevant T2I architectures: FLUX.1, FLUX.2 Dev/Klein, Z-Image, Qwen-Image 2.1, Qwen-Image, Krea 2 and Ideogram 4. All constants derive from the pinned upstream source, without any new merged catalogue.
2. Accept a resolved, effective T2I configuration (model exact filename, size, steps, guidance, known guidance-embed/profile settings). Calculate synchronously with no provider connection, storage write or service worker. Return `None` for unsupported versions/modifiers, missing exact metadata, missing profile evidence when needed, image editing, and uncertain LoRA modifiers. No fuzzy filename match, extrapolation or fabricated zero.
3. Add a short, subdued, full-width label immediately **below** the Checkpoint selector in Generate (without touching the accepted Checkpoint info icon/label styling). Refresh on model, size, steps, guidance and relevant request-mode changes. In cloud mode show `Estimated Compute Units: ~N CU`; for unsupported states show `Compute Units estimate unavailable` with a reason in a tooltip. Local/fake do not imply subscription costs.
4. Tooltip explains the upstream formula, offline derivation and its limits. Never present the result as remaining credits, authorization, exact billing, or a guarantee of request acceptance.
5. Keep the estimate out of the wire request, durable state, billing paths, queue scheduling, preflight and cloud authentication. Preserve the existing Generate behavior.

## Review and validation boundary

- As explicitly requested by the owner: **add no tests and run no tests** (including UI renders, the current CI checks and smoke tests).
- Source inspection and PR diff review only. CI may run automatically; do not manually invoke it.
- Owner Windows smoke: Cloud selection, model and 1024/512 px/step changes, long checkpoint name, missing published spec, local/fake, LoRA selection and Inpaint. Compare a supported result with DT+ UI's estimate separately before calling this server-parity verified.
- No training, inference, model download, billable cloud call or quota check.

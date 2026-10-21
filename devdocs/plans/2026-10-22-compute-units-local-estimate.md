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
3. Show the estimate as a compact **meter** on the Checkpoint label row in Generate › Backend: `Checkpoint` label, the accepted info button (icon/label styling untouched), then the meter filling the rest of the row (`computeUnitsMeter`, a `QProgressBar`; the former full-width label below the selector is removed). Refresh on model, size, steps, guidance and relevant request-mode changes. Cloud only; Local/fake hide the meter and do not imply subscription costs.
   - **Scale:** 0 to 40 000 CU, left to right. Below 40 000 the fill is blue (muted tint of the accent). At 40 000 and above the meter stays full and turns red (muted error tint, number in the danger text colour).
   - **Text:** the number only, left-aligned inside the meter, 8pt. No "Estimated…" label, no "CU", no `~`. Thousands are separated by a narrow no-break space (U+202F), e.g. `100 000`.
   - **Unsupported states:** an empty meter reading `unavailable`, in placeholder colour. Never a fabricated 0. The reason is exposed through `accessibleDescription` only.
   - **Geometry:** 16px tall, `radiusSm` corners, input surface with a 1px border; the label row stays a fixed height so the meter never shifts the layout. Vertical rhythm of the Backend panel: 8px between field rows, 12px above the Checkpoint group with 4px between its label row and selector, the two buttons grouped at 6px, the inventory caption 6px under them.
4. **No hover overlay** (owner decision, replaces the earlier tooltip). The formula, offline derivation and limits live in the Checkpoint details inspector instead (opened from the info button), never as a hover tooltip on the meter. Never present the result as remaining credits, authorization, exact billing, or a guarantee of request acceptance.
5. Keep the estimate out of the wire request, durable state, billing paths, queue scheduling, preflight and cloud authentication. Preserve the existing Generate behavior.

## Review and validation boundary

- As explicitly requested by the owner: **add no tests and run no tests** (including UI renders, the current CI checks and smoke tests).
- Source inspection and PR diff review only. CI may run automatically; do not manually invoke it.
- Render states (`tools/ui/render_states.py`, patched estimate, no provider read): `generate-47` meter at 14 250, `generate-48` 100 000 over the limit, `generate-49` unavailable, plus a hidden-in-local assertion. Owner Windows smoke: U+202F glyph in Segoe UI, a small value still drawing a visible fill, Cloud selection, model and 1024/512 px/step changes, long checkpoint name, missing published spec, local/fake, LoRA selection and Inpaint. Compare a supported result with DT+ UI's estimate separately before calling this server-parity verified.
- No training, inference, model download, billable cloud call or quota check.

# Queues #127 — Markdown reference derived from the authoritative HTML mockup

**Review reference, 2026-10-21.** This document is an *annotated text companion* to the owner's standalone interactive mockup, **`Queues Item Table (standalone).html`**. The HTML's actual arrangement, styles, examples, and hover/selection behavior supersede conflicting choices in the earlier Markdown pre-mockup or plan.

- **Artifact provenance:** owner-uploaded standalone HTML, SHA-256 `b57dffcfafb6ab2ecb49d4264bdabb1f491f561a588b6dc4c87bcea0e0ff3445`. The HTML attachment is **not currently tracked in this PR**. Retain the exact file; do not pretend this Markdown table reproduces its pixels.
- **Evidence boundary:** inspected the bundled HTML/template and its UI state logic; this is neither a real Qt screenshot nor working production behavior. All generations, thumbnails, queue summaries, IDs, timings, and backend labels below are fictional.
- **Implementation boundary:** the application retains its canonical Qt Designer/model-view, durable queue/backend contracts, shared styles, and owner acceptance gates. The HTML controls are interaction **illustrations**, not runnable backend commands.

## Central table — authoritative column order and hierarchy

| Preview | Prompt | Model | LoRAs | Parameters | Status | ⏱ |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: |
| `[thumb A]` | Portrait of a traveler in a blue coat at dawn, soft light, mountains in the distance… | **Z-Image-Turbo_v2.ckpt** | **portrait_style_v1.safetensors** `███░░░░░░░` 0.90<br>**cinematic_rim_light_v3.safetensors** `████░░░░░░` 1.20 | **Steps 28**<br>CFG 4.5<br>Sampler DPM++ 2M SDE Karras | **succeeded** | 31.2 s |
| `[thumb B]` | Portrait of a traveler in a blue coat at dawn, soft light, mountains in the distance… | **Z-Image-Turbo_v2.ckpt** | **portrait_style_v1.safetensors** `████░░░░░░` 1.20<br>**cinematic_rim_light_v3.safetensors** `██░░░░░░░░` 0.60<br>**ultra_realistic_characters_style_detail_v12.safetensors** `█████░░░░░` 1.50<br>**background_bokeh_v2.safetensors** `███░░░░░░░` 0.90 | **Steps 32**<br>CFG 5.0<br>Sampler DPM++ 3M SDE Exponential | **succeeded** | 36.8 s |
| `[no image]` | A red house beside a mountain lake, with cloud reflections spreading across the water… | **PhotoReal_XL-v2.ckpt** | **landscape_style_v2.safetensors** `███░░░░░░░` 0.90<br>**cloud_mood_v4.safetensors** `████░░░░░░` 1.20<br>**texture_detail_v1.safetensors** `██░░░░░░░░` 0.60 | **Steps 24**<br>CFG 4.0<br>Sampler DPM++ 2M SDE Karras | **running** | — |
| `[no image]` | Studio photo of a ceramic cup on a table, with warm light against a pale background… | **PhotoReal_XL-v2.ckpt** | — | **Steps 24**<br>CFG 4.0<br>Sampler DPM++ 2M SDE Karras | **no result** | — |
| `[no image]` | An illustrated old town square after rainfall, with lamps reflected in wet cobblestones… | **Z-Image-Turbo_v2.ckpt** | **portrait_style_v1.safetensors** `███░░░░░░░` 0.90 | **Steps 28**<br>CFG 4.5<br>Sampler DPM++ 3M SDE Exponential | **pending** | — |
| `[no image]` | A lighthouse on a rocky coast at dusk, with waves breaking against dark stone… | **PhotoReal_XL-v2.ckpt** | — | **Steps 24**<br>CFG 4.0<br>Sampler DPM++ 2M SDE Karras | **cancelled** | — |

The table represents the **six synthetic items A–F** in the HTML. Item **B is selected initially** (navy selected-row surface). The synthetic `ultra_realistic_characters_style_detail_v12.safetensors` is **55 characters including `.safetensors`**. This is a deliberately long fixture, not a verified user's actual LoRA or upstream model identifier.

**Important Markdown limitation:** the HTML uses CSS line-clamping, exact column tracks, font metrics, fixed gauges, hover overlays, and a minimum scrollable width. Line breaks and glyph gauges here are schematic, not pixel-accurate; the *HTML* wins on differences.

### Geometry and typographic rules observed in the HTML

| Element | HTML authority / Qt translation target |
| --- | --- |
| Table | Scrollable horizontal surface with **1,000 px minimum content width** in the specimen; seven-column grid; selected-row fill and fine dividers. Do not squeeze content to fit a narrow center. |
| Column tracks | `72px minmax(0,2fr) minmax(150px,1fr) minmax(0,2fr) minmax(120px,0.9fr) 84px 64px` in the HTML grid. Prompt and LoRAs have equal flexible weights; **no hard 30–33% prompt-width rule** remains. Grid gaps are 12 px and horizontal cell padding 12 px. |
| Preview | **64 × 64 px** square containing an aspect-fit saved-image placeholder (no actual images bundled for item results). A neutral `no image` tile otherwise. |
| Prompt | Beginning of **one continuous positive prompt**, natural wrapping, **two visual lines maximum**, clipped on overflow. Not a fixed character count; never place separate ellipses after each line. |
| Model | Checkpoint **filename with extension** (e.g. `Z-Image-Turbo_v2.ckpt`), bold; long names wrap within their allotted cell instead of stretching the table. |
| LoRAs | **All configured LoRAs shown inline, one per line, including the fourth and beyond.** Each row has a single-line elided filename, a **44 px horizontal gauge**, and a right-aligned **34 px numeric value**. No `…` count/extra-items disclosure and no artificial three-LoRA limit. |
| LoRA gauge | Blue accent on a neutral track, with numeric weight retained. The HTML's synthetic gauge uses `min(100%, weight / 3 × 100%)`; the Qt implementation must use a scale justified by the actual weight domain (the app's supported editor range is 0.0–3.0), and not confuse gauge strength with generation progress. |
| Parameters | **Steps** bold; CFG and sampler in smaller secondary text, with full potentially long sampler strings. |
| Status | **Semibold colored text only**, without a pill/badge or added progress. Main sample: succeeded green, running/no result/cancelled amber, pending neutral gray. Actual failure/save-failure state, although absent from the six HTML examples, retains the app's error-red semantic color. |
| Duration | **16 px stopwatch icon only** in the header (tooltip/accessibility name **Backend duration**). Compact, right-aligned monospace durations; unavailable is `—`. Only recorded backend duration, never queue ETA. |

### Read-only hover and selection behavior

| Hover target | HTML behavior | Implementation caveat |
| --- | --- | --- |
| Preview tile | A **220 × 220 px** fixed overlay beside the tile, with placeholder text for saved images or missing outputs. | Real Qt must show an actual bounded **aspect-fit image** only for verified saved outputs; HTML placeholders do not prove file/loading behavior. |
| Prompt cell | **380 px wide**, max **260 px high** overlay showing full **Positive** and **Negative** prompts. | HTML combines `overflow:auto` with `pointer-events:none`, so **interactive scrolling is not established**. Make very long prompts fully accessible without stealing row selection when translating to Qt. |
| LoRA cell | **400 px wide** overlay containing **all LoRAs** in a `LoRA / Weight / Gauge` grid, even though they are already displayed inline. | This replaces the old `…`-only trigger. Each inline elided filename also has the full identifier as a tooltip. |
| Entire row | Click selects the item; inspector updates to selected item's evidence. Hover overlays are read-only and `pointer-events:none` in HTML. | Preserve click and keyboard selection in the Qt item view; overlays cannot swallow selection or imply editing. |

The HTML places hover overlays near the target and constrains their screen coordinates. In a real Qt viewport, validate edge collision, tracking while scrolling, timing, keyboard accessibility, and selection preservation; do **not** claim those have been tested merely because the bundled HTML specifies handlers.

## Right inspector — selected synthetic item B

The HTML's inspector is headed **ITEM EVIDENCE** and displays **three information sections, conditional action buttons, then the existing creation mini-log**. It contains **no image preview, no saved-output filename**, and no substitute content when the right rail is hidden.

| Section | Field | Item B sample | Treatment |
| --- | --- | --- | --- |
| Selected item | Item | `demo-item-b-002` | Selectable monospace ID |
| Selected item | Attempt | `demo-attempt-b-01` | Selectable monospace ID |
| Selected item | Backend | **Draw Things (example)** | Semibold evidence |
| Selected item | Outputs | 1 saved image | Only when actually verified |
| Additional information | Seed | `295625551` | Selectable monospace |
| Additional information | Image dimensions | **1024 × 1024** | Secondary evidence |
| Additional information | Operation | Text to image | Secondary evidence |
| Technical details | Outcome | No error recorded | Recorded, not inferred |
| Actions | Open in Images | Enabled in synthetic item B | Only for verified gallery identity |
| Actions | Open folder | Enabled in synthetic item B | Only with accessible valid path |
| Actions | Retry item | **Disabled** by fixture default | Real availability follows existing no-replay/retry safeguards |

**Creation mini-log (synthetic, selected item B):**

| Time | Event |
| --- | --- |
| 19:42:01 | Item accepted into queue |
| 19:42:02 | Attempt started on example backend |
| 19:42:39 | Attempt completed; result saved |

**Conditional examples:** Running C has no saved image and no available Open folder/Retry; No result D has no image/folder but Retry is enabled; Pending E has none of those actions; Cancelled F has Retry enabled but no saved image. These are **HTML fixture flags**, not proof of real eligibility logic or billing safety.

## Surrounding shell and responsive arrangement

The HTML shows the **left Queues rail**, **center queue heading/summary/controls/progress and item table**, and **right Item Evidence inspector**. For its illustrative responsive treatment, the inspector **hides below 1,100 px window width** with no details fallback. The center remains horizontally scrollable; the mockup uses a **272 px left rail when wide and 220 px when narrow**. The canonical Qt shared-shell constraints and dispatch semantics still need reconciliation rather than blindly transferring all placeholder geometry or nonfunctional buttons.

**Mock-only discrepancies requiring care:** the HTML's illustrative `Max at once` spin box is capped at 4, whereas the existing Qt/service contract allows 1–10; do not silently reduce working concurrency. The simulated queue-header `Running` badge is amber, while item `running` is amber and existing queue-level mapping can differ. The six-item sample has **no failed row**; include failed/save-failed cases in Qt render-state fixtures without changing the approved visual language.

## Superseded decisions from the earlier Markdown draft

The previous **three-inline-LoRA maximum, extra-items ellipsis, one-third fixed prompt width, 64 × 48 speculative thumbnail, and a merely hypothetical inspector section layout** are **not authoritative**. Use the HTML's all-inline LoRA pattern, equal flexible Prompt/LoRAs tracks, 64 × 64 preview and three-section inspector instead.

This revised document and the [implementation plan](../plans/2026-10-21-queues-item-table-render-first.md) record the comparison. They **do not** constitute Qt implementation, live data wiring, test execution, Windows/DPI acceptance or billable generation. The next deliverable is a **Qt render-state translation of this HTML**, followed by explicit owner review before integration.

[Draft PR #150](https://github.com/djebaz/dtflow/pull/150) · [Issue #127](https://github.com/djebaz/dtflow/issues/127)

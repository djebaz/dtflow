# Queues item table: current Qt presentation reference

Updated 2026-10-21 for PR #150. The [tracked standalone HTML](Queues_standalone_mockup.html) is the original visual reference; [later owner decisions](../plans/2026-10-21-queues-item-table-post-mortem.md#2-decisions-made-during-review-owner-directed) and the [reconciled delivery plan](../plans/2026-10-21-queues-item-table-render-first.md) supersede conflicting early specimen details.

| Region | Current Qt behavior |
| --- | --- |
| Preview | 64 px centered square crop, asynchronous gallery-backed thumbnail; aspect-fit mouse-transparent hover up to 440 px. Missing/excluded results are unavailable, not arbitrary historical-path reads. |
| Prompt | Two visible wrapped positive-prompt lines; full positive/negative Qt tooltip, read-only. Tooltip scrolling is not implemented. |
| Model | Bold extension-less display name; recorded identity remains unchanged. |
| LoRAs | First three inline with gauges and numeric weights; whole-cell tooltip lists every LoRA without a +N chip. Display names hide extensions. |
| Gauge | One scale across rows: highest recorded positive weight rounded up to 0.5, minimum 1.0. Not progress or a compatibility statement. |
| Parameters | Recorded steps, guidance and wrapped sampler. Missing evidence is unavailable, not guessed. |
| Status | Explicit semantic text preserved on selection. No-result is distinct from failed/save-failed. |
| Duration | Painted stopwatch header; compact right-aligned observed backend duration or an unavailable marker. Never ETA/inference-time substitution. |
| Inspector | Selected identity/backend/output summary and actions; Additional information holds seed/dimensions/operation/outcome. Both item cards hide while composing. |
| Open in Images | Gallery-backed mapped availability; clear conflicting Images filters, locate exact live output and open Detail. Missing identity produces an inline warning; no generation. |
| Activity | Existing compact event stream retained; item-click/collapse interactions are not added in this slice. |
| Narrow shell | Existing inspector breakpoint retained without a new fallback. Minimum table column widths may require horizontal scrolling; Prompt targets about 30% in Qt. |

The thumbnail cache is identity-keyed, revalidates visible entries every three seconds and clears across navigation. Owned timers stop at shutdown, and obsolete work cannot revive a previous selection. These are source contracts, not measured native performance or cross-platform acceptance.

Implementation geometry: rows are 76 logical px high; the 64 px tile uses a 128 px backing pixmap. Hover preview waits 350 ms before appearing. These values describe the current Qt presentation, not measured DPI acceptance.

Offline fixtures: `queues-60` through `queues-64` describe the original table/hover states; `queues-65-composer-without-old-item-evidence` and `queues-66-item-table-unavailable-output` define the corrections. Added definitions are not screenshots until captured. No real generation, upload or provider work is needed to render them.

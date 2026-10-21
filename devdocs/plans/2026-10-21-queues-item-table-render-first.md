# Queues #127: item table and inspector delivery

**Updated:** 2026-10-21. **PR:** [#150](https://github.com/djebaz/dtflow/pull/150), implementation and review corrections; not design-only. Parent [#130](https://github.com/djebaz/dtflow/issues/130).

## Current authority and scope

The owner-provided [standalone HTML](../mockups/Queues_standalone_mockup.html) supplied the original visual direction. Later owner decisions recorded in the [implementation post-mortem](2026-10-21-queues-item-table-post-mortem.md#2-decisions-made-during-review-owner-directed) supersede conflicting HTML and earlier Markdown choices. The old [Queues UI kit](../design-system/ui_kits/queues/QueuesScreen.jsx) is historical, not the accepted item-table design.

Current source implements the selected existing queue's seven-column table, right inspector, asynchronous thumbnails and Open in Images handoff. It is not the New queue composer preview. No scheduler, retry/replay, reservation, state schema, backend protocol or billable-generation behavior changes belong to this slice.

## Accepted presentation decisions

- Columns: Preview, Prompt, Model, LoRAs, Parameters, Status and an icon-only painted stopwatch header. Keep the existing stable-identity Qt model/view and canonical Designer form; do not create per-row widget trees.
- Tiles: 64 px centered crops of the gallery thumbnail; aspect-fit hover up to 440 px, read-only and mouse-transparent. Synthetic captures never imply generated/saved results.
- Prompt: two visible wrapped lines with full positive/negative tooltip. Long tooltip scrolling remains a known limitation, not an accepted native behavior claim.
- Model/LoRA display names hide weight-file extensions. Show three inline LoRAs, all entries in the whole-cell tooltip, no extra disclosure chip. Gauges share the largest recorded positive weight rounded up to 0.5, minimum full scale 1.0.
- Status keeps the approved semantic text color even when selected. Duration is recorded backend wall time, may include provider waiting and is never inferred from ETA.
- Prompt targets about 30% of the Qt table; minimum column widths permit horizontal scrolling. Keep the shared inspector auto-hide breakpoint and the owner's decision not to add a narrow inspector fallback.
- Selected item/actions plus Additional information (seed, dimensions, operation, outcome) remain separate from the New queue summary. Activity retains the compact recorded event stream; clickable/collapsible activity is not implemented by this PR.

## Full-review corrections authorized by the owner

1. Require a matching live committed gallery record before decoding a thumbnail. Excluded, recycled/deleted, missing or unreadable records never authorize fallback to an old filesystem path.
2. Key previews by recorded path and gallery identity. Revalidate visible entries every three seconds through the gallery read/lease/cache boundary, clear them on hide/queue change/re-entry, and display unavailable results honestly. This is bounded polling, not continuous file monitoring or measured performance.
3. Parent refresh/retry timers and the hover widget to owned QObjects/widgets; cancel on navigation/shutdown and drop obsolete results. Clearing cannot reset actual in-flight job counts; at most three decodes remain admitted.
4. Open in Images clears conflicting browse filters, requests the exact live identity and shows an inline warning if unavailable. Snapshot browsing cannot authorize this handoff. Preserve latest request intent while waiting for an existing read, and cancel it on changed intent/navigation/shutdown.
5. Gate the action using shared gallery-backed, mapped availability, not GUI-thread `Path.is_file()` on a foreign recorded path. Do not invent reverse-runtime path support.
6. Hide the Additional information card while composing, alongside the old item identity/activity evidence.

## Ownership and implementation boundaries

- `persistence/queues.py` projects recorded requests and settlement duration; no new durable schema or provider access.
- `ui/queue_presenter.py`, `queue_model.py` and `queue_item_delegate.py` own bounded row presentation. The live gallery lookup and original-file checks run off the GUI thread.
- `queue_thumbnails.py` reuses gallery `_image` and shared disk-cache/no-cache policy; it creates no competing persistent metadata or cache authority.
- `gallery_page.py` owns identity-directed Images navigation and its latest-request lifecycle; `queue_page.py` owns selection/composer presentation only.
- Canonical `queue_page.ui`, shared QSS/tokens and the existing renderer remain the presentation authority. Tokens are unchanged.
- Dev-mode observability uses shared structured diagnostics: bounded thumbnail outcome/timing summaries and terminal Images-handoff outcomes. See the [diagnostics ownership guide](../architecture/structured-diagnostics.md#ownership-and-dependency-boundaries). No prompts, identities, paths or raw error text are added; these additions still need their own CI evidence.

## Progress and evidence

| Checkpoint | Current evidence |
| --- | --- |
| Plan PR and owner design decisions | Completed; original plan is retained in Git and the historical post-mortem. |
| Qt translation / source integration | Implemented. Historical owner report: Queues captures and one application run looked good; no exhaustive Windows/DPI or performance acceptance inferred. |
| Initial suite repair | `ebe171e`: CI 39675079765 passed 629 tests, 3 skipped; Ruff return codes 0. Existing routing/state-column tests were updated, not proof of new interactions. |
| Six review corrections | Source implemented after explicit owner authorization; focused existing contract tests extended. Correction head `408d734` passed [CI 39678409026](https://github.com/djebaz/dtflow/actions/runs/39678409026): 636 passed, 3 skipped; Ruff lint/format return codes 0. Later documentation and dev-diagnostics edits are not covered by that head's run. |
| Preview coverage | Existing states 60–64; definitions 65 (composer without old evidence) and 66 (unavailable output) added. New captures not executed locally. |
| Local validation | Syntax/lint/format/whitespace and documentation-target checks only; no local pytest or renders. |
| Native/platform acceptance | Corrected navigation/removal/mapped-path/shutdown interactions and performance still need owner validation; no inference or provider actions authorized. |

## Remaining gates

- [x] Inspect exact-head CI for correction head `408d734` (linked above); do not equate skipped UI renders with layout acceptance or extend that result to later commits.
- [x] Owner reviews corrected interactions; request fresh offline captures only if wanted, then native smoke as appropriate.
- [x] Reconcile #127/#130 closure with accepted delivered scope. Do not claim clickable activity, scrollable prompt tooltips, broader backend compatibility or exhaustive DPI/fault acceptance.
- [x] Obtain explicit merge approval. This task does not merge, build, tag or publish.

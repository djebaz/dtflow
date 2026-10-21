# Queues #127: render-first item table and inspector redesign

**Date:** 2026-10-21  
**Status:** Owner-supplied standalone HTML accepted as the authoritative visual/interaction mockup; Markdown specimen and implementation plan reconciled to it. Qt translation and production integration remain unapproved and unverified  
**Parent epic:** [#130](https://github.com/djebaz/dtflow/issues/130)  
**Feature issue:** [#127](https://github.com/djebaz/dtflow/issues/127)

## Owner workflow and approval gates

Work in **one dedicated draft PR**, with coherent commits pushed at each approved checkpoint. The plan was the first commit. The initial owner-requested **text-only Markdown pre-mockup** established the information hierarchy; the owner subsequently supplied a **standalone HTML mockup** and explicitly made it authoritative where its layout, interactions, or hierarchy differ from that earlier draft. The Markdown companion now documents observed HTML behavior, not an independent design proposal. This does **not** mean that equivalent Qt behavior has been implemented or visually accepted on Windows.

1. **Plan first.** Commit and push this plan and open the PR. No feature code, tests or UI renders in this checkpoint.
2. **Documentation alignment (current checkpoint).** Inspect the owner-supplied HTML's actual component structure, sample values, layout, and interactions, then update the [derived Markdown reference](../mockups/2026-10-21-queues-item-table-text-preview.md) and this plan. The HTML attachment is the authoritative design source; no Qt render, tests, real data, or implementation in this step.
3. **Qt translation mockup only.** Reproduce the owner-supplied HTML's reviewed design in canonical Qt Designer forms, shared tokens/QSS, and `tools/ui/render_states.py` with synthetic offline fixtures. Render actual Qt screenshots; do not wire the proposed controls to live queue state. Do **not** create another independent HTML design or silently revert to superseded Markdown choices.
4. **Owner visual approval is mandatory.** Present a named screenshot set and an explicit list of what is mock-only. Capture feedback and revise the mockup until accepted. Do not begin real data integration on implied approval.
5. **Implement the approved layout.** Connect it to the existing selected-queue read/presenter/gallery boundaries without changing dispatch, reservations, schema or billable generation behavior. Commit/push this slice for owner functional review.
6. **Owner functional approval is mandatory.** Review the real desktop behavior (especially native Windows layout and interaction) before starting test additions, running tests, lint/format gates, or general documentation updates.
7. **Only after functional approval:** add/adjust the smallest authoritative tests, run agreed validations, update applicable user and developer documentation plus release notes, record evidence and prepare the PR for merge. Update **this plan alone** earlier when scope, decisions or checkpoint outcomes change.

**Before functional approval:** do not add or run pytest/other test suites, or independently run lint/format/packaging/CI workflows. Only this plan and the owner-requested fictional Markdown specimen are permitted early documentation changes; no unrelated documentation edits. Running `render_states.py` in the Qt mockup stage is an explicitly requested **visual capture**, not a claim of test, performance, Windows/DPI or end-to-end acceptance. Opening/pushing a PR can trigger repository-managed CI automatically; distinguish that from tests explicitly invoked by the implementer. Never run real generation, upload, training, or potentially billable operations for this task.

## Current source audit (before implementation)

Source inspected on the default branch on 2026-10-21; these are **code observations**, not fresh native runtime or screenshot validation.

- `src/dtflow/ui/forms/queue_page.ui` owns the central `queueItemsView`, right `queueInspector`, selected-item box and activity browser. Keep Designer as the layout authority.
- `src/dtflow/ui/queue_model.py` is already a `QAbstractTableModel` with stable order and in-place row updates; its current columns are `# / Item / Seed / State / Attempt / Outputs / Edit`. No separate table framework is required.
- `src/dtflow/ui/queue_presenter.py` currently exposes `ItemRow` with item identity, seed, state, attempt identity, output paths, metadata path, failure code, operation and edit strength. The current `event_lines()` renders a compact durable activity stream, including recorded successful settlement duration when available.
- `src/dtflow/ui/queue_page.py` polls summary rows for all queues and reads items/events only for the selected queue; it preserves per-item selection through `QueueItemModel`. The right inspector currently shows shortened item/attempt IDs, backend, output summary, **Open folder** and **Retry item**; it has no implemented image preview or Open in Images handoff.
- `tools/ui/render_states.py` already has `render_active_queue_states()` and named queue states such as `queues-16-item-states` and `queues-17-item-inspection`, with synthetic read views and no actual dispatch. Extend these rather than create a second screenshot harness.
- The repo's `DESIGN.md` and `AGENTS.md` require Qt Designer, shared style tokens/QSS, offline fixture renders for UI changes, bounded asynchronous image handling, honest outcome text and no work on the Qt event loop.
- The [older Queues UI kit](../design-system/ui_kits/queues/QueuesScreen.jsx) is historical and still depicts the ID-heavy table; it is **not** an approved #127 mockup.

Unverified until the integration audit: the precise selected-item request/plan-metadata read API; the authoritative mapping from queue output identities to committed Images gallery identities; whether duration already exists on the selected-item projection; native Windows performance and width behavior.

## Design reference and research-review findings (2026-10-21)

The owner selected **Images → Detail** as the information-priority reference and **Images → List** as the thumbnail, compact row and styling reference. The intermediate prototype is Markdown only, not HTML. The current implementation is in [`gallery_page.py`](../../src/dtflow/ui/gallery_page.py), the canonical [Images Designer form](../../src/dtflow/ui/forms/gallery_detail_page.ui), and shared [QSS](../../src/dtflow/resources/styles/dtflow_dark.qss).

- **Reuse visually:** a compact, image-led row with the user's **current Images → List thumbnail setting** as the sizing reference; about **64 × 48 logical pixels** is illustrative, not a mandated fixed size, restrained cell spacing and separators, row-wide hover/selection states, readable value hierarchy, and familiar navigation to Image Detail. These are *proposed starting dimensions and styling*, not approved measurements or runtime results.
- **Adapt semantically:** a two-line positive prompt, repeated **checkpoint filenames**, repeated **LoRA filenames** with weight gauges, steps, CFG, sampler, state label and recorded backend duration are primary; output filenames, seed, dimensions, progress and internal hashes are not separate central columns. Retain a separate explicit state and recorded duration column; missing or pending output uses a neutral placeholder, not a fabricated preview.
- **Do not copy structurally:** Images List currently uses `QTableWidget`, while Queues already owns a model-backed `QTableView`. Keep `QueueItemModel`, queue selection/filters, bounded asynchronous image delivery and queue-specific controls. Do not bring gallery bulk checkboxes, export/delete actions or a second gallery controller into Queues.

### Owner decision overriding the earlier P2 narrow-details proposal

The earlier P2 reviewer recommendation was an **inline Details fallback** when the shared shell hides the right inspector below **1100 logical px**. The owner explicitly chose **not to add any replacement or relocated inspector content** at narrow widths. The right inspector holds complementary, non-essential information only; the central table must remain the primary readable surface.

The owner also rejected the proposed large inspector image: **show thumbnails in central rows and enlarged read-only images on hover; do not show an image in the inspector**. Clicking any central row selects it and updates the visible inspector. Hover overlays are informational and must not intercept row selection.

These are owner-approved placement/interaction constraints, not approval of a rendered layout or production implementation.

### Review finding P3 — broken reference

The older Queues UI-kit relative link was corrected from `../../design-system/...` to `../design-system/...` relative to this file in `devdocs/plans/`. No other documentation is changed at this checkpoint.

**Review provenance:** these P2/P3 issues were reported in a reviewer screenshot supplied by the owner on 2026-10-21. The review explicitly characterized them as plan issues and reported no edits, tests, renders or workflows. They are not evidence of a reproduced production bug.

## Design authority and observed HTML differences (2026-10-21)

**New source of design truth:** owner-uploaded \`Queues Item Table (standalone).html\`, SHA-256 \`b57dffcfafb6ab2ecb49d4264bdabb1f491f561a588b6dc4c87bcea0e0ff3445\`. Its bundled markup, styles, fictional data, and interaction handlers were inspected. **The HTML file is not currently tracked in PR #150; keep the exact original artifact available separately**. The [derived Markdown table and inspector reference](../mockups/2026-10-21-queues-item-table-text-preview.md) is secondary, not a replacement for it. No browser runtime, Qt render, native Windows interaction, or inferred backend execution is claimed from source inspection.

**Priority order for this feature:** owner-supplied HTML for layout/visual hierarchy/read-only hover/row-selection intent; canonical Qt/shared design system for native implementation and accessibility; durable queue/services contracts for operational truth and safety. Resolve inconsistencies explicitly rather than copying illustrative widgets or backend assumptions from an HTML fixture.

### Main differences from the former Markdown proposal

| Concern | Earlier Markdown / plan | Owner HTML now authoritative |
| --- | --- | --- |
| LoRA list | Maximum **3** inline, plus \`…\` hover for all | **Every configured LoRA inline**, one row each, with elided filename, **44 px weight gauge and 34 px numeric value**; hover on the **whole LoRAs cell** reveals all entries; no \`…\` control. |
| LoRA weight scale | Await verification | HTML illustrates \`min(100%, weight / 3 × 100%)\`; respect the actual 0.0–3.0 supported editor range and preserve exact numeric weight. Do not fabricate strength from generation progress. |
| Prompt/LoRA width | Prompt fixed around one-third of table | **Prompt and LoRAs equal flexible 2fr tracks** inside the specimen's seven-track grid; no hard percentage goal. One naturally wrapped positive prompt is line-clamped to **two visible lines** and only truncated at the end; complete positive/negative text on hover. |
| Thumbnail | Speculative 64 × 48 | **64 × 64** square tile (aspect-fit content for actual saved outputs), with **220 × 220** hover overlay. The HTML contains placeholder text, **not real generated images**. |
| Status fixtures | Succeeded/Running/Failed/Queued | **Six HTML items:** two succeeded, one running, one **no result**, one pending, one cancelled. Semibold colored **text only**. Failed/save-failed cases must still be represented by real app error-red semantics even though missing in sample. |
| Table width | Fit/clip in responsive center | **Minimum 1000 px scrollable table content width**, preserving column tracks and permitting horizontal scroll in a narrow center. |
| Inspector | Conceptual fields/sections, arrangement deferred | **ITEM EVIDENCE** panel; **Selected item** (Item, Attempt, Backend, Outputs); **Additional information** (Seed, Image dimensions, Operation); **Technical details** (Outcome); then conditional actions and creation mini-log. |
| Hover | Overlay trigger for thumbnail/prompt and extra LoRAs | Fixed **nonblocking overlays** on thumbnail, prompt, and **entire LoRAs cell**. Preserve row click. HTML's \`pointer-events:none\` prompt popup cannot establish scrollable long-text interaction despite \`overflow:auto\`; solve access to full long text in Qt rather than copying that inconsistency. |

### Central table: confirmed content and layout

**Seven columns in order:** **Preview | Prompt | Model | LoRAs | Parameters | Status | stopwatch icon (backend duration)**. No generation-position, seed, saved-output filename, item ID, attempt, output count, or operation column in the center.

| Column | HTML reference | Qt-specific behavior to preserve |
| --- | --- | --- |
| Preview | 64 × 64 dark square with neutral placeholder; hover popup 220 × 220. | Bounded, asynchronous cached image rendering for actual committed output only; aspect-fit; never imply missing result is saved. |
| Prompt | Dedicated flexible **2fr** track, one continuous positive prompt, **two lines** maximum. | Clip by rendered pixel metrics, not per-line character count; full positive/negative hover; no click interception. |
| Model | Bold **checkpoint filename with extension**, including punctuation; track \`minmax(150px,1fr)\`. | Repeat each row, wrap long unbroken names within allotted column without expanding table unexpectedly. |
| LoRAs | Flexible **2fr** track; **all names inline**, one LoRA per line with compact blue gauge and exact numeric weight; full-cell hover popup 400 px. | **No three-item cap or ellipsis**; 55-character synthetic filename case; gauge and number remain visible when names elide; row height grows with entries. Confirm bounded rendering at 100–200 items. |
| Parameters | **Steps** bold; CFG and long sampler in smaller secondary lines; \`minmax(120px,.9fr)\`. | Preserve sampler identifiers, wrapping and hierarchy. Seed and dimensions stay in inspector. |
| Status | Semibold semantic **text label only** in an 84 px track. | Pending neutral; succeeded green; running, no result and cancelled amber; failed/save_failed red. No status progress bar. The underlying item state vocabulary stays authoritative. |
| Backend duration | 16 px stopwatch **header icon only**, 64 px track; compact right-aligned monospace value or \`—\`. | Tooltip/accessible label **Backend duration**. Show observed backend timing only; never substitute ETA. |

**Layout grid:** \`72px minmax(0,2fr) minmax(150px,1fr) minmax(0,2fr) minmax(120px,0.9fr) 84px 64px\`; gaps 12 px, horizontal padding 12 px, minimum scrollable content width 1000 px **in the standalone HTML**. Port visually, but test real Qt metrics and scrolling; the HTML is not a guarantee of native DPI fit. The row uses a selected navy fill and fine divider. Status styling is the same color vocabulary already used by DTFlow; no new badge should be invented.

**Right inspector:** no image and no saved-output filename. Clicking the row updates only complementary **ITEM EVIDENCE** and the retained mini-log. Buttons are **Open in Images**, **Open folder**, **Retry item** with real availability derived from recorded evidence and the existing retry/credit safeguards. The HTML's illustrative button flags are not proofs of production availability.

**Narrow widths:** inspector disappears below the shared-shell 1100 logical-pixel breakpoint, and **nothing is relocated or substituted** for its secondary details. Preserve readable horizontally scrollable center table. HTML left rail is illustrative (272 px wide/220 px narrow); reconcile with Qt's shared 248–300 px Designer constraints and preserve navigation.

**Existing queue controls are not redefined by sample HTML.** In particular, its decorative \`Max at once\` spinner is 1–4, while the real app supports 1–10; preserve the real contract. The HTML's queue header chip uses an amber Running border, while actual queue-level and item-level color mappings differ; keep queue-level semantics authoritative outside the redesigned **item** status cell.

**Data and safety:** every item repeats its own resolved checkpoint/LoRAs/settings; never infer setup from nearby rows. No model inference, billing, uploads, dispatch, schema modifications, or fabricated artifact/gallery identity for mockup validation. The queue creation mini-log remains visible in the inspector. Missing/unknown/cancelled outcomes use recorded evidence and honest text. Use existing \`QueueItemModel\` / \`QTableView\`, not a transplanted gallery \`QTableWidget\`.

## Implementation tasks

### M0 — Research/plan PR (first commit; no implementation)
- [x] Review #127, #130, relevant canonical repository contracts and the current queue model/controller/presenter/renderer.
- [x] Record the rendered-mockup-first approach, exclusions, acceptance gates and file ownership in this plan.
- [x] Push the plan-only commit to the dedicated branch and open **draft PR #150**, linked to #127/#130 (commit `785c288`).
- [x] Keep the PR in draft pending visual and functional acceptance.
- [x] Record P2/P3 review findings; the later owner decision explicitly supersedes P2's narrow fallback proposal.
- [x] Incorporate **Images → Detail** as the information hierarchy and **Images → List** as the visual reference.
- [x] Add the requested text-only sample with separate central-table and inspector tables, fake values, the 55-character synthetic LoRA filename, hover rules and responsive behavior.
- [x] Complete the historical **column-by-column Markdown review**, then inspect the subsequently provided **authoritative standalone HTML** and update both Markdown files for its revised design (especially **all inline LoRAs**, **64 × 64 tiles**, equal Prompt/LoRA tracks, scrollable central table, and three-section inspector).

### M1 — Translate the authoritative HTML into real-Qt **visual mockups only**

- [x] Complete the historical owner column-by-column Markdown review; subsequently **supersede conflicting specifications with the owner-provided HTML** and inspect its static behavior/data.
- [x] Update the derived Markdown specimen with **six sample rows** (including pending, no result and cancelled), all-inline LoRAs, revised inspector sections, grid dimensions, and observed mockup limitations.
- [ ] Establish fresh baseline screenshots from the existing named queue render states. No historical screenshot labeled as newly captured.
- [ ] Translate the owner's HTML arrangement and seven column tracks using the canonical Designer \`queue_page.ui\`, shared QSS/tokens, and existing model-view seam. Do not recreate another standalone HTML design.
- [ ] Mock **64 × 64** aspect-fit tiles, full positive/negative prompt hover, naturally wrapped two-line positive prompt, **all inline LoRAs** with **44 px gauge + 34 px value** and **full-column LoRA hover**, long model/sampler identifiers, colored plain-text status and minimal stopwatch duration header.
- [ ] Stress zero/one/three/**four or more** LoRAs, **55-character** filename, long and negative prompts, absent previews/durations, both very long and short sampler identifiers, and nontrivial row heights. Keep identifiers accessible and clicks/keyboard row selection dependable.
- [ ] Include the six HTML status fixtures **and extra failed/save_failed cases** for coverage of the real app vocabulary; render with the existing semantic palette, not ad-hoc status colors.
- [ ] Translate the three-section **ITEM EVIDENCE** inspector, conditional actions and creation mini-log; no image and no narrow-width fallback.
- [ ] Verify the horizontal scrolling model of a **1000 px minimum specimen table**, inspector auto-hide below 1100 px and central panel usability at 1080 × 800 and 800 × 680. Do not quietly change actual queue concurrency caps based on the illustrative HTML spinner.
- [ ] Mock/read through **220/380/400 px** nonblocking hover previews and ensure full text can be accessed without stolen clicks; the HTML's noninteractive overflowing prompt overlay is an explicit design/Qt-accessibility gap.
- [ ] Extend \`tools/ui/render_states.py\` with named synthetic fixtures and capture actual Qt screenshots. Report as offscreen **layout evidence only**, not Windows DPI, performance, mouse/keyboard or native acceptance.
- [ ] Push cohesive Qt mockup commits and obtain **explicit owner visual approval of the Qt translation** before wiring real queue data. Do not add or run tests at this stage.

### M2 — Integrate the approved design, without tests/docs yet
- [ ] Audit durable per-item request/evidence fields for model, LoRAs, weight values, both prompts, steps/CFG/sampler, seed/dimensions, backend/operation, statuses, gallery result IDs and recorded elapsed backend duration. Never fill absent values by guessing.
- [ ] Extend existing `queue_presenter.ItemRow` and `QueueItemModel` bounded projections; preserve selection, order, filters, and efficient in-place row updates while keeping stable technical identities internal.
- [ ] Reuse bounded asynchronous gallery/thumbnail decoding and safe cached source access; no blocking file/database reads or per-row heavyweight widgets in Qt paint paths.
- [ ] Wire read-only preview/prompt/LoRA hover overlays without stealing selection events; preserve full prompts and names (including long names), and use a verified gauge scale with explicit numeric values.
- [ ] Repeat model/LoRA setup on each row, show exact Steps/CFG/Sampler, single status label only and duration only from recorded backend evidence.
- [ ] Preserve inspector creation mini-log; enrich selected-item secondary fields (seed/dimensions/backend/IDs/outcome). Add **Open in Images** only for verified committed live gallery outputs; keep Open folder and Retry item semantics and consent safeguards. No inspector image or narrow fallback.
- [ ] Preserve responsive navigation, stable selection and scroll with typical 100–200-item queues; handle stale asynchronous thumbnail deliveries and missing outputs without implying generation success.
- [ ] Keep `render_states.py` fixtures aligned; push coherent integration commits and request explicit owner functional approval. Do not add or run tests or update unrelated docs before approval.

### M3 — Only after mockup **and** functional approval: validation and documentation
- [ ] Add/update **focused, authoritative** tests only for new behavior not covered by existing tests: request projection and timing evidence, stable filtered selection, async image identity/stale result handling, missing/snapshot-only files, and Open in Images navigation. Prefer extending existing contract-focused tests over one-off test accumulation.
- [ ] Run the agreed targeted tests, then relevant existing project validation/CI and selected Qt render captures. Record exact commands/results; distinguish Linux/offscreen layout from actual Windows interaction and packaged behavior.
- [ ] Perform owner Windows smoke: select/scroll through normal sized queues, inspect varied settings, show failed/unknown/missing outputs, Open in Images, resize and tab switches, monitor stalls and late image delivery. Any real generation or potentially billable provider action requires separate explicit authorization.
- [ ] Update `DESIGN.md`, `devdocs/guides/queues.md`, relevant architecture/source-of-truth docs, `devdocs/current_dev_status.md`, `devdocs/releases/unreleased.md` and parent #130's stale checklist **only as needed** to reflect verified shipped behavior. If tokens change, update runtime `tokens.json` and its `DESIGN.md` snapshot together.
- [ ] Record final verified/unverified boundaries; remove draft status only after agreed checks; close #127 only when accepted, and reconcile #130 without treating old unchecked boxes as work still missing.

## Explicit non-goals

No scheduler, concurrency, remote cancellation, retry/replay, state-schema or model/back-end protocol changes. No third-party UI library or HTML mockup. No unbounded decoding/polling, real model inference or uploads for mockup/validation. Do not conflate per-item duration with queue ETA, a synthetic fixture with a saved result, or offscreen screenshots with Windows/DPI acceptance.

## Evidence and checkpoint log

| Checkpoint | State | Evidence |
| --- | --- | --- |
| Owner HTML inspection and Markdown reconciliation | Completed | Standalone owner HTML SHA-256 `b57dffcfafb6ab2ecb49d4264bdabb1f491f561a588b6dc4c87bcea0e0ff3445`; plan and derived specimen updated. The attachment is not tracked in the PR. No production UI code, tests, browser behavior verification or Qt renders. |
| Qt translation rendered | Not started | Fresh named offline screenshots translating HTML to Qt, then explicit owner visual review. |
| Owner visual approval | Not requested | Explicit owner decision, not inferred from PR checks. |
| Real-data wiring | Not started | Implementation commits and changed files to be recorded. |
| Owner functional approval | Not requested | Native interactive evidence to be recorded. |
| Tests, validation, documentation | Deferred by owner | None should be added or run before M2 approval. |
| Training/inference/billable execution | Not performed for this planning slice | Future runs require explicit scope and authorization. |

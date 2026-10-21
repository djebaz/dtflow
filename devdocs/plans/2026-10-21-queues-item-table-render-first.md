# Queues #127: render-first item table and inspector redesign

**Date:** 2026-10-21  
**Status:** Research / plan and fictional Markdown pre-mockup recorded; Qt mockup and integration not yet approved or verified  
**Parent epic:** [#130](https://github.com/djebaz/dtflow/issues/130)  
**Feature issue:** [#127](https://github.com/djebaz/dtflow/issues/127)

## Owner workflow and approval gates

Work in **one dedicated draft PR**, with coherent commits pushed at each approved checkpoint. The plan is the first commit; never implement the feature as a prerequisite for creating the plan PR. The owner explicitly requested an intermediate **text-only Markdown pre-mockup** with fictional tables for both central rows and right inspector before a real Qt render. These written examples establish content hierarchy, not approved Qt geometry or runtime implementation.

1. **Plan first.** Commit and push this plan and open the PR. No feature code, tests or UI renders in this checkpoint.
2. **Text-only pre-mockup.** Review the [central table and inspector Markdown examples](../mockups/2026-10-21-queues-item-table-text-preview.md) with entirely fictional values. Record the owner's choices in this plan. No Qt render, tests, real data or implementation.
3. **Qt mockup only.** Use the canonical Qt Designer form, shared tokens/QSS and `tools/ui/render_states.py` with isolated, synthetic offline fixtures. Render actual Qt screenshots for review; do not connect the proposed controls to live queue state yet. No separate HTML mockup.
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
- **Adapt semantically:** a two-line positive prompt, repeated model, repeated LoRA names/weight gauges, steps, CFG, sampler, state label and recorded backend duration are primary, not filenames, seed, dimensions, progress or internal hashes. Retain a separate explicit state and recorded duration column; missing or pending output uses a neutral placeholder, not a fabricated preview.
- **Do not copy structurally:** Images List currently uses `QTableWidget`, while Queues already owns a model-backed `QTableView`. Keep `QueueItemModel`, queue selection/filters, bounded asynchronous image delivery and queue-specific controls. Do not bring gallery bulk checkboxes, export/delete actions or a second gallery controller into Queues.

### Owner decision overriding the earlier P2 narrow-details proposal

The earlier P2 reviewer recommendation was an **inline Details fallback** when the shared shell hides the right inspector below **1100 logical px**. The owner explicitly chose **not to add any replacement or relocated inspector content** at narrow widths. The right inspector holds complementary, non-essential information only; the central table must remain the primary readable surface.

The owner also rejected the proposed large inspector image: **show thumbnails in central rows and enlarged read-only images on hover; do not show an image in the inspector**. Clicking any central row selects it and updates the visible inspector. Hover overlays are informational and must not intercept row selection.

These are owner-approved placement/interaction constraints, not approval of a rendered layout or production implementation.

### Review finding P3 — broken reference

The older Queues UI-kit relative link was corrected from `../../design-system/...` to `../design-system/...` relative to this file in `devdocs/plans/`. No other documentation is changed at this checkpoint.

**Review provenance:** these P2/P3 issues were reported in a reviewer screenshot supplied by the owner on 2026-10-21. The review explicitly characterized them as plan issues and reported no edits, tests, renders or workflows. They are not evidence of a reproduced production bug.

## Scope: owner-approved content and text-only prototype

The central table lists generation items in the **selected existing queue**, not the left queue list or New queue composer. The [Markdown pre-mockup](../mockups/2026-10-21-queues-item-table-text-preview.md) provides fictional **central-table and inspector tables**, including an explicitly synthetic **55-character LoRA filename with extension** for layout stress. It is not a Qt mockup or a claim about source data.

**Seven central columns, in order:** **Preview | Prompt | Model | LoRAs | Parameters | Status | Backend duration**. No separate generation-position, seed, filename, item ID, attempt, output count or operation column.

| Column | Content and hierarchy | Interaction and constraints |
| --- | --- | --- |
| Preview | Compact cached thumbnail, aspect-fit; placeholder when unavailable. Row height accommodates a small thumbnail and multiple lines. | Hover provides a larger read-only image without intercepting clicks. Do not fabricate saved images. |
| Prompt | Dedicated column targeted to ~one-third of table width; **positive prompt only**, regular text, **two lines max**, ellipsis on overflow. | Read-only hover overlay contains **full positive and negative prompts**; no Copy button, no click interception. |
| Model | Model name semibold; repeated on every row, even if identical to adjacent rows. | Elide long identifiers safely; do not let the cell expand the table beyond its layout. |
| LoRAs | Separate column, **one LoRA per line**, each with name, horizontal **weight gauge**, and numeric weight; repeat on every row. Show **at most three LoRAs inline**. | For >3 entries display **`…`**, and on hover show **the entire list** (including visible entries), with names/gauges/values. Hover-only, read-only. Keep gauge width and scale consistent; confirm numerical domain before implementation. Include the reported 55-character filename case. |
| Parameters | **Steps, CFG, sampler** in compact readable lines, with emphasis on primary values. No seed or image dimensions. | Compare values without requiring inspector. |
| Status | **Single textual state label** with semantic styling. | **No step counter, progression or extra progress bar**, which already exists elsewhere. |
| Backend duration | Recorded backend elapsed duration, compact and right-aligned; `—` when unrecorded. | Never use estimates or queue ETA in place of backend duration. |

**Right inspector:** preserve the creation **mini-log**. Selected-item section contains only complementary evidence: **seed, dimensions, backend and operation when relevant, stable technical IDs, outcome/failure/uncertainty details**, and eligible **Open in Images**, **Open folder**, **Retry item** actions with existing safety semantics. **No large image preview or filename field.** Clicking a table row updates this inspector when visible.

**Narrow widths:** let the shared shell hide the right inspector. **No Details disclosure below the table and no replacement detail mode**. Primary comparison data stays in the central table. Evaluate clipping, readable scroll and overflow at 1080 × 800 and 800 × 680 during Qt mockup review.

**Input provenance:** repeat per-item model and LoRAs whether or not changed; never infer data from adjacent rows. Missing outputs, prompts, duration, statuses or settings must be represented honestly rather than invented. Use bounded off-thread image delivery and retain Queues' model-backed `QTableView`; do not transplant gallery `QTableWidget`/bulk actions.

**Activity:** preserve the useful existing mini-log first. The issue's item-scoped activity-selection proposal remains a later review detail; do not falsely imply this is implemented.

Retain existing queue selection, controls, queue header, composer, dispatch policy and responsive shell.

## Implementation tasks

### M0 — Research/plan PR (first commit; no implementation)
- [x] Review #127, #130, relevant canonical repository contracts and the current queue model/controller/presenter/renderer.
- [x] Record the rendered-mockup-first approach, exclusions, acceptance gates and file ownership in this plan.
- [x] Push the plan-only commit to the dedicated branch and open **draft PR #150**, linked to #127/#130 (commit `785c288`).
- [x] Keep the PR in draft pending visual and functional acceptance.
- [x] Record P2/P3 review findings; the later owner decision explicitly supersedes P2's narrow fallback proposal.
- [x] Incorporate **Images → Detail** as the information hierarchy and **Images → List** as the visual reference.
- [x] Add the requested text-only sample with separate central-table and inspector tables, fake values, the 55-character synthetic LoRA filename, hover rules and responsive behavior.

### M1 — Real-Qt visual mockup, **no production data wiring**
- [ ] Obtain owner review of the fictional Markdown table and inspector example; revise their proposed formatting where useful.
- [ ] Establish screenshot baseline from the existing named queue renders. No historical screenshots mislabeled as new captures.
- [ ] Use seven agreed columns and **Images → Detail** hierarchy / **Images → List** compact style, retaining the existing Queues table-model architecture.
- [ ] Mock compact thumbnail plus larger hover, dedicated two-line positive prompt plus complete positive/negative hover, separate Model and LoRAs, per-LoRA weight gauge/number, three inline LoRAs then hover `…`, Steps/CFG/Sampler, simple status and observed backend duration.
- [ ] Explicitly stress a **55-character synthetic LoRA filename including extension**, zero/one/three/four LoRAs, long prompts, long model names, missing images and missing duration. Avoid horizontal layout blowout; keep complete long LoRA identity accessible without disrupting row selection.
- [ ] Mock the right inspector as complementary data/actions plus preserved creation mini-log, **without any image preview or narrow-window fallback**.
- [ ] Update canonical Designer `queue_page.ui`, scoped theme/QSS and offline-only fixture adapters as needed; use `tools/ui/render_states.py`, not HTML or an independently duplicated UI framework.
- [ ] Capture real Qt fixtures at normal and narrow widths (1080 × 800, 800 × 680); verify hover-overlays versus row-click selection in interactive mockup, and note native Windows/DPI acceptance separately.
- [ ] Push mockup commit(s) and request **explicit owner visual approval** before connecting production queue data. Do not add or run tests.

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
| Plan and fictional Markdown pre-mockup | Completed | PR #150 draft; original plan commit `785c288`, P2/P3 review commit `624b1e5`, and a separate Markdown specimen with central and inspector examples. No product code or Qt renders. |
| Qt mockup rendered | Not started | Named offline screenshots to be linked here after actual capture. |
| Owner visual approval | Not requested | Explicit owner decision, not inferred from PR checks. |
| Real-data wiring | Not started | Implementation commits and changed files to be recorded. |
| Owner functional approval | Not requested | Native interactive evidence to be recorded. |
| Tests, validation, documentation | Deferred by owner | None should be added or run before M2 approval. |
| Training/inference/billable execution | Not performed for this planning slice | Future runs require explicit scope and authorization. |

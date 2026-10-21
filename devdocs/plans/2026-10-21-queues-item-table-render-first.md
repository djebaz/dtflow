# Queues #127: render-first item table and inspector redesign

**Date:** 2026-10-21  
**Status:** Research / plan committed; mockup and implementation not yet approved or verified  
**Parent epic:** [#130](https://github.com/djebaz/dtflow/issues/130)  
**Feature issue:** [#127](https://github.com/djebaz/dtflow/issues/127)

## Owner workflow and approval gates

Work in **one dedicated draft PR**, with coherent commits pushed at each approved checkpoint. The plan is the first commit; never implement the feature as a prerequisite for creating the plan PR.

1. **Plan first.** Commit and push this plan and open the PR. No feature code, tests, UI renders or documentation changes in this checkpoint.
2. **Qt mockup only.** Use the canonical Qt Designer form, shared tokens/QSS and `tools/ui/render_states.py` with isolated, synthetic offline fixtures. Render actual Qt screenshots for review; do not connect the proposed controls to live queue state yet. No separate HTML mockup.
3. **Owner visual approval is mandatory.** Present a named screenshot set and an explicit list of what is mock-only. Capture feedback and revise the mockup until accepted. Do not begin real data integration on implied approval.
4. **Implement the approved layout.** Connect it to the existing selected-queue read/presenter/gallery boundaries without changing dispatch, reservations, schema or billable generation behavior. Commit/push this slice for owner functional review.
5. **Owner functional approval is mandatory.** Review the real desktop behavior (especially native Windows layout and interaction) before starting test additions, running tests, lint/format gates, or general documentation updates.
6. **Only after functional approval:** add/adjust the smallest authoritative tests, run agreed validations, update applicable user and developer documentation plus release notes, record evidence and prepare the PR for merge. Update **this plan alone** earlier when scope, decisions or checkpoint outcomes change.

**During steps 1–5:** do not add or run pytest/other test suites, or independently run lint/format/packaging/CI workflows; no unrelated documentation edits. Running `render_states.py` in step 2 is an explicitly requested **visual capture**, not a claim of test, performance, Windows/DPI or end-to-end acceptance. Opening/pushing a PR can trigger repository-managed CI automatically; distinguish that from tests explicitly invoked by the implementer. Never run real generation, upload, training, or potentially billable operations for this task.

## Current source audit (before implementation)

Source inspected on the default branch on 2026-10-21; these are **code observations**, not fresh native runtime or screenshot validation.

- `src/dtflow/ui/forms/queue_page.ui` owns the central `queueItemsView`, right `queueInspector`, selected-item box and activity browser. Keep Designer as the layout authority.
- `src/dtflow/ui/queue_model.py` is already a `QAbstractTableModel` with stable order and in-place row updates; its current columns are `# / Item / Seed / State / Attempt / Outputs / Edit`. No separate table framework is required.
- `src/dtflow/ui/queue_presenter.py` currently exposes `ItemRow` with item identity, seed, state, attempt identity, output paths, metadata path, failure code, operation and edit strength. The current `event_lines()` renders a compact durable activity stream, including recorded successful settlement duration when available.
- `src/dtflow/ui/queue_page.py` polls summary rows for all queues and reads items/events only for the selected queue; it preserves per-item selection through `QueueItemModel`. The right inspector currently shows shortened item/attempt IDs, backend, output summary, **Open folder** and **Retry item**; it has no implemented image preview or Open in Images handoff.
- `tools/ui/render_states.py` already has `render_active_queue_states()` and named queue states such as `queues-16-item-states` and `queues-17-item-inspection`, with synthetic read views and no actual dispatch. Extend these rather than create a second screenshot harness.
- The repo's `DESIGN.md` and `AGENTS.md` require Qt Designer, shared style tokens/QSS, offline fixture renders for UI changes, bounded asynchronous image handling, honest outcome text and no work on the Qt event loop.
- The [older Queues UI kit](../../design-system/ui_kits/queues/QueuesScreen.jsx) is historical and still depicts the ID-heavy table; it is **not** an approved #127 mockup.

Unverified until the integration audit: the precise selected-item request/plan-metadata read API; the authoritative mapping from queue output identities to committed Images gallery identities; whether duration already exists on the selected-item projection; native Windows performance and width behavior.

## Scope: what the approved design must convey

The **central table** represents generation items of the **selected existing queue**, not the left queue list and not the separate **New queue** composer preview.

Proposed information hierarchy (subject to screenshot review):
- **Table:** position, small result thumbnail/placeholder, concise varied values (seed, model, LoRA set/weights, steps, and other actually varied values), explicit state, recorded duration when present.
- **No prominent ID columns:** full stable item/attempt/request IDs remain inspectable and copyable in the right inspector; preserve stable row identity internally.
- **Inspector:** selected item's larger aspect-fit output preview, exact request summary, varied values, human-readable outcome including honest no-result/save-failed distinction, full identifiers, **Open in Images** and **Open folder**. Preserve existing explicit **Retry item** action and consent/billing semantics.
- **Activity:** readable collapsible event list in the right rail. If a line identifies an item, activating it selects that item in the central table; queue-scoped lines do not invent an item.
- **Missing evidence:** show `Not recorded`, `No saved image`, `Duration unavailable` or equivalent explicit language. Never infer elapsed inference time, remote cancellation, saved artifacts, or cross-provider compatibility.
- Retain existing selected-queue header, filters, status/progress, left queue list, composer, queue controls and responsive shell.

## Implementation tasks

### M0 — Research/plan PR (first commit; no implementation)
- [x] Review #127, #130, relevant canonical repository contracts and the current queue model/controller/presenter/renderer.
- [x] Record the rendered-mockup-first approach, exclusions, acceptance gates and file ownership in this plan.
- [ ] Push the plan-only commit to the dedicated branch and open a **draft PR**, linked to #127/#130.
- [ ] Leave the PR in draft pending visual and functional acceptance.

### M1 — Real-Qt visual mockup, **no production data wiring**
- [ ] Establish screenshot baseline from the existing named queue renders; do not relabel historical fixtures as new screenshots.
- [ ] Sketch compact columns and the right inspector for a normal desktop width; prioritize image/variation/state over internal IDs, without crowding the table.
- [ ] Edit the tracked `queue_page.ui` and scoped tokens/QSS only as necessary. Render the *actual* Qt widgets; avoid a duplicate HTML implementation or hand-edited generated `Ui_*` Python.
- [ ] Add fixture-only presentation adapters/data to existing Qt models/presenters where needed; clearly separate mock data from production read/dispatch paths. Reuse image fixture assets already in the repo.
- [ ] Add distinct named `queues-*-` states to `render_states.py` for: empty/no selection, repeat queue, Pair/Cross with long varied values and LoRAs, running/pending, mixed success/failure/no-result, selected saved output in inspector, multi-output/absent output, activity expanded/collapsed, narrow center (inspector auto-hidden below 1100 px).
- [ ] Capture full-window and useful close-up screenshots at normal and constrained widths. Confirm visually that the queue composer and header still fit; no static screenshot counts as interaction/performance acceptance.
- [ ] Push the mockup commit(s), share screenshot artifact references and a concise list of proposed decisions. **Stop here for explicit owner visual approval.**

### M2 — Integrate the approved design, without tests/docs yet
- [ ] Recheck selected-item durable read models and saved attempt/request evidence; document the exact read fields needed, without inventing a new dispatch or persistence authority.
- [ ] Extend `queue_presenter.ItemRow` and `QueueItemModel` with a bounded UI projection of the recorded request's varied values and status; show full request in the inspector. Preserve model/view stable selection, item ordering, existing status filters and efficient same-item updates.
- [ ] Derive item duration only from persisted, per-item observed timing evidence (e.g., successful settlement event) where available; display unavailable otherwise. Do not substitute estimated queue ETA or guess backend inference time.
- [ ] Implement thumbnail and inspector-image reads using existing saved-output/gallery boundaries, safe original-file access and asynchronous bounded decode/caching. No decoding, disk reads, database scans or heavy per-row rendering in Qt paint/layout callbacks; avoid per-row widget allocation for large plans.
- [ ] Verify exact queue output identity → gallery identity/commit status and reuse Images navigation to open the correct saved image in Detail view. Disable the action when no verified live committed image can be opened; do not authorize actions from gallery snapshots alone.
- [ ] Reuse the existing `ImagePreview` behavior if its Qt ownership/sizing contract fits; do not create a second image decoder or a competing metadata service.
- [ ] Add copyable identities and honest outcome/details in the inspector, preserve **Open folder** and **Retry item**, and connect item-scoped activity to stable item selection. Handle queue-scoped events, missing outputs and item disappearance cleanly.
- [ ] Keep normal polling/selection/navigation responsive with larger queues (100–200 ordinary; 1000-item plans are bounded stress cases, not the primary UX). Preserve user scroll and selected preview during updates and ignore stale async responses after selection/tab/shutdown changes.
- [ ] Extend existing `render_states.py` fixtures as the implementation evolves; use renders to verify layout continuity. Do **not** add or run tests or update general docs in this milestone.
- [ ] Push coherent integration commit(s), review code/visual differences, request explicit owner functional smoke approval.

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
| Plan commit and draft PR | Pending PR creation | This plan is the only intended changed file for M0. |
| Qt mockup rendered | Not started | Named offline screenshots to be linked here after actual capture. |
| Owner visual approval | Not requested | Explicit owner decision, not inferred from PR checks. |
| Real-data wiring | Not started | Implementation commits and changed files to be recorded. |
| Owner functional approval | Not requested | Native interactive evidence to be recorded. |
| Tests, validation, documentation | Deferred by owner | None should be added or run before M2 approval. |
| Training/inference/billable execution | Not performed for this planning slice | Future runs require explicit scope and authorization. |

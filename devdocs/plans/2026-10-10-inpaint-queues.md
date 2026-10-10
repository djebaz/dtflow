# Plan: Inpaint and image-to-image in queues

**Status:** Implemented in this pull request as source (steps 1 to 3); owner Windows acceptance (step 4) pending. No DB schema change was needed: the staged snapshot and the per-item consent live in the request. Date: 2026-10-10. Base: main `1088a55` (PR [#64](https://github.com/djebaz/dtflow/pull/64) merged).
**Related plans:** [smart mask inpainting](2026-10-10-smart-mask-inpainting.md) (its "Out" list names image-operation queues), [Phase 5B desktop queue](2026-10-04-phase-5b-desktop-queue.md), [Phase 6 queue planning](2026-10-04-phase-6-queue-planning.md), [image operations architecture](../architecture/image-operations.md).

## Intent

Let a user queue Inpaint edits from the Generate editor (and later reuse the same source and mask for several seeds) instead of running them one at a time, with the queue's existing guarantees: durable items, restart safety, reservations for unknown outcomes, no replay.

Today an edit runs immediately: **Edit image** builds an owner-probe permit, prepares inputs on a worker and calls `generate_image_once`. Queues, planner and batch entry points refuse every non-`txt2img` request (`persistence/queues.py` `request_from_record` and `_create_queue`, `services/planning.py`). This plan lifts that refusal narrowly and safely.

## What already exists

| Piece | Where | Consequence |
| --- | --- | --- |
| Request v2 holds `operation`, `strength` and an immutable `ImageInputSnapshot` reference (no bytes, no paths) | `core/models.py`, `core/image_inputs.py` | A queue item can already describe an edit; only the queue refuses it. |
| Durable, exclusive source/mask/blend snapshots under `<state>/inputs/<attempt-id>/`, manifest written last | `persistence/image_inputs.py` | Inputs survive restart and are verified by hash. They are evidence, never cache. |
| Attempt-bound permit with exact backend, checkpoint and operation scope, cloud description digest when one is recorded | `services/session.py` (`owner_probe_permit`, `generate_image_once`) | A queued edit still needs a permit at dispatch time. |
| Execution DB v3, queue tables `queues`, `queue_items`, `queue_controls`, `queue_events` | `persistence/store.py`, `persistence/queues.py` | Items store `request_json`; `attempt_id` is set at claim time. |
| Dispatcher calls `GenerationService` per item and settles the item from the attempt | `services/queue.py`, `services/queue_session.py` | Image items need a different execute path but the same settlement rules. |

## Findings that shape the design

1. **Inputs are keyed by attempt, but queue items exist before attempts.** (Implemented differently: the staged copy lives under its own id in `<state>/inputs/`; at dispatch the service mints the attempt id and snapshots the restored inputs under it, so no pre-allocated attempt id was needed.) `generate_image_once` mints the attempt id, writes the snapshot and then runs. A queue must persist the inputs at enqueue time. Recommended: pre-allocate the attempt id at enqueue, write the snapshot under it, store the snapshot reference inside the item's request, and have the dispatcher create the attempt with that same id. Confirm at the start of step 1 that the store can create an attempt with a caller-supplied id; if it cannot, the alternative is an item-keyed input directory that is bound to the attempt at claim.
2. **Consent cannot be a live click.** The editor treats the click as the per-run probe consent. For a queue the click is at enqueue, the run is later. Record the consent as part of the item (permit scope: backend, checkpoint, operation, endpoint label, evidence id such as `queue-<item-id>`, cloud description digest if any). It is single-use for that item, never a standing permission, and the dispatcher revalidates scope against the queue's recorded backend before sending anything.
3. **Uploads happen at dispatch, not at enqueue.** Cloud edits send source and mask off-machine and may consume credits. Enqueueing writes local files only. The Queues page must disclose the upload and credits before Start, as it already discloses cloud credits.
4. **Unknown outcomes keep their meaning.** Unknown, interrupted and save-failed items stay reserved and never replay; their input snapshots stay as evidence. Clearing queues removes only queue records, never `inputs/` or `outputs/`.
5. **Memory and disk.** Items must not hold pixels in the DB. Source and mask are read from the snapshot when the item is claimed and released after dispatch. A queue of many seeds of one edit should share one staged snapshot, not copy a 16 MP source per item.
6. **Planner gets one new dimension.** `strength` (finite, 0..1) joins the planner's scalar dimensions and is accepted only for image-operation plans; txt2img plans keep refusing it. The other existing scalars (prompt, model, steps, guidance, sampler, LoRA weights, seed) keep working over a staged edit, except `size`, which is refused because the snapshot is fitted to one size. The item count is still computed arithmetically and bounded by the 1000-item queue maximum before enumeration. Each item records `plan.dimension.strength` in its evidence like any other dimension.

## Scope

### In

- Stage an edit (source, mask, grow, feather, keep-outside, size) once and enqueue 1..N items that share it. Items may differ by seed and by **strength**, in all three planner modes: `seed` (count items), `pair` (zip) and `cross` (product). Example: strength `0.5, 0.7, 1.0` crossed with seeds `1, 2` gives six items over one staged edit.
- The Queues tab's Create queue and Plan area queue the current Inpaint edit when Generate is in Inpaint mode; same disclosure text as Edit image.
- Planner and dry run: `strength` as a dimension for `seed`, `pair` and `cross` plans over a staged edit, shown in the dry-run table. The Queues page Plan area offers it only when the queue is an edit queue.
- Dispatcher path for image items: restore the snapshot, rebuild the permit from the recorded consent, call the existing image generation path, save raw and final outputs and settle the item from the attempt.
- Queues page: an operation badge (Inpaint or Image to image), strength, and a small source thumbnail per queue; Start disclosure for cloud uploads.
- Restart: pending image items resume; hash verification fails closed (item fails with a clear reason, no replay, no regeneration of inputs).
- Documentation, release notes and status.

### Out

- `dtflow queue add` and plan/config files for image operations (they stay refused with the current message).
- Per-item masks, multiple sources, per-item size (the staged inputs are fitted to one generation size), control or reference images.
- Standing permits, automatic retries, provider fallback, PAYG.
- Re-using the previous result as the next item's source inside one queue ("chain"). Single edits can already chain through **Use result as source**.
- Changing the meaning of unknown outcomes or the override control.

## Design

### Persistence

- No new table if the request reference is enough. Add queue item fields only for what must be queryable without decoding JSON: operation (derived from the request, so none needed) and a consent record (in the request's evidence or a small `consent_json` column). Prefer evidence inside `request_json` to avoid a DB bump; add an execution DB version only if a column is needed, using the repository's backed-up transactional migration and keeping older writers rejected.
- Staged inputs reuse `save_input_snapshot`. The item request carries the `ImageInputSnapshot`. Shared snapshots are referenced by several items; deletion rules stay "never delete".
- `request_from_record` and `_create_queue` accept `inpaint`/`img2img` only when the request carries a valid snapshot, one output, and an item consent record. Every other image-operation request is still refused.

### Dispatch

- `QueueDispatcher._execute` branches on `request.operation`. Image items call a new `session` entry that takes a restored snapshot and a recorded consent instead of prepared pixels, then reuses the existing exact-scope validation, backend execution, raw/final saving and settlement.
- The permit is rebuilt from the item's consent; if the backend, endpoint label or checkpoint differs from what was recorded, the item fails before authentication or upload with a message naming the mismatch.
- Cloud concurrency and the per-backend reservation rules are unchanged; image items count like any other item.

### UI

- Queues tab: in Inpaint mode, **Create queue** stages the editor's inputs on a worker and creates the paused queue; the Plan area shows a Strengths field. It never starts dispatch.
- Queues page: shows operation, strength, thumbnail and the cloud disclosure; Start and Clear keep their current semantics. Preview states for `tools/ui/render_states.py` are added with the view change.
- No new modal dialogs; inline feedback only.

## Pull-request sequence

1. **Persistence and request acceptance.** Pre-allocated attempt id (or item-keyed inputs), consent record, relaxed queue validation for well-formed image items, planner `seed`/`pair`/`cross` modes with the `strength` dimension for staged edits. Offline contract tests with the fake backend: enqueue, restart, hash mismatch, scope mismatch, unknown outcome keeps its reservation.
2. **Dispatcher and session path.** Image item execution, outcome settlement, raw/final saving, cloud and local scope checks. Fake-backend tests for success, failure, cancellation and no-replay.
3. **Desktop.** Queues tab Create queue/Plan wiring, Queues page display, disclosure, render states, desktop guide.
4. **Owner acceptance.** Owner-run Windows smoke on local and DT+ cloud with one exact checkpoint each. No live generation in CI, nothing billable by the author.

Each step updates `devdocs/releases/unreleased.md`, `devdocs/current_dev_status.md` and the image-operations architecture document in the same change. No version bump is implied; this belongs with the 0.5.0 image-to-image milestone in [the release roadmap](releases.md).

## Owner decisions (2026-10-10)

1. Seed-sweep items share one staged snapshot rather than one per item.
2. **Queue edit** is available on cloud when no model description is published, like **Edit image** (the capability line still discloses it).
3. Strength is a per-item value and joins the cross and pair plan systems, not only the seed sweep.

## Validation

Planned: `uv lock --check`, `uv run ruff check .`, `uv run ruff format --check .`, offline `uv run pytest` including restart and no-replay cases with the fake backend. Live checks are owner-run only. Nothing in this plan has been implemented or verified.

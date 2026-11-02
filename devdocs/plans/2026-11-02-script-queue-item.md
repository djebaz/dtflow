# Script run as one queue item (design note, PR 0)

Status: proposal for the owner's approval. Docs only; no code, schema or behaviour changes in this PR. Part of the
Scripts epic (#114, #118; #115 and #196 are touched only where noted). Written against `main` at 697d839.

## Decision being implemented

Owner decision (2026-11-02, #118): **a script run is ONE queue item for the whole run, and its `generateImage` calls
are child steps.** This note says how that fits the existing queue without regressing it. Approving it also amends two
earlier statements: #114's "no script-specific dispatcher" and the #201 design's "No queue entries".

## What exists today (facts the design builds on)

- A queue holds 1..`MAX_ITEMS` items; each `queue_items.request_json` is an `asdict` of a `ResolvedGenerationRequest`.
  `_claim_next` decodes it with `request_from_record` (which runs `check_queueable`), marks the item `running`
  (a reservation counted per backend key against the queue cap), and `QueueDispatcher._execute` runs it through
  `GenerationService`. `_settle_item` finds the item's attempt by `request_id` and maps the attempt state through
  `ATTEMPT_TO_ITEM`. `reconcile` does the same after a crash.
- Controls are `pause`, `resume`, `cancel`, `cap`, posted by any process and applied by the dispatch owner.
  `cancel` marks pending items cancelled and cancels running item tasks locally (never the remote job).
- `ScriptRunStore` (`script_runs.sqlite3`, its own `user_version`) keeps `script_runs` and ordered `script_run_children`;
  `run_recorded` creates and locks the row before the child starts, hooks call `link_child(attempt_id)` before each
  submission, and a crashed `running` row becomes `interrupted` and is never resumed or replayed. `ScriptHost` runs each
  `generateImage` through `run_generation`, one attempt each, tagged `source=script`, `script_call=N`.

The two stores are separate SQLite files. Nothing may assume a cross-file foreign key or one transaction over both.

## Shape

### Queue and item

- A queue gets `kind` (`generation`, default, or `script`); the schema bump is `SCHEMA_VERSION` 4 to 5 in
  `persistence/store.py`, added with the same `ALTER TABLE ... ADD COLUMN` pattern as `add_up_next`. A script queue holds
  **exactly one item**, cap fixed at 1, and uses the normal `backend_key`, so it shares backend reservations,
  up-next/auto-play, overlap, ownership lock, listing and deletion with every other queue.
- The item's `request_json` is a **script request record**, not a `ResolvedGenerationRequest`:
  `{"kind":"script","run_id":…,"source_name":…,"source_sha256":…,"answers":…,"start":{…}}`. `start` is the same
  starting context `run_cli.starting_context` records today (base settings, backend, `max_generations`). The script
  source itself is read from the managed library by SHA (PR 1), not stored in the queue row.
- `run_id` is **generated when the queue is created**, so the item names its run durably before anything starts.
  `ScriptRunStore.create` gains an optional `run_id` argument (unique; still takes the OS lock before the row exists).
- Child steps are not queue items. They are the run's `script_run_children` rows, read through to their attempts
  (state, artifacts, backend duration) when a queue is inspected. `queue_items.attempt_id` stays empty for a script item.

### Why not N items, or a pre-expanded queue

A script decides at run time how many images it makes (loops, form answers, earlier results), so its steps cannot be
planned into pending items, and items created mid-run would let pause/cancel/relaunch act on steps the script has not
asked for. One item with a bounded `max_generations` (default 4, existing) keeps the owner's decision and the existing
generation cap intact.

## Code paths that must branch on kind

All branches are on `queues.kind`, defaulting to the current code path, so generation queues stay byte-for-byte on the
old path.

| Place | Change |
|---|---|
| `check_queueable`, `_check_new_queue`, `request_from_record` | Untouched. A new `create_script_queue` does its own validation and inserts through the same `_insert_paused` shape; `expand_items`, composer, templates, relaunch and Retry item refuse a script queue with a clear error (a script relaunch is a new Start, not a copy; revisit with the Scripts page). |
| `_claim_next` | If the queue is a script queue, decode the script request record instead of calling `request_from_record`; same `pending` to `running` reservation. A malformed record fails the item with `stored_request_invalid`, as today. |
| `_dispatch_one` (`services/queue_session.py`) | Same backend open, permits (none for Tier A) and `GenerationService`; then call a new `ScriptItemRunner` instead of `QueueDispatcher.run`. The branch is a separate function so a script bug cannot enter the generation loop. |
| `ScriptItemRunner` | Applies controls, claims the one item, builds `ScriptHost` with the service and `link_child` exactly as `run_cli` does, runs `run_recorded(..., run_id=…)` in a thread, drains submitted attempts, settles the item and returns the same queue view shape. Must reuse `run_cli`'s hook-building code (extract it, do not copy it) so CLI and queue run the same host. |
| `_settle_item`, `reconcile` | For a script item, state comes from the run row plus its children (table below), never from a lone attempt. |
| `_read` / `read_queues` | For a script item, add `script` (name, SHA, run id, run state) and `children` (ordered steps with attempt id, attempt state, artifacts) to the item dict, and fill `request_id` from the run id. `_item_request_fields` already tolerates missing keys (empty strings/None); script items show the script name instead. The `varied` summary and ETA treat a script queue as "unknown duration"; no estimate is invented. |

## Item state mapping

| Run state (`script_runs`) | Children | Item state |
|---|---|---|
| running | any | `running` |
| completed | all succeeded | `succeeded` |
| completed | some non-succeeded (script swallowed a failure) | `succeeded`, with a "N of M steps failed" note derived from children |
| failed | any | `failed` (`failure_json` from run error, children listed) |
| stopped | any | `cancelled` (matches existing local-stop meaning) |
| interrupted (crash) | any child `unknown`/non-terminal | `unknown` |
| interrupted (crash) | all children terminal | `failed` |
| no row yet when the owner restarts | none | back to `pending`, as an item with no attempt is today |

Rules carried over: an `unknown` child never blocks other work, releases the reservation, is **never replayed**, and a
run that sees an unknown outcome blocks its own further generation (already true in `ScriptHost`). A run is never
resumed after a crash. A missing run row means the run never started, so the item returns to `pending`; it is never inferred to have run.
The existing `ITEM_STATES` do not change.

## Admission and concurrency

- The script item holds **one** queue reservation for the whole run. Script hooks block, so a run has at most one
  generation in flight; child generations are still admitted by `GenerationService`/backend limits and the existing
  `--max-generations` cap, so the combined limit stays enforced where it is today. No parallel children in this slice.
- Other queues on the same backend still fit under the shared `_reserved` count; the script just occupies one slot.
- Unsupported effective settings still fail closed in `ScriptHost` (#115): a rejected field fails that call before any
  submission, and the run decides what to do with the error. Nothing in the queue layer relaxes this.

## Stop, Cancel, Pause

- **Cancel before start**: item `pending`, queue cancelled by the existing `cancel` control. Free; no run row exists
  yet (the id is reserved only in `request_json`).
- **Stop while running**: the same `cancel` control. The runner sets the run's cancel event, the child gets its normal
  grace, `run_recorded` settles the run as `stopped`, the item becomes `cancelled`. In-flight attempts are never
  cancelled remotely and stay recorded (same wording as the Stop test in the smoke plan). No new control action.
- **Pause/resume**: only affect claiming. A running script item is not paused mid-run, which matches a running
  generation item today. The Scripts page must not show a pause affordance for a running run.
- The known Ctrl+C status quirk in the console (#198: child also receives SIGINT and reports `failed`) is out of scope
  here; Stop through the queue goes through the parent-side event, which is the path that yields `stopped`.

## Mid-run input (PR 4 hook, defined now so the schema does not need a second bump)

`requestFromUser` must work when the dispatching process is not the UI process ("dispatch owned elsewhere"), so answers
travel through persisted state, not callbacks:

- A new table in `script_runs.sqlite3` (`script_run_prompts`: run_id, ordinal, components_json, answer_json NULL,
  created_at, answered_at) is written by the host when the hook is called and read back by the page. The run's own
  `answers_json` log (existing) records answers as they are consumed.
- While a prompt is unanswered the item stays `running`; the view derives a `waiting_for_input` flag from the open
  prompt, so `ITEM_STATES` and queue statuses need no new value. Stop resolves the open prompt as cancelled.
- This table is a `script_runs` schema 2 bump, shipped with PR 4, not PR 2. PR 2 keeps the headless answers path.

## Images link

Child attempts already carry `source=script` and `script_call=N` in their resolution evidence. PR 2 adds the run id to
that evidence so "Created by script X, step N" can be resolved from an attempt without scanning runs; the reverse
direction (item to attempts) is the children list above.

## Risks and how the design contains them

1. **Dispatcher regression** (`_claim_next`, `_dispatch_one`, `_read` are shared). Contained by the default-kind branch
   and by keeping the script runner a separate function. The flaky dispatch-order test tracked in #142 may show up in
   CI on these files; it is not caused by this change.
2. **Two databases, no atomicity.** The run row (script DB) and item state (attempts DB) can disagree after a crash.
   Reconcile always derives the item from the run row, treats a missing row as "never started", and never infers
   success the run row does not state.
3. **Schema 5 on the attempts DB.** The store already backs up before upgrading (`attempts.pre-v…` copy); opening with
   an older build after the bump is refused by the existing version check. State this in the PR 2 description.
4. **Item with no single request.** Everything that reads `request_json` assuming a generation request (ETA, "varied"
   summary, composer source, relaunch, image scopes, `_settle_item`) must be audited in PR 2; the table above is the
   known list, not a proof it is complete.
5. **Billable generation.** PR 2 is verified headless on the fake backend only; real local/cloud smoke is run and
   approved by the owner using the existing `smoke_one.js`/`smoke_two.js` fixtures (see the minimal plan, section 8).

## Out of scope

Canvas, masks, pickers, detection (#116, #117), the editor tooling of #202, a pre-run Configure form (upstream builds
forms by running the script), credit estimates, parallel children, resuming a run after a crash, and multi-script
queues. Per project convention this note adds no tests, runs none and changes no other documentation; `scripting.md`
and `current_dev_status.md` are updated with the implementation PRs after the owner's smoke validation.

## Follow-on PRs

| PR | What this note defines for it |
|---|---|
| 2 | `kind` column and schema 5, `create_script_queue`, `ScriptItemRunner`, optional `run_id` in `ScriptRunStore.create`, claim/settle/reconcile/read branches, run id in attempt evidence, CLI entry (`dtflow script … --queue`, headless, no UI). |
| 3 | Scripts Start creates a script queue; page status and history read the item view. |
| 4 | `script_run_prompts` (script_runs schema 2), `waiting_for_input` flag, answer control path. |

## Decisions requested

- **A.** Approve one `script` queue with one item, cap 1, `kind` column (schema 5) as the shape. Alternative: a separate
  `script_queue` table outside the shared queue; rejected here because it would duplicate admission, ownership,
  listing, deletion and Stop.
- **B.** Approve that a script relaunch is a new Start (relaunch/Retry item refused for script queues in PR 2).
- **C.** Approve amending #114's "no script-specific dispatcher" and the #201 design's "No queue entries".

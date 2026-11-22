# Provider admission (#557, epic #554)

The scarce resource is an **active provider generation**, not a queue item. One dispatch owner admits generation calls
through one `ProviderPool` per backend (`cloud:-`, `local:host:port`), implemented in `services/provider_admission.py`.
Every queue the owner dispatches shares it, so the configured limit (DT+ **Default concurrency**, 1–10) holds across
queues, Scripts and Workflows.

## Slots and open calls

- A call holds one of `limit` **slots** while the provider samples it. The slot is freed as soon as the provider phase
  ends: the adapter's own sampling-complete progress (DT+ only: the last `sampling` step of the request), otherwise the
  end of its stream (`core/provider_phase.py`, a ContextVar callback the service binds around `backend.generate`; an
  adapter calls `provider_done()` once, and a call with no bound callback does nothing). Decoding, encoding and saving
  continue without a slot.
- A call stays **open** until it settles. Open calls are capped at `limit × OVERLAP` (2), so a stuck download can never
  pile up unbounded paid requests. The DT+ adapter's own lane is widened by the same factor so it is not a second,
  tighter bound; the service buffer budget is widened with it (320 MiB still reserves one 32 MiB artifact per open call).
- The limit is a utilization target. A race between claims may overshoot for a moment; nothing removes that, only
  sustained excess is prevented.

## Ordering

- Ordinary queue items take a slot **when the dispatcher claims them** (`QueueAdmission.reserve`, synchronous): no
  claimed item waits behind a gate, and the database no longer counts items that are only saving. A queue with pending
  work reports it (`want`); a call may take the next slot only if no waiting call or ready queue ranks ahead of it.
- Script and Workflow child generations, automatic retries and a video returning from its local-resource wait **wait in
  rank order** (`acquire`). Queues rank by start order (older first); a manual class (`PRIORITY_MANUAL`) sorts before
  them (see below).
- Up next queues stay reorderable until they start. The next one starts early when every live queue is open, the newest
  one is still running and the pool has capacity nobody wants (`spare`: a free slot, no waiting call, no ready queue). A
  queue that has work but cannot use a slot (repairing assets, a Script/Workflow at its child limit) does not count as
  wanting one; the oldest queue that is ready still gets the next slot. A paused or cancelled queue never overlaps.
- A paused queue claims nothing. A claimed item asks its queue twice (`can_submit`, which also counts a Pause or Cancel
  posted but not yet applied): before its attempt is created, and again after the buffer budget and image snapshot, just
  before the attempt is marked submitted. If the queue is no longer running nothing is sent and the item returns to
  pending (`queue.item_returned`). On the second check the prepared attempt is kept as `cancelled` (not submitted,
  nothing started) and the pending item takes a new request identity; on the first no attempt exists yet. If the owner
  dies between the two writes, restart reconciliation finishes that one step for a cancelled attempt whose failure code is
  `held_before_submission` (item pending under a new identity, attempt untouched); every other cancelled or unknown
  attempt still settles its item as before and nothing is replayed. An automatic retry that finds its queue no
  longer running sends nothing either: the item settles with its first failure. A retry waits for a slot after its delay,
  not during it.
- Script and Workflow runs keep their own child limit (`reserve_script_child(..., shared=True)` drops only the global
  count); a Workflow that asks for a wider run widens the pool and never narrows it.

## One Generate click (#558)

An ordinary single-image Generate click is a one-item queue on this same path, not a second route. The Queues page
creates it (`run_generate`) with the queue event `origin` = `generate`; **origin and priority are separate**: priority
is derived (origin `generate` **and** exactly one item, `persistence.queues.is_manual`), so a Generate-origin Repeat
batch keeps its origin but has ordinary priority. No schema change: the marker is a queue event, read with the queue.

- **Starting.** With this window's owner running, the queue is inserted up next and the owner's loop starts it at once,
  out of order and beside whatever runs (`claim_queued(manual=True)`, polled with the owner's 0.5 s cycle; one query).
  With no owner here it is saved paused and this window dispatches it (`StateBusyError` from another process is
  reported to Generate; the request stays paused in Queues, nothing is dispatched twice). If the owner ends before it
  started, the owner's end hands it back paused and the window dispatches it again, so an explicit Generate is never
  left parked. A start-up failure of this queue (backend would not open) pauses only that queue and reaches Generate;
  it neither releases other waiting queues nor ends early starts.
- **Priority.** `_dispatch_one` ranks it `PRIORITY_MANUAL`: the pool serves it before any not-yet-submitted ordinary
  work, older Up next queues included. Active calls are never interrupted and the pool limits and open-call cap are the
  same. While every slot is busy the dispatcher says so once (`waiting_provider_slot`, "Waiting for an available
  generation slot"); nothing is sent and no provider deadline runs.
- **Presentation.** Only this queue enables live previews (`preview_images`) and keeps Generate's output layout and
  its trigger-prefix switch (`generate`). Its events (real queue and item), saved result or failure, and end go to
  Generate through `ManualSink`; other queues keep the existing relay untouched.
- **Several clicks.** Each click is its own queue with manual priority (`_manual_runs` by queue id); Generate follows only the latest (`_manual`), events and results of earlier ones are dropped from Generate and live in Queues. If the owner ends first, all unreported clicks are handed back together (`waiting`) and dispatched again.
- **Progress presentation.** The header bar is the queue run's (`_run_view`: the dispatched queue, else the shown click's
  queue), not Generate's; `_generate_presents` is true only for runs that are not one-item queues. Events of a Generate
  queue are forwarded to the shown click and also tracked like any queue's, so the bar's stage and step come from the
  same observed events. Generate's page keeps the last stage and step and shows the seconds since the last update
  (`_stage_tick`, 1 s); no percentage or time estimate is derived.
- **Stop and Retry.** Stop cancels this queue only (a waiting one at once, a started one through a posted control; the
  service records the local-stop evidence, read beside the owner by `read_attempt_record`). Retry is the queue's own
  in-place item retry (`retry_queue_item`): same item, recorded settings and seed, a new request and attempt, the
  earlier attempt kept, then the queue goes up next again (or is dispatched here).

## Video

The call gives its slot back before waiting for `video_admission` (local pipeline, payload and disk budget, #550), takes
a slot again afterwards and frees it when the stream has been received. Open-call capping bounds how many videos can wait
that way. #550's lease, encoder cap and FFmpeg protections are unchanged.

## Diagnostics

Existing events carry the facts; three records are new: `admission.pool_opened` (backend key, limit, overlap),
`admission.slot_wait` for a call that waited 30 s or longer (waited, limit, active, open) and `admission.slot_released`,
once each time a call gives its slot back (reason `sampling_complete`, `stream_end`, `returned`, `local_wait`,
`cancelled` or `closed`, time held, active/open/limit), which shows whether DT+'s final sampling step or the stream end
freed it. Nothing is logged per
sampling step, poll or unchanged wait. `queue.item_settled` now carries `queue_id`, the automatic retry count and, for a
failed item, the recorded failure (code, message, category, submission stage, remote outcome); the provider's own error
text stays in `backend.api_failed`. Quota exhaustion is never inferred.

## Limits

- The slot is taken at claim, so a short preparation (preflight, asset check) holds it; first-use asset uploads are
  resolved before the queue's first claim. The buffer budget is widened with the open-call cap, so DT+ text-to-image
  never waits on it; a larger image edit can wait for budget while holding its slot, which is accepted rather than
  adding a reservation scheme.
- A Pause arriving after the second check, in the few awaits before `backend.generate`, can still be sent; the
  attempt is already recorded as possibly submitted by then.
- A DT+ call that sends a later `second_pass` signpost after its last sampling step is already admitted past its slot;
  the open-call cap bounds this.
- Other non-queue callers (CLI `generate`, scripts) do not take part.
- Inpaint, Controlnet and video clicks (#559) are the same one-item queues as an image: `run_generate(..., job)` stages the
  edit or pose through `create_edit_queue`/`create_pose_queue` (now taking `origin` and `queue_id`), and a video uses the
  ordinary video queue path (own lease, spool and FFmpeg limits unchanged, slot freed at sampling complete). Generate's
  status line words a queued video's phase and elapsed time; the header bar shows the queue.
- A Generate click's request is not claimed until a slot is free, so the dispatcher (not the call) reports the wait; a
  click that finds no owner here and another process owning dispatch is saved paused and not run.
- Preflight diagnostics (`run_generation`'s advisory LoRA check) are not shown for a queued single image.

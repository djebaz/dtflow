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
  them for later use.
- Up next queues stay reorderable until they start. The next one starts early only when every live queue is open, the
  last one has handed out its last item and the pool has capacity nobody wants (`spare`).
- A paused queue claims nothing, and an automatic retry that finds its queue no longer running sends nothing: the item
  settles with its first failure. A retry waits for a slot after its delay, not during it.
- Script and Workflow runs keep their own child limit (`reserve_script_child(..., shared=True)` drops only the global
  count); a Workflow that asks for a wider run widens the pool and never narrows it.

## Video

The call gives its slot back before waiting for `video_admission` (local pipeline, payload and disk budget, #550), takes
a slot again afterwards and frees it when the stream has been received. Open-call capping bounds how many videos can wait
that way. #550's lease, encoder cap and FFmpeg protections are unchanged.

## Diagnostics

Existing events carry the facts; two records are new: `admission.pool_opened` (backend key, limit, overlap) and
`admission.slot_wait` for a call that waited 30 s or longer (waited, limit, active, open). Nothing is logged per
sampling step, poll or unchanged wait. `queue.item_settled` now carries `queue_id`, the automatic retry count and, for a
failed item, the recorded failure (code, message, category, submission stage, remote outcome); the provider's own error
text stays in `backend.api_failed`. Quota exhaustion is never inferred.

## Limits

- The slot is taken at claim, so a short preparation (preflight, asset check) holds it; first-use asset uploads are
  resolved before the queue's first claim.
- A DT+ call that sends a later `second_pass` signpost after its last sampling step is already admitted past its slot;
  the open-call cap bounds this.
- Generate's single manual run, Inpaint and other non-queue callers do not take part yet (#558, #559).

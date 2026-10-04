# Plan: Phase 5B desktop queue controls

**Status:** Planned; follows Phase 5A (this plan's entry condition is 5A merged, see [the 5A plan](2026-10-04-phase-5a-desktop-generation.md))
**Milestone:** Completes the first usable Qt generation and queue paths (release 0.3.0 with Phase 5A)
**Authoritative acceptance:** `5B-O1` to `5B-O5` from [the initial plan](initial.md#phase-5-testing-ownership); Phase 3B stays authoritative for scheduling, restart and no-replay semantics
**Related plans:** [Phase 3 CLI and queues](phase-3-cli.md), [Phase 3B cloud assets](2026-10-04-phase-3b-cloud-assets.md), [Qt UX design](2026-10-04-qt-ux-design.md), [release roadmap](releases.md)

## Intent

Turn the disabled Queues preview into a working surface over the durable queues the CLI already runs: create a queue from the Generate form, start dispatch, watch stable item rows fill in, pause, resume, cancel, change concurrency, and handle an uncertain reservation explicitly. Qt stays a presenter: the scheduler, reservations, restart reconciliation, override semantics and no-replay rules are the Phase 3B services, exercised through the same functions the CLI uses. The roadmap's only live evidence for the desktop remains `5-LG1`/`5-LG2`; no new live queue run is required.

## What already exists (audit against the 5A branch)

| Already delivered | Where | Consequence for 5B |
| --- | --- | --- |
| Rolling dispatcher: one owner, rolling refill, reservations before attempts, cap and backend limit, `unknown` blocking, restart reconciliation | `services/queue.py` (`QueueDispatcher`), `persistence/queues.py` | Qt never reimplements any of it; it starts the dispatcher and reads state. |
| Control path for non-owners: `post_control` (pause, resume, cancel, override, cap) and read-only `read_queues`, over short separate connections, polled by the owner | `persistence/queues.py` | The desktop posts controls exactly like `dtflow queue pause` does, so a window and a CLI process can control the same queue. |
| Queue creation and dispatch orchestration | `cli.py` (`queue_requests`, `run_queue` add/start) | Still CLI-only; extracted first (PR 1), mirroring what 5A did for generation. |
| Background execution boundary, coalescing event relay, request building from the Generate form (including LoRAs and bound receipts), presentation mapping, session service | `ui/runner.py`, `ui/generation_page.py`, `ui/generation_presenter.py`, `ui/lora_panel.py`, `services/connection.py`, `services/session.py` | Reuse; add no second runner and no second request builder. |
| Canonical queue form with the object names the surface needs (`queueList`, `createQueueButton`, `concurrencySpinBox`, `concurrencyHintLabel`, `startQueueButton`, `pauseQueueButton`, `resumeQueueButton`, `cancelQueueButton`, `queueStateLabel`, `queueItemsView`, `emptyStateCard`, `queueProgressBar`, `itemIdLabel`, `attemptIdLabel`, `itemBackendLabel`, `uncertaintyLabel`, `overrideReservationButton`, `queueEventBrowser`, `ownershipBadgeLabel`) | `ui/forms/queue_page.ui` | Small additions only (item count spin box, refresh button, outputs list); the preview badge is repurposed as the dispatch-ownership badge. |

## Findings that shape the design

1. **Dispatch holds the state ownership lock for its whole duration.** `QueueDispatcher` runs on an owned `SQLiteAttempts`, and the lock cannot be per operation here (the owner polls controls and refills continuously). While the desktop dispatches, a single generation from the same window would hit the same lock as another process would. Decision below: the desktop treats dispatch as an exclusive activity.
2. **Controls do not need the lock.** Pause, Resume, Cancel, Override and cap are appended with `post_control` and applied by the owner within one poll interval (0.5 s) or at the next start, so the window can drive a queue that another process is dispatching, and can queue a control while nothing is dispatching.
3. **Item progress is state-level only.** The dispatcher calls `generate(request)` without an event sink, so the Queues page shows item state transitions, not per-item progress bars or previews. Adding event passthrough to the dispatcher is out of scope here (it is a Phase 3B service change); the Generate page remains the place for live progress.
4. **The memory admission budget currently admits about two real generations at once** regardless of the queue maximum (see the Phase 3B finding). The concurrency hint must say so rather than imply the number is the effective parallelism.
5. **Cancel and override both have honest-evidence wording requirements**: cancel interrupts waiting locally and never confirms remote cancellation; override acknowledges every unresolved unknown reservation on the backend, is recorded, and never resolves or replays an attempt.

## Entry conditions and preserved contracts

- Phase 5A is merged and the branch starts from current `main`.
- No network, SQLite, file or generation work on the Qt event loop; all service calls go through `AsyncRunner`.
- Qt renders immutable view data and emits intent; it imports no gRPC, HTTP, SQLite or provider types, and the CLI and services still import without Qt.
- Dispatch never uploads; queue items carry only already-confirmed prepared references bound at creation.
- A local cancel is never presented as remote cancellation; unknown outcomes are never replayed, and Resume can never clear one.
- Credential precedence is unchanged; the GUI queue path uses the saved-credential provider like Generate.
- No live generation, upload or billable action is run by an agent.

## Scope

### In

- Shared, Qt-independent queue session functions (PR 1) used by both the CLI and the desktop.
- Queue list, stable item table, per-queue progress and a status line with an explicit status vocabulary (`paused`, `running`, `blocked`, `finished`, `cancelled`).
- Creating a paused queue from the current Generate form: item count, seeds incrementing per item, the LoRA selection and any bound receipts, concurrency cap.
- Start (this window becomes the dispatch owner), Pause, Resume, Cancel, and concurrency change, each through the shared operations.
- Restart and uncertainty surface: restored paused queues, `blocked` queues, unknown items, and an explicit override behind a confirmation.
- Item inspector: identity, attempt id, backend, outputs and metadata paths, failure code, recorded queue events.

### Out

- Per-item live progress and previews (service change), gallery and thumbnails (Phase 8), batch planning and cross/pair expansion (Phase 6), priorities, fairness or adaptive scheduling (Phase 7).
- Retrying or re-queuing a failed or unknown item; there is no replay path by design.
- Editing a queue's items after creation, reordering, and deleting queues.
- A global change to the Phase 3B scheduler, buffer budget or SQLite policy.

## Pull-request sequence

Each step starts from current `main` unless the owner folds steps into one pull request (as was done for 5A); each step opens as a draft after its first coherent commit, updates this plan with decisions, and is merged by the owner only after its owned checks pass.

### PR 1: Shared queue session functions and read-only queue surface

1. **Extract the queue orchestration** from `cli.py` into `src/dtflow/services/queue_session.py`:
   - `expand_items(request, count)`: the count and 32-bit seed-range validation, fresh request ids and `queue_position` evidence (today `queue_requests`).
   - `create_queue(state, backend, request, count, cap)`: opens the store briefly, persists a paused queue, returns its id and cap (cap defaults to the persisted cloud setting for cloud, otherwise 4).
   - `dispatch_queue(state, queue_id, *, saved, timeouts, on_settle)`: the current `queue start` body (open the owned store, build the backend from the queue's recorded backend, bounded open, inventory refresh, dispatch with ordered close), returning the final queue view. A held lock raises `StateBusyError`.
   - `read_queue_views(state, queue_id=None)` and `post_queue_control(state, queue_id, action, payload)`: thin async wrappers over the existing synchronous read and post functions via `asyncio.to_thread`, including the existing validation (cap 1..4, known actions).
   - `cli.py` keeps parsing, JSON printing and exit codes only; the existing queue tests are the regression evidence and no parallel matrix is added.
2. **Presenter** `src/dtflow/ui/queue_presenter.py` (Qt-free): maps a queue view to row data, the status line wording, an enabled-controls matrix by `(status, dispatch ownership)`, the uncertainty text, and the concurrency color level (`normal` for 1 to 2, `elevated` above 2). Item states reuse one vocabulary and one level mapping (`unknown` and `save_failed` stay visually distinct from `failed`).
3. **Model and page controller**: `ui/queue_model.py` (`QAbstractTableModel` keyed by `item_id`, updated in place with `dataChanged`, never re-sorted; the form's `sortingEnabled` is turned off) and `ui/queue_page.py` (queue list, table, progress, inspector, event browser). A single in-flight read at a time refreshes on page show, after every action and on a 1 s timer while any queue is `running` or a dispatch is active here. Reads are read-only and take no lock.
4. **Enable the page shell**: remove the preview wording, keep write controls disabled until PR 2, show an ownership badge (`this window dispatches`, `another process dispatches`, `not dispatching`).

Owned checks: `5B-O1` (rows, order, identity and outputs rendered from a controlled queue state, run through `pytestqt.modeltest`), `5B-O5` (controlled out-of-order item completions update the right rows by `item_id` without reordering or misattaching outputs). Use the fake backend and real SQLite state; do not rerun the 10/4 matrix.

### PR 2: Create, control, concurrency and uncertainty

1. **Create**: `createQueueButton` builds the request from the Generate form through the existing presenter (the same field-error mapping), takes the item count from a new `queueCountSpinBox` (1 to 1000, the shared maximum) and the cap from `concurrencySpinBox`, binds confirmed receipts exactly as Generate does (`bind_prepared`, cloud only), and calls `create_queue`. The queue appears paused; creation contacts no backend and uploads nothing.
2. **Start**: calls `dispatch_queue` on the background loop; the window becomes the dispatch owner for the run. While it runs, the Generate page's single-generation and check actions are disabled with a stated reason (the lock is held); inventory refresh stays available because it takes no lock. A busy lock (another process dispatching) is reported as `Dispatch is owned by another process` and Start stays disabled while the other controls keep working.
3. **Pause, Resume, Cancel**: post through `post_queue_control`; the row and status update when the owner applies them (at most one poll interval). Resume never clears uncertainty. Cancel shows a confirmation that states pending items are cancelled, active attempts stop waiting locally, and remote cancellation is not confirmed; the resulting wording comes from the recorded item evidence.
4. **Concurrency**: `concurrencySpinBox` holds 1 to 4. A change saves the shared cloud maximum (cloud backend only, through the existing validated setting) and, when an unfinished queue on that backend is selected, posts `cap` for it; the value shows the subtle `elevated` color above 2 with no dialog. The hint states local Draw Things remains one effective lane and that the memory budget currently admits about two real generations at once.
5. **Uncertainty**: unknown items and `blocked` queues are shown distinctly with the existing evidence; `overrideReservationButton` is enabled only when an unresolved unknown reservation exists and opens a confirmation stating it acknowledges every unresolved unknown reservation on this backend, is recorded, does not resolve or replay the attempt, and means remote concurrency is no longer guaranteed. Default is No.
6. **Window close**: closing while this window dispatches asks first (active attempts are interrupted locally and their outcome may stay unknown); after confirmation the runner shutdown cancels dispatch and the service records the local interruption before exit.

Owned checks: `5B-O2` (controls call the shared operations and reflect the resulting state), `5B-O3` (cap 1..4 maps to the shared setting and the posted control, invalid values never reach dispatch, color threshold above 2), `5B-O4` (a restored paused queue and an unresolved reservation render distinctly, override is reachable only through its confirmation, and the uncertain item stays `unknown` and unreplayed afterwards). Reuse the fake backend gate and the `lost` scenario; keep dispatcher semantics in the Phase 3B tests.

### Gate: desktop composition

- Full offline checks on the final head: `uv lock --check`, `ruff check`, `ruff format --check`, `pytest` (Qt tests offscreen in the sandbox, real display on Windows).
- Windows visual review of every Queues state (empty, paused, running, blocked, finished, cancelled, unknown) and of closing during dispatch.
- Exercise the exact portable executable per the 0.3.0 gate in the [release roadmap](releases.md); packaged-executable verification is not claimed by this plan.
- No new live workflow is required: `5-LG1`/`5-LG2` cover desktop generation, Phase 3B `3-LG3` covers a cloud queue, and live runs need the owner's explicit authorization and are never run by agents.

## Decisions to take in PR 1 (defaults proposed)

- **Dispatch is an exclusive activity in the desktop** (lock held for the run; Generate's single generation disabled meanwhile). Alternative rejected for now: routing single generations through the dispatcher's open store, which would need a shared-owner design the services do not have.
- **Controls always go through `post_control`**, even when this window owns dispatch, so the window and the CLI behave identically and there is one control path.
- **One concurrency control**: it edits the shared cloud maximum and the selected queue's cap together (cloud), and only the selected queue's cap for local (shown as one effective lane). To be confirmed by the owner; the alternative is two separate controls.
- **Item count and seeds** follow the CLI: item N uses seed + N - 1, at most 1000, 32-bit overflow rejected before creation.
- **Polling interval** of 1 s for reads and the existing 0.5 s owner poll for controls; no push channel is added.

## Risks and mitigations

- **Window holds the lock and blocks the CLI during a long queue:** the ownership badge names the state, Pause or Cancel plus the end of dispatch releases it, and the CLI fails clearly with the existing busy message.
- **Frozen UI during reads or dispatch:** every call runs on the background loop; tests assert the event loop keeps processing while a gated dispatch is pending.
- **Row churn or misattached outputs:** rows are keyed by `item_id` and updated in place; the out-of-order check is owned by `5B-O5`.
- **Misleading cancellation or override wording:** wording comes from recorded item and event evidence, and both actions sit behind confirmations that state what they do not do.
- **Closing mid-dispatch loses evidence:** confirm-before-close, plus a test with the gated fake that the interruption is recorded before the runner stops.
- **Overstated parallelism:** the hint discloses the admission budget finding; the number stays a client policy, not entitlement.

## Exit criteria for Phase 5B

- `5B-O1` to `5B-O5` pass through their documented authoritative layers.
- A queue can be created, started, paused, resumed, cancelled and overridden from the desktop through the same service functions as `dtflow queue`, with no scheduling, reservation or persistence logic in `ui/`.
- Unknown reservations and restored paused queues are visible and distinct; neither Resume nor any UI path replays an uncertain attempt.
- Dispatch never uploads; cancel and override wording is honest and never claims remote cancellation or resolution.
- Docs (`README.md`, `initial.md`, runtime layout for the dispatch lock lifetime, `unreleased.md`) distinguish what is wired from what remains preview-only; unverified items (Windows visual review, packaged executable) are stated as unverified.

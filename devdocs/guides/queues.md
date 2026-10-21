# Queues and batch plans

Queues are available on the desktop Queues page and through the [CLI](cli.md). Creation persists paused items and does not generate. Start dispatches them; local/cloud dispatch submits real generation and may consume credits. Cancel interrupts local waiting, not confirmed remote execution. Unknown attempts are never silently replayed, and they never block other work.

## Desktop controls

**+ New queue**, at the top right of the center, opens the composer in the center in place of the selected queue; **Close**, or selecting a queue in the list, returns to it, and the composer keeps its values. The composer starts with a one-line summary of the Generate settings it builds on (prompt, model, size, steps, guidance, sampler, seed, LoRAs, Inpaint strength) and **Edit in Generate**; coming back to Queues refreshes it.

**Variation → Mode** is **Repeat** (N items with seeds s, s+1, ...) or **Cross** / **Pair** for comparisons. In Cross and Pair, each dimension is a row of values you pick, shown as chips (click a chip to remove it): **Models** and **Samplers** open the shared searchable list (models come from the Generate inventory; *Not listed* accepts an exact identifier), **Sizes** offer Generate's size and common presets or a typed `WIDTHxHEIGHT`, and **Steps**, **Guidance** and Inpaint **Strengths** use a bounded number box with **Add**. Prompts are one per line; seeds (`1; 2; 10..20`) and LoRA weights (`IDENTIFIER: 0.6; 1.0`, one LoRA per line) stay typed. The item count updates as you edit and the preview table of the exact items follows a moment later; there is no Dry run button. Errors mark the field and appear in the count line. The same bounded planner serves the CLI, and plan files are unchanged.

The buttons at the top right of the composer create the queue: **Start now (N items)** creates it and starts it from this window (real, possibly billable generation; disabled while this window dispatches or generates, and while another process dispatches a queue), **Add to queue** saves it *up next* behind the running queue so it starts by itself (enabled only while a queue runs with a live dispatch owner), and **Save paused** saves it paused for a later Start. **Max at once** beside them sets the new queue's cap.

The left rail lists queues grouped as **Running**, **Up next** (in start order), **Paused** and **History** (finished and cancelled), newest first within a group. Each row shows the first prompt, model, item count and what the items vary in (for example `2 prompts, 2 step counts`), the backend, the creation time, a status chip and a small progress bar by outcome. The filter box matches any of those words, the status or the queue id. The list reads queue summaries only (counts and the first item); items and events are read for the selected queue alone, and it refreshes by itself every few seconds while the Queues tab is open (every second while anything runs), so queues created from the CLI appear without Refresh.

The selected queue's header has one status chip (its tooltip says which process dispatches it), one primary action that changes with the state (**Start**, **Start next**, **Pause** or **Resume**), **Cancel**, its own **Max at once** (1 to 10, fixed once the queue has finished) and **Remove**. Below it, a segmented bar shows settled items by outcome, and the ✓ ✗ ? – count chips filter the items table to succeeded, failed, no-result or cancelled items; with none checked every item shows. Outcomes of actions appear in the shared bottom overlay. **Remove** (also on the list's context menu) deletes one paused, finished or cancelled queue record after an inline confirmation; **Clear history** removes finished and cancelled ones and keeps paused queues. Neither touches attempts or saved images, and a running or up-next queue cannot be removed.

Queues and items stay in stable order. Start, Pause, Resume and Cancel use the shared queue services. Cancel takes effect immediately, without confirmation, and does not confirm remote cancellation: an item interrupted after submission ends as *no result*. The item panel summarizes outputs with Open folder; shortened attempt IDs expose full IDs in tooltips.

The right-side event evidence panel presents a compact activity stream keyed by queue position (for example, `#3 submitted` and `#3 completed - 12.34 s`). The durable queue evidence still records selected backend lifecycle events (preparing, submission boundary, received final artifacts and terminal response) plus item settlement; the UI hides redundant low-level lines and full request/item identifiers from the default view. Successful settlement includes the same client-observed backend duration saved with the image metadata. That duration runs from submission boundary to terminal response and may include provider-side queueing or serialization; DTFlow does not call it pure generation/inference time. Progress and preview events are intentionally not persisted into this diagnostic stream.

**Max at once** in the header posts a cap for the selected paused, waiting or running queue only. **Max at once** in the composer sets the cap of queues you create; with cloud chosen it also shows and saves the shared cloud default for new cloud queues. Values above two are tinted, and the limits are explained in the tooltips. Backend and shared memory limits still apply. A saved change shows no overlay; a refused one does.

Start holds the state ownership lock for the entire dispatch run. Other processes can read, post controls and add paused queues, but cannot own another generation or dispatch in that state directory. See [runtime ownership](../architecture/runtime-layout.md).

### Editing and up next while a queue runs

Every Generate, LoRA, Inpaint and Plan input stays editable during a generation or a queue run (#133). A running request is the immutable snapshot taken when it started; edits only shape the next request or queue, and Generate's Effective request says so while a single run is active. The composer stays available during a run: **Save paused** saves a paused queue and **Add to queue** puts it up next.

While this window dispatches, **Start** on another paused queue reads **Start next**: the queue waits *up next* and starts by itself, on the same dispatch ownership, when the running queue ends (#125). Up-next queues run in the order shown in the list's Up next group (`Up next 1`, `Up next 2`, ...), each on its own recorded backend. **Earlier** and **Later** (shown only for a waiting queue) move it; **Pause** takes it out of the order and leaves it paused. Pressing **Generate** while a queue runs adds the request behind it as a one-item up-next queue (an Inpaint edit is staged the same way as a queued edit). The header run control counts the waiting queues.

**Cancel** on a waiting queue cancels it at once (it holds no reservation, so nothing of it ran); from the CLI, `queue pause` and `queue cancel` on a waiting queue likewise take it out of the order or cancel it immediately. The CLI cannot enqueue or reorder, and `queue start` runs only the queue it names.

Auto-play continues after a queue finishes or is cancelled; a paused running queue holds the order until you Resume or Cancel it. Picking the next queue and starting it are one step, so a Pause, Cancel or reorder made just before is respected. Auto-play stops when dispatch itself ends: a failure, a local stop or closing the window. Whenever the owner stops, and whenever a new owner opens the state (a restart included), every waiting queue returns to paused, so nothing starts without a live owner. Joining the order needs a live owner: once the running queue has ended, **Start next** is refused and Generate saves its one-item queue paused, saying so.

### Elapsed time and ETA

The shared bottom progress row and the header Run control tooltip show elapsed wall time since the latest dispatch start, including pauses and provider waits. A new dispatch after a stop or restart starts a new elapsed interval. If no reliable start timestamp exists, elapsed time is omitted.

After the **first successful item with a usable recorded duration**, a labeled estimate appears: median successful `backend_elapsed_seconds` × remaining items (pending + running) ÷ items actually running. Failed, cancelled and unknown items do not contribute durations; the configured maximum is never substituted for observed concurrency. A separate presentation timer counts down once a second, even while a database read is pending. Unchanged polls do not restart it; each observed item settlement, timing-sample update or change in running concurrency rebases it. The display uses minutes:seconds (hours:minutes:seconds for longer runs), reaches zero without claiming completion, and disappears while paused, cancelled, finished, without an active owner or with no running items. Resume starts a fresh estimate. Plans varying model, size or steps say the estimate is rough.

Resume rebasing uses the latest applied durable Resume event, so even Pause and Resume between two polls reset the estimate. This leaves elapsed time's dispatch-start timestamp unchanged.

Durations include provider-side queueing and are not pure inference time. The blue bar continues to show real settled counts, never estimated progress. After each item settles, the dispatcher records a durable timing snapshot event (elapsed wall time and the ETA at that moment, counting the lane the next claim refills). The Events pane lists it as `elapsed + ETA = projected total`; it is skipped before the first successful duration sample and is not a measured completion time. The countdown seeds from a snapshot only while it is newer than the latest start or Resume and no live settlement, sample or lane change has superseded it.

## CLI examples

```bash
uv run dtflow queue add -b fake -p "Offline fixture" -k 10 -j 4   # persist a paused queue of 10 items (seeds s, s+1, ...)
uv run dtflow queue add -b fake -p "Offline fixture" -r plan.json -u   # dry run: print the exact items of a plan, create nothing
uv run dtflow queue add -b fake -p "Offline fixture" -r plan.json   # persist a paused queue of the planned items
uv run dtflow queue start QUEUE                                   # become the dispatch owner and run it (Ctrl-C interrupts)
uv run dtflow queue pause QUEUE                                   # from another terminal; also resume, cancel, cap -j N
uv run dtflow queue show QUEUE                                    # items in queue order with state, attempt and outputs
```

## Dispatch, restart and uncertainty

`queue add` persists independent, immutable resolved items (it contacts no backend and does not support `-r/--prefix`; put prefixes in the prompt) in a queue that starts paused. `queue start` makes that process the single dispatch owner of the state directory, resumes the queue and rolls: it admits the next item as soon as any reservation is released, never exceeding the queue maximum (`-j` on `add` or `queue cap`) or the backend's own limit (local gRPC is one lane).

Another process can run `list`, `show`, `add`, `pause`, `resume`, `cancel` and `cap` while dispatch is running: controls are stored and polled, a queue added meanwhile is saved paused over a short separate connection, and `start` needs the state directory to itself. Up next is a desktop feature: the CLI has no command to queue behind a running dispatch, and `queue start` runs the queue it names.

A paused, idle dispatcher keeps polling until resumed or interrupted. `cancel` stops pending items and interrupts local waiting on active ones; it never claims remote cancellation, so an item interrupted after submission is `unknown`.

An `unknown` item (shown as *no result* on the desktop) means the request may have reached the server but no final result arrived, so it most likely failed. It releases its reservation, never blocks the backend, other queues or the rest of its queue, and the attempt is never replayed automatically. `queue start` exits `3` when any item is unknown, failed, cancelled or pending. The former `queue override` command is a hidden, deprecated no-op kept for old scripts; it prints a note and records nothing.

Local Draw Things runs one request at a time and stopping local waiting does not confirm that it stopped rendering. After a *no result* item, the next item is dispatched right away and may wait on the server behind the abandoned job; its total deadline includes that wait. This is accepted: the server queues requests itself, and a local run costs no credits.

After a restart queues are restored paused, completed results are kept, and no item that reached the remote boundary becomes eligible again. `queue start` exits `0` when every item succeeded, otherwise `3`.

Direct DT+ text-to-image reserves 32 MiB per in-flight request against a 320 MiB shared admission budget, permitting up to ten client-side submissions when Max at once is ten. This is not measured process RAM or verified server-side entitlement. Image edits can reserve more; local Draw Things remains one lane. The cloud default stays four until changed.

## Plan files and dry runs

`queue add -r/--plan FILE` replaces `-k/--count` (the two are mutually exclusive) with a bounded, deterministic batch built from the resolved base request (the usual `-p`, `-m`, `-c`, `-l` options) plus varied dimensions; `-u/--dry` prints the exact item table (index, varied values, resulting seed) and the count as JSON and persists nothing, for a plan or a plain `-k`. A plan file is versioned, credential-free JSON, user-supplied (it is never copied into state; each queued item records its resolved request and `plan.*` evidence):

  ```json
  {"schema": "dtflow.queue-plan", "version": 1, "mode": "cross",
   "dimensions": {"prompt": ["a cat", "a dog"], "steps": [4, 8], "size": [[512, 512], [768, 512]],
                  "lora_weight": [{"identifier": "EXACT_LORA", "weights": [0.6, 1.0]}]}}
  ```

  Modes are `seed` (`"count": N`, seeds increment from the base seed, the `-k` rule), `pair` (equal-length dimensions matched position by position; unequal lengths are an error, never truncated) and `cross` (every combination, the last listed dimension varying fastest).

Dimensions are `prompt`, `model`, `seed` (a list or `{"start": S, "count": N}`), `steps`, `guidance`, `sampler`, `size` (`[width, height]` pairs, never independent axes) and `lora_weight` (weights for a LoRA already given with `-l`; no compatibility is inferred).

In `pair` and `cross` the base seed stays fixed for every item unless a `seed` dimension is listed or the plan sets `"seed": "increment"`.

A plan larger than 1000 items, an empty dimension or duplicate combinations without distinct per-item seeds, unequal pair lengths, a value the request validators reject or a seed overflow fails before anything is created.

Plan files use `dtflow.queue-plan`, version 1. Repeated dimension values are allowed only when each item gets its own seed; otherwise duplicate combinations are refused. See the [planning decisions](../plans/2026-10-04-phase-6-queue-planning.md#decisions-taken-in-the-pull-request) for the precise contract.

## Related documents

- [Desktop guide](desktop.md)
- [CLI reference](cli.md)
- [Backend contract](../architecture/backend-contract.md)

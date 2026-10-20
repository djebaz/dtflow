# Queues and batch plans

Queues are available on the desktop Queues page and through the [CLI](cli.md). Creation persists paused items and does not generate. Start dispatches them; local/cloud dispatch submits real generation and may consume credits. Cancel interrupts local waiting, not confirmed remote execution. Unknown attempts are never silently replayed, and they never block other work.

## Desktop controls

Create a queue of N items from the Generate form (seeds s, s+1, ...), or enable Plan batch for Pair or Cross comparisons. Plan batch accepts prompts one per line, other dimensions separated by semicolons, and LoRA weights per line. Inspect the live count and Dry run table before creating the batch; the same bounded planner serves the CLI.

Queues and items stay in stable order. Start, Pause, Resume and Cancel use the shared queue services. Cancel takes effect immediately, without confirmation, and does not confirm remote cancellation: an item interrupted after submission ends as *no result*. The item panel summarizes outputs with Open folder; shortened attempt IDs expose full IDs in tooltips.

The right-side event evidence panel presents a compact activity stream keyed by queue position (for example, `#3 submitted` and `#3 completed - 12.34 s`). The durable queue evidence still records selected backend lifecycle events (preparing, submission boundary, received final artifacts and terminal response) plus item settlement; the UI hides redundant low-level lines and full request/item identifiers from the default view. Successful settlement includes the same client-observed backend duration saved with the image metadata. That duration runs from submission boundary to terminal response and may include provider-side queueing or serialization; DTFlow does not call it pure generation/inference time. Progress and preview events are intentionally not persisted into this diagnostic stream.

One concurrency control sets the selected paused or running queue's cap. With cloud chosen and no such queue selected, it edits the shared cloud maximum instead; values above two are tinted. Backend and shared memory limits still apply.

Start holds the state ownership lock for the entire dispatch run, disabling desktop Generate. Other processes can read and post controls, but cannot own another generation or dispatch in that state directory. See [runtime ownership](../architecture/runtime-layout.md).

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

Another process can run `list`, `show`, `pause`, `resume`, `cancel` and `cap` while dispatch is running: controls are stored and polled, and `add` and `start` need the state directory to themselves.

A paused, idle dispatcher keeps polling until resumed or interrupted. `cancel` stops pending items and interrupts local waiting on active ones; it never claims remote cancellation, so an item interrupted after submission is `unknown`.

An `unknown` item (shown as *no result* on the desktop) means the request may have reached the server but no final result arrived, so it most likely failed. It releases its reservation, never blocks the backend, other queues or the rest of its queue, and the attempt is never replayed automatically. `queue start` exits `3` when any item is unknown, failed, cancelled or pending. The former `override` control is a hidden no-op kept for old scripts.

After a restart queues are restored paused, completed results are kept, and no item that reached the remote boundary becomes eligible again. `queue start` exits `0` when every item succeeded, `4` if any item is unknown, otherwise `3` if any failed, was cancelled or remains pending.

Note: the shared buffer budget (256 MiB, about 100 MiB reserved per request) currently admits only about two concurrent real generations, whatever the queue maximum.

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

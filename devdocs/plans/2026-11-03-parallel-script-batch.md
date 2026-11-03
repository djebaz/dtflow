# Plan: bounded parallel generations from scripts

**Status:** Runtime batch implementation and deterministic test sources are present in draft PR #218. This follow-up performs source inspection only; native/provider acceptance and the two concurrency-lifecycle review findings remain open.
**Base:** `main` after squash merge of PR #217 (`2569104a338ad7995b2164b050cbb5847f464d59`).
**Scope owner:** Separate feature PR. Do not fold these runtime changes back into merged PR #217.

## Goal and compatibility contract

Allow one DTFlow script to generate up to its authorized run limit (currently default 30, accepted range 1..32) with independently configured requests executing concurrently **only when the selected backend and global queue admission permit it**. This is different from changing the script's total-generation ceiling.

- Propose an **opt-in**, DTFlow-only `pipeline.runBatch(jobs, maxConcurrent)` facade installed through the engine overlay and a dedicated host hook. Do **not** edit the pinned upstream `shared_script.js`.
- Keep ordinary `pipeline.run()` and the Draw Things API unchanged. Scripts not explicitly using the new facade retain sequential behavior.
- Adapt `tools/scripting/krea2_lora_mixer_portable.js` to expose **Max concurrent jobs** in the DTFlow `requestFromUser` form (initial proposed value 4; integer 1..10). In Draw Things, keep the original sequential execution and canvas-saving path.
- The requested value is an upper bound, **not** a guarantee of simultaneous remote requests. Effective concurrency must respect backend capabilities, the shared cloud/local queue limit, and other already-reserved work. If safe shared admission cannot be implemented, fail closed or run sequentially rather than bypass the queue.
- The first API should be a synchronous-from-JavaScript batch call with independently snapshotted job descriptions and a bounded, order-associated result list. The host may perform asynchronous work while the JavaScript child waits. Do not require top-level `await` support.
- The script must not use shared `pipeline.configuration` mutation or a single canvas as the source for concurrent results; save each result through its own committed attempt/output identity.

## Required implementation boundaries

1. **Validate before submission.** Accept a bounded list of independent text-to-image requests with the existing `configuration`, `prompt`, and `negativePrompt` semantics. Reuse the current supported-field and model/LoRA validation; reject changed unsupported fields and masks before generation. Count jobs toward the run's `max_generations` ceiling, including any preceding sequential calls. Do not partially launch a batch that exceeds the ceiling.
2. **Durable child attempts.** Give each admitted job a unique attempt ID, link it to the ScriptRun ledger **before** any possible provider submission, and retain the job's input order, resolved prompt, seed, model, sampler, LoRA identities/weights and output paths in the existing durable metadata. Preserve result-to-job mapping when completion order differs from submission order.
3. **Global concurrency accounting.** Today a whole script occupies one `KIND_SCRIPT` queue item, while `QueueMixin._reserved()` counts running queue items. Introducing concurrent children without adjusting reservations could exceed the global cap. Choose and verify one authoritative mechanism shared with ordinary queue generation, overlapping queues, and other scripts; never rely on a host-local semaphore alone.
4. **Lifecycle and failures.** After Stop or loss of admission, schedule no further jobs. Do not silently cancel or retry already-submitted remote work; drain/settle their durable evidence. Preserve successful outputs even if another child fails. An unknown remote outcome or output-save failure must be visible and must prevent unsafe subsequent submissions. No automatic replay on restart.
5. **Per-job outputs and UI.** Expose explicit per-job statuses, attempt IDs and committed outputs without reading or clearing a shared canvas. Keep the script itself one run-level queue entry with inspectable children in Queues/Images. Add sanitized concurrency/attempt diagnostics without leaking prompts, credentials or binary data.
6. **Setup consent.** Native `Inspect > Setup > Run Script` continues to perform zero provider work before the explicit Run Script action. Later forms still use the existing Continue bridge. Form edits, stored suggestions and backend changes must not silently grant new dispatch consent.
7. **Runtime compatibility.** The optional facade is absent in real Draw Things, so the portable mixer detects `typeof pipeline.runBatch === "function"` and falls back to the existing per-image `pipeline.run()` plus canvas saving. Never claim that the proposed method is an upstream Draw Things API.

## Acceptance gates

- Deterministic fake-backend tests for batch size limits (0/1/30/31/32 where applicable), numeric concurrency bounds, backend cap, shared cap, and no over-reservation across concurrently active ordinary queues.
- Demonstrate actual **overlapping submissions**, not only overlapping JavaScript or UI calls, with a controlled fake backend: compare active counts at 1, 2 and 4; verify that cap 1 remains sequential.
- Verify stable association of completion and saved artifacts to the original ordered job descriptions under out-of-order completions.
- Exercise validation failures before any attempt, mid-batch safe failure, unknown outcome, save failure, Stop before/after admission, process death, lost Setup, and restart/recovery without automatic replay.
- Verify the same portable script's **Draw Things sequential fallback** with mocked Draw Things hooks and its DTFlow-specific form field/optional batch dispatch. Verify that the existing sequential mixer workflow is unchanged when the extension is absent.
- Keep tests/provider runs distinct: all routine checks use fake backends and disposable state. Owner-controlled real local/cloud smoke, Windows UI, packaged executable and any cloud-credit consumption are separate, explicit acceptance steps.
- Run repository-prescribed Python syntax/import/help validation and focused pytest checks once runtime code is added; record concrete commands/results instead of inferring success.

## Initial investigation / known constraints

- `scripting/host.py:ScriptHost.generate_image` currently blocks on one `run_generation()`, links one child attempt, and uses a latest-result virtual canvas.
- `scripting/engine.py` provides the optional DTFlow host marker but no batch API. `scripting/hooks.py` and `runner.py` validate and dispatch the existing synchronous JSON bridge. The vendored upstream facade must remain untouched.
- `services/queue_session.py:create_script_queue` creates a **single script queue item** with queue cap 1; global queue reservations currently count queue items, not script children.
- The current Krea2 mixer already derives deterministic per-job LoRA and seed configuration. Those snapshots are a suitable batch input, but its canvas-based save calls cannot safely run concurrently.

## Delivery state

The initial planning-only description is historical: the branch now includes the batch bridge/host, shared child reservations, portable mixer concurrency form and deterministic test sources. Their presence does not establish runtime acceptance. No tests, checks, renders, app launch, asset transfer or generation were executed during this follow-up.

## Owner follow-up: concurrency and queue-equivalent LoRA readiness

- Owner reports apparent concurrency ceiling 4 and requests automatic LoRA readiness “like in queues.” The mixer requests 4 by default but accepts 1..10; the batch pool takes the minimum of that request and backend capacity. The shared setting currently reads 10. Do not silently change the shared policy, infer a provider-side ceiling, or promise ten simultaneous remote requests from source inspection.
- Read-only retained evidence: the latest five persisted concurrency answers are 3, 3, 3, 5 and 4 (newest first). This does not establish a fixed ceiling of four or backend acceptance. The retained log contains an unknown transport outcome; do not replay any job to investigate concurrency.
- [x] Give cloud scripts the same confirmed local asset-receipt pool used for queue LoRA sets, independent of the LoRAs selected on Generate. Read it without a provider during Setup; reload it at actual admission, attaching it before releasing the preparation gate.
- [x] Resolve script-selected LoRAs against that pool, preserving explicit starting-request bindings and weights, selecting only receipts actually used, and recording their IDs in request evidence. Existing dispatch-time backend/account/receipt checks remain authoritative. `createLoRA` can resolve an exact prepared identifier absent from raw Echo inventory.
- This is automatic binding of existing prepared assets, not automatic downloading or uploading. As in Queues, genuinely missing files still require the existing explicit Prepare workflow; unknown/failed receipts are not substituted, reconciled, retried or uploaded by generation. No new asset protocol, source-folder scan or download URL inference is introduced.
- [ ] Owner smoke: script chooses a prepared custom LoRA not selected on Generate; mixed built-in/custom LoRAs; stale/account-mismatched receipts fail before submission; requested concurrency 1/5/10 under the existing shared cap.
- [ ] Earlier review findings remain open: close intake checks across pre-submission awaits, and drain all workers after a slot-release failure. This LoRA follow-up does not repair or validate those findings.

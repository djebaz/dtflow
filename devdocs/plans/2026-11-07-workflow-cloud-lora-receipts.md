# Workflow custom cloud LoRA binding

Date: 2026-11-07. Branch: `fix/workflow-cloud-lora-receipts`, based on current main `8e287f1899c79f828eee39d9fb74684642a372e5` (5.5.0).

## Report and source evidence

The owner reports prepared custom DT+ LoRAs work in ordinary Generate and Queues but appear ineffective in Workflows Generate and Inpaint nodes. The workflow node resolver preserves names/weights, but the shared workflow submission path did not attach prepared receipts. `wire_lora_name` consequently retains the filename instead of the uploaded content SHA-256, and `custom_lora_override` has no prepared identifiers to describe. This is a source-confirmed binding gap, not an independently reproduced live-model comparison.

## Scoped correction

- At shared workflow dispatch, load the same confirmed local receipt pool used by Scripts/queue authoring and bind only identifiers selected by that node through existing `session.bind_prepared`.
- Cover Generate, Inpaint and Img2img without adding receipt data to portable workflows or depending on ordinary Generate's selection.
- Keep generation-service fresh account/backend/receipt validation, weights, checkpoint settings, durable evidence and existing cloud wire mapping authoritative. No new upload/download, provider fallback or replay.
- Recheck Pause and Stop after receipt-loading awaits. Wait for Resume before image submission; cancellation retains the existing attempt/submission accounting and reserved-slot cleanup.

## Validation boundary

Owner explicitly requested no added tests and no local test execution; regression tests are not added and existing tests are left to GitHub CI. Only Ruff format/check is run locally before commits. The subsequently requested warning UI has offline fixture definitions; rendering is left to the GitHub Action. No local renders, live generation, asset preparation/upload or model inference.

## Review follow-up and acceptance

- Owner smoke of the original fix confirmed that workflow LoRAs now apply. This is owner-reported behavior, not agent-executed live generation, and predates the Pause correction below.
- CI [42380963298](https://github.com/djebaz/dtflow/actions/runs/42380963298) passed lock, Ruff format/lint and offline pytest at `2d29b4dfff42118315ae5f9a96347277996ea1c0`; UI rendering was skipped. Later-head CI remains separate.
- L1: document invalid bound-receipt failures and account/reconcile/explicit-Prepare recovery. Existing `failure_text` delegates to the shared `present_generation_failure`, which labels `prepared_asset_*` failures as "Prepared asset not validated" and retains the sanitized cause. No competing failure presenter or new UI state is introduced.
- L4: honor a Pause observed during receipt binding before calling `run_generation`; Stop/cancel still wakes or cancels the wait and settles without a new attempt.
- Remove process/validation text from user-facing unreleased notes. Keep Markdown prose unwrapped per configured editing guidance.
- L2/L5 follow-up (owner requested): retain bounded per-LoRA preflight availability/code and a prepared boolean on each step, with no receipt IDs/endpoints. Cloud `availability_unknown` without a bound receipt creates an advisory warning, not a claim that all unbound LoRAs are ignored. The warning appears in a deduplicated transient amber overlay, retained workflow output/node details and a durable queue activity event. Default diagnostic logs receive only code/count/node-type data through the shared producer API.
- L3 receipt-pool loading/snapshot policy and display of positive receipt provenance remain deferred; raw receipt identities are deliberately not exposed in the UI.
- Add `workflows-12-lora-warning` and `queues-75-workflow-lora-warning` offline preview states to the existing catalogue/component packs. GitHub Actions renders them; no new render assertions or local render execution.
- At `0393cf7`, CI [42382237260](https://github.com/djebaz/dtflow/actions/runs/42382237260) reported 691 passed, 3 skipped and one timeout in the existing ordinary-queue Pause/Resume/Cancel test. The wait incorrectly required concurrent backend-arrival order `[0, 1, 2, 3]`; a valid `[0, 1, 3, 2]` never satisfies it. Change only that existing test's arrival checks to sorted-list equality, preserving duplicate/count checks, no-refill/cancel checks and durable outcomes. FIFO admission remains covered at `claim_next` in 3B-O1. The failed run did not report the actual start list, so arrival reordering is a plausible explanation, not a proven diagnosis of that run. No new tests or local test execution; subsequent CI is required.
- Warning UI renders [42382237271](https://github.com/djebaz/dtflow/actions/runs/42382237271) and workflow preview [42382237344](https://github.com/djebaz/dtflow/actions/runs/42382237344) succeeded at `0393cf7`; this establishes job completion, not independent visual/native acceptance.

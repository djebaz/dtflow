# Workflow custom cloud LoRA binding

Date: 2026-11-07. Branch: `fix/workflow-cloud-lora-receipts`, based on current main `8e287f1899c79f828eee39d9fb74684642a372e5` (5.5.0).

## Report and source evidence

The owner reports prepared custom DT+ LoRAs work in ordinary Generate and Queues but appear ineffective in Workflows Generate and Inpaint nodes. The workflow node resolver preserves names/weights, but the shared workflow submission path did not attach prepared receipts. `wire_lora_name` consequently retains the filename instead of the uploaded content SHA-256, and `custom_lora_override` has no prepared identifiers to describe. This is a source-confirmed binding gap, not an independently reproduced live-model comparison.

## Scoped correction

- At shared workflow dispatch, load the same confirmed local receipt pool used by Scripts/queue authoring and bind only identifiers selected by that node through existing `session.bind_prepared`.
- Cover Generate, Inpaint and Img2img without adding receipt data to portable workflows or depending on ordinary Generate's selection.
- Keep generation-service fresh account/backend/receipt validation, weights, checkpoint settings, durable evidence and existing cloud wire mapping authoritative. No new upload/download, provider fallback or replay.
- Recheck Stop after receipt-loading awaits and settle the reserved child slot through the existing cleanup path without submitting.

## Validation boundary

Owner explicitly requested no added tests and no local test execution; regression tests are not added and existing tests are left to GitHub CI. Local validation is limited to syntax, lint/format and diff inspection. No UI changes/renders, live generation, asset preparation/upload or model inference. Real DT+ Generate/Inpaint workflow acceptance remains owner smoke; fixture/CI success cannot establish visual LoRA effect.

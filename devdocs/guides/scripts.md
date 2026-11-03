# Scripts

DTFlow runs locally imported Draw Things JavaScript through its pinned upstream facade and bounded host. Unsupported effective configuration changes fail rather than being silently applied. Scripts currently generate text-to-image requests; image pickers do not enable canvas pixels, masks or image-conditioned generation.

## Inspect, Setup, Run Script

Import or select a `.js` version in Generate > Scripts. Inspect shows source-only syntax/hook information while Setup preloads the first actual form in a bounded JavaScript child, using cached inventory and locally confirmed LoRA receipts. Preparation creates no queue item, opens no provider session and submits no generation. This is not a promise that arbitrary JavaScript is evaluated without side effects inside its own confined sandbox.

Choose Setup to edit the form, then Run Script to create the ordinary one-item queue and admit the same parked execution. Later dynamic forms appear there with Continue. Back to form and Reload Setup rebuild preparation rather than replaying a completed run. Settings and answer suggestions never grant generation consent automatically. A generation-first script receives a ready prompt; a script with neither forms nor generation can finish during preparation.

Forms preserve nested section answer arrays. `image` displays supplied raster images and optionally returns a selected index; `imageField` and `directory` use native pickers. Image inputs are normalized owned snapshots, and folders become bounded, nonrecursive owned image copies, not arbitrary external filesystem access. The DTFlow-only descriptor extension `{ ...this.imageField("Reference", false), multiple: 64 }` optionally centre-crops the imported copy to the chosen pixel grid; omit `multiple` for unrestricted dimensions. Original files are unchanged.

## Remove a saved script version

Each library card has a rounded-square × button at its top right. Click once to arm the red ✓ button, then click again to remove that version; no confirmation overlay opens. Escape or changing selection/view cancels. Save or discard a dirty draft first; active runs cannot be removed from their card. Removal frees a library slot but preserves immutable source bytes for queued runs, history, preferences and generated outputs. Reimporting the source adds it back.

## Optional parallel batches

The [portable Krea2 adaptation guide](krea2-mixer.md) documents PR #219's cached LoRA enumeration, single-form Setup, source-version preferences and explicit bounded chunks. Its DTFlow-only helpers are feature-detected; the original Draw Things fallback and owner acceptance boundaries remain separate.

`pipeline.runBatch(jobs, maxConcurrent)` is a DTFlow extension, not an upstream Draw Things API. Ordinary `pipeline.run()` remains sequential. Each job accepts the existing `configuration`, `prompt` and `negativePrompt` values, plus an optional sandbox-relative PNG `outputFile`. Masks are unsupported. Jobs are snapshotted and validated before any batch child is linked or submitted; backend/inventory/preflight failures can still occur later.

```javascript
const jobs = [101, 102].map(seed => ({
  configuration: { ...pipeline.configuration, seed },
  prompt: "A landscape",
  negativePrompt: "",
  outputFile: `landscape-${seed}.png`
}));

if (typeof pipeline.runBatch === "function") {
  const results = pipeline.runBatch(jobs, 2);
  // Inspect every result.status before deciding what to do next; never blindly retry.
  console.log(JSON.stringify(results));
} else {
  // Supply the script's normal Draw Things sequential path here.
}
```

This example submits real work only when run in an admitted script; cloud generation may consume credits. The portable example `tools/scripting/krea2_lora_mixer_portable.js` contains the actual Draw Things sequential/canvas-saving fallback, with an optional DTFlow batch path. It is an adapted example, not evidence that the unchanged iOS mixer or every model/LoRA combination is supported.

The concurrency argument is an integer 1..10 (default 1). The portable mixer form defaults to 4 and allows 1..10. Actual concurrency is bounded by backend capacity, the shared cloud concurrency policy and other queue reservations; it is not verified cloud entitlement or guaranteed simultaneous provider work. Local Draw Things remains single-lane. Parallel batches require a queued script with shared child-slot accounting; headless runs without that accounting reject requested concurrency above 1. Batch size also respects the remaining per-run generation cap, including earlier sequential calls; parallelism does not increase that cap.

Results stay in input order, even when completion order differs. Each reports `status`, `attemptId` and `outputPaths`; success can include `savedPath`, and failures can include `error`. Statuses are `succeeded`, `failed`, `unknown`, `save_failed` and `not_started`. An already linked but unsubmitted job may have an attempt ID with `not_started`. Durably committed outputs remain authoritative; batch output does not require selecting a shared canvas. Sandbox exports must be unique `.png` destinations and never overwrite existing files.

Adapted chunk jobs may also supply the pair `planId` (64 lowercase hexadecimal SHA-256 characters) and `jobIndex` (integer 1..10000). They become `script_plan`/`script_job` request evidence, not replay/deduplication authority or additional dispatch consent. The mixer guide explains fingerprinting and explicit manual continuation.

Stop closes intake and stops waiting; it does not cancel remote work or permit replay. Unknown outcomes and save failures close intake; waiting workers recheck before generation. Already admitted workers drain before parent settlement/backend closure, including when a lane release fails. Failed releases propagate after draining rather than being retried or silently deleted. Inspect existing attempts before considering another run.

## LoRA readiness

Cloud scripts use the same confirmed local receipt pool as queue LoRA sets, even when those LoRAs are not selected on Generate. Setup reads local records; admission refreshes the pool before permitting generation. Only receipts for selected LoRAs enter a child request, and explicit starting-request bindings take precedence. Fresh backend/account/receipt validation is still required. A stored receipt is not proof of present server availability or model compatibility.

Genuinely missing files still need the existing explicit Prepare workflow. Scripts do not automatically download models, upload files, reconcile uncertain receipts or substitute credentials. Built-in inventory LoRAs retain their normal path.

## State and acceptance

PR #218 upgrades the attempts database to schema 6 for script child reservations, using the existing backup-then-transactional migration. Older writers refuse newer schemas; do not downgrade an active database. Restart clears stale lane reservations without replaying uncertain children. The ScriptRun ledger remains separate. Back up valuable runtime state before upgrading, and never use a database rollback as authorization to resubmit uncertain work.

CI at runtime head `0465f3d` checked out that exact commit and reports 697 passed, 3 skipped; format and lint also passed in [run 41613970580](https://github.com/djebaz/dtflow/actions/runs/41613970580). UI rendering was skipped. This does not establish exhaustive fault coverage, Draw Things fallback acceptance, provider concurrency, Windows/DPI, packaged execution or checkpoint/LoRA compatibility. Those remain owner-controlled gates, separate from squash merge.

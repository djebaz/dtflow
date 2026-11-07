# Scripting layer (Scripts epic, first slice)

DTFlow ports the community Draw Things scripting layer instead of inventing its own. This page describes what the first
slice (the engine, the hook table and the ledger; issues #119 and #113) does and does not do.

## Upstream source

- Pinned commit `e2c7fa474ce3bddfe34ad20bdde75f425344800e` of the Draw Things community repository (GPL-3.0).
- `src/dtflow/scripting/resources/shared_script.js` is the `SharedScript.swift` JavaScript string extracted verbatim.
  `tools/scripting/extract_shared_script.py --check` re-extracts it from the pinned Swift file and fails on drift.
- `models.py` ports `ScriptModels.swift` (configuration fields, strict decoding, dimension rounding to multiples of 64).
- Scripts are evaluated like upstream, as `(function(){ <script> })();`, so top-level `return` is valid.

## Engine and process model

- Engine: `quickjs-ng` (Python module `quickjs`). One JSON bridge, `__dtCall(name, jsonArgs)`, serves every `__dtHooks`
  entry. `hooks.py` lists each hook as implemented, planned or unsupported (`answer`, `CLIP` and `repl` are unsupported
  by decision).
- Every run happens in a disposable child process (spawn). QuickJS cannot run host callbacks while a time limit is set,
  so the parent enforces Stop, the JavaScript-segment deadline (default 300 s without a host call; hook time excluded),
  memory and stack limits and size bounds. After Stop the child gets a 1 s grace and is then killed. Killing a child
  never cancels provider work a hook already submitted; that stays owned by its durable attempt.
- The child is bound to the app's lifetime on a best-effort basis: Linux parent-death signal and a Windows kill-on-close
  Job Object (not independently verified on Windows). If assignment fails, or on another platform, an orphaned child can
  outlive the app until it notices the closed pipe and exits. The frozen entry point calls `multiprocessing.freeze_support()`.
- `console.info`/`console.debug`, a cancellable `__dtSleep`, and the read-only `globalThis.__dtflowScriptHost` marker (`{name: 'DTFlow', apiVersion: 1}`) are DTFlow overlays kept outside the vendored file. The marker alone does not establish batch support: portable scripts check `typeof pipeline.runBatch === "function"` for the optional PR #218 extension, and keep their Draw Things sequential path when it is absent.

## Bounded parallel batches (PR #218)

The optional DTFlow-only `pipeline.runBatch(jobs, maxConcurrent)` hook validates bounded independent requests and returns ordered per-job outcomes. It counts jobs against the existing run ceiling, not a separate parallel-generation allowance. Queue child lanes (attempts schema 6, an item column since schema 9): the parent holds one reservation, the first child reuses it and further children compete with ordinary queues for shared backend capacity. Backend/session types stay outside the JS facade. Per-job configuration/evidence and committed outputs use the existing generation service and attempt ledger; child linking is serialized on the run, and each link is written before it takes effect.

Since #220 a failed, unknown or not-saved job no longer closes intake; each job keeps its own status. Reservation, child-link and lane-release failures still close intake, which waiting workers recheck after reservation and child-link awaits. Explicit worker ownership, all-results gathering and cancellation-safe draining prevent parent settlement/backend closure before admitted siblings finish, including lane-release failures. No remote cancellation is introduced; the only automatic retry is the shared one of `core/retry_policy.py` (#241), a new linked child counted in `max_generations`, sent on the job's existing lane. Restart clears stale lane bookkeeping under the existing exclusive owner, preserving durable attempts and no-replay semantics. Older database writers refuse schema 6; migration retains the existing pre-upgrade backup policy.

Scripts read the same confirmed cloud LoRA receipt pool used by queue LoRA sets during Setup and refresh it at admission, without requiring those LoRAs to be selected on Generate. Only script-selected receipts enter child requests; explicit starting-request bindings win, and fresh backend/account/receipt validation remains mandatory. This never downloads missing models, uploads files or treats stored receipts as current server availability. See the [Scripts guide](../guides/scripts.md) for API limits, result semantics and acceptance boundaries, and the [batch plan](../plans/2026-11-03-parallel-script-batch.md) for exact CI evidence.

## Krea2 authoring extensions (PR #219)

The frozen DTFlow host overlay additionally exposes paged `listLoRAs`, `runLimits`, source-version preference read/write and `planId`. `services/script_inventory.py` composes the existing catalogue, immutable cached backend snapshot and confirmed local receipt pool into one bounded projection; metadata compatibility and readiness stay distinct. No new provider, filesystem discovery or arbitrary receipt data crosses this boundary. `RunContext` supplies the immutable source SHA for preferences; the independent schema-1 store is not execution authority.

The adapter uses one ordinary `requestFromUser` form in Setup. Inspect preloads that parked form without a queue or provider session. Run Script submits the answers through the existing queue admission path; there is no preview-specific hook, local answer bridge or intermediate consent form.

The portable adapter constructs deterministic global chunks under the captured run cap after the single Setup form is submitted. Optional validated batch `planId`/`jobIndex` fields become `script_plan`/`script_job` resolution evidence, preserving per-job mapping without adding another dispatcher or execution store. Source/settings identity is not proof of execution and does not authorize replay. The pinned upstream facade, original Draw Things fallback and unsupported-field gate are unchanged. See the [adapter guide](../guides/krea2-mixer.md) and [plan](../plans/2026-11-03-krea2-mixer-adaptation.md); source implementation is not fake/backend/native/packaged acceptance.

## Analyzer (`dtflow script <file>`)

The analyzer never runs the script. The source is **parsed** with the vendored Acorn 8.14.0 inside a memory-capped,
hook-less QuickJS child; the script is passed to `acorn.parse` as a string value, never concatenated into code, compiled
or evaluated. Options: latest ECMAScript, script source type, top-level `return` allowed and a leading `#!` line **rejected**, because
the runtime wrapper `(function(){ <script> })();` rejects it too (upstream behaves the same).
Caveat: Acorn follows the newest ECMAScript, so syntax the execution engine does not support yet can parse cleanly and
still fail at run time. The report lists facade calls found by a conservative static scan; calls reached through dynamic
expressions are not visible and the report says so. The report records the parser and engine versions (`acorn`,
`quickjs-ng`) so a parse/run mismatch can be traced; re-check the gap when either is bumped. The packaged build copies the `quickjs-ng`
distribution metadata so frozen reports do not fall back to `unknown`.

Acorn is vendored unmodified (MIT). Version, source URL, checksums and the licence text are in
`src/dtflow/scripting/resources/acorn.NOTICE.txt`.

## Engine self-check

`dtflow script --check-engine [-o result.json]` (and `--dtflow-script-selfcheck [result.json]` in the packaged
executable) runs eight checks in the real child process: import, console and cancellable sleep, an unsupported hook,
the script wrapper, Stop, the segment deadline, the memory limit and the analyzer (a valid, a broken and a breakout
script through the same code path the frozen exe uses). It reports child start-up time, CPU name and CPU
count, so it doubles as a Windows and older-CPU compatibility probe. A `containment` block records what the parent did
(`contained`), what the JavaScript process itself reported (`child_in_job`, Windows only; evidence, not proof, since an
inherited job also answers true) and whether a launcher stub sits between the two processes (`launcher_stub`).

### Manual containment diagnostic

`dtflow script --hold-child pids.json` (hidden from `--help`; frozen: `--dtflow-script-hold-child pids.json`) starts the real contained child on
a busy loop, writes `parent_pid`, `outer_launcher_pid` (frozen onefile builds only: the bootloader process above the app), `spawned_pid` and
`child_pid` to the file and waits. Kill the parent (`taskkill /F /PID
<parent_pid>`), wait three seconds and check that `tasklist /FI "PID eq <child_pid>"` (and `spawned_pid`) report no
task, and likewise for `outer_launcher_pid`. Ctrl+C stops the run normally. This is a manual check for the owner; it is not run by CI and its result is the
only evidence that Windows containment, including the order in which a onefile launcher joins the Job Object, works.

## Script run state (kept in the queue item, #243)

There is no separate run store. A queued run lives in its script item, as `request_json["run"]` (state, starting
context, ordered child attempt ids, forms, recorded answers, start and end times), like a workflow run. States: running,
completed, failed, stopped, interrupted. `scripting/run_state.py` (`ScriptRun`) holds the run in memory and hands every
change to the dispatch owner (`update_script_run`, one short transaction) *before* it takes effect: a child attempt is
linked before anything can be submitted, a form is published and its answers recorded before the script sees them. A
failed write raises and the run stays as it was. `run_recorded` (in `runner.py`) writes the starting state before the
child starts, settles it from the result (completed, failed or stopped) and settles it as failed if anything raises.

If an owner-side form-answer write fails, the dispatcher requests local script cancellation and waits for the script worker and admitted generation futures before releasing its backend/store; the persistence error still propagates. The existing 60-second write timeout does not guarantee cancellation of the database operation or a known write outcome; a late commit can still require conservative unknown/interrupted recovery. No uncertain work is replayed automatically.

Crash recovery is the queue reconcile, one transaction under the owner lock: a run left running becomes interrupted,
its open form is closed, and the item takes the outcome its run and children decide (`unknown` while a child has no
confirmed outcome). Nothing is resumed or replayed. A script item without a run never started and returns to pending.

Forms are answered from any process by posting a `script_answer` (or `script_cancel_form`) queue control; the script
runner takes it (`take_script_controls`), checks the answers and owned picker inputs again and records them. If the runner refuses an answer (for
example a picked file changed in between), it notes the reason on the still-open form and Scripts offers the form again
with that reason. Picker input capabilities are small files under `<state>/script_inputs/<run-id>/`, outside the
script's own sandbox, bound to the run, form and field. The last answers of each form are remembered in the
`script_form_answers` table of the attempts database. The recorded answers kept in the run replace inline images
(`data:` URLs) by their SHA-256 and size, so the item that is rewritten on every step stays small; the script itself
received the full answers. An open form's fields (which may embed images) are kept only while it is open.

Posting a form answer or cancellation rechecks the exact open form and running item inside the same write transaction that inserts the control. An answer that loses a race with form closure or item settlement is rejected rather than left pending after the dispatcher exits.

Child lanes (`pipeline.runBatch`, shared with workflows) are the item's `lanes_json` column: the attempt ids holding a
lane. Restart clears them under the owner lock. Scripts Setup keeps its run in memory until Run Script; the dispatcher
writes it into the item when it adopts the Setup, before generation can be admitted. `dtflow script --run` also creates
a script queue item; without `--queue` it starts that queue as the dispatch owner and reports from the item.

The script request records an `interactive` input policy. Existing records default to interactive queued behavior; newly created immediate CLI runs explicitly disable interaction, preserving initial/default answers or supplied `--answers` instead of waiting for another process. CLI `--queue` and desktop runs retain interactive forms when no answer file is supplied; desktop Setup requires the interactive policy.

Schema 9 copies each script item's run from the old `<state>/script_runs.sqlite3` (read-only; the file and
`script_run_locks/` are left in place and no longer used), drops the `script_child_slots` table and moves remembered
form answers. A ledger that cannot be read marks the affected runs unreadable instead of failing the upgrade.

## Testing status

Per project convention the first slice adds no tests; CI runs the existing suite. Tests and edits follow after the
smoke test has been validated.

## Scripts Qt queue integration (PR #215)

The native Scripts page imports immutable content-addressed `.js` versions into the managed library, runs a source-only Acorn/hook inspection, and explicitly creates a one-item script queue through the existing Queues controller. The queue item's `run_id` links one ScriptRun to child attempt records before submissions. The UI polls durable state and shows child results and history; success uses the shared activity bar, and pause is a queue state rather than a separate warning overlay. The Queues parent entry is a run-level summary whose thumbnail previews the last successfully committed child, while image opening stays attached to child attempts. Render fixtures are offline and do not establish Windows acceptance.

Current hook scope adds virtual `clearCanvas`, zoom/size/move and empty `clearMoodboard` geometry bookkeeping only; no pixel canvas, masks, source image handoff or non-empty guidance. No real-time native `requestFromUser` form binding is delivered here. Image and broader configuration/LoRA compatibility remain deliberately fail-closed until their owning issues land. The original #199 owner fake-backend smoke does not verify #215's UI or local/cloud implementation.

## Tier A headless generation (PR #199)

The ScriptHost in `scripting/host.py` implements the subset of upstream hooks for read-only configuration/prompt lookup, generation, model/LoRA lookup, headless form answers, and confined file access. One `generateImage` maps to one existing `run_generation` / `GenerationService` attempt. A caller-named attempt identifier is linked to the ScriptRun before the provider submission can begin. A failed or unconfirmed step does not block the rest of the run: with the interactive form bridge the host opens a host-owned form (Retry with the same settings, or Continue without the image), and a retry links a new child whose request carries `script_retry_of` evidence naming the attempt it replaces; the item outcome is decided by the retry, not the replaced try. Each retry counts as one generation against `max_generations`, so the cap stays a single ceiling. Headless runs raise the step's error to the script; Stop only stops local waiting, and the service drains submitted attempts before closing. `--max-generations` defaults to 30 per run (maximum 32); this limits calls per run, not simultaneous generations.

The start configuration is resolved with the normal CLI validator before JS evaluation and never mutates the live Generate form. Only width, height, seed, steps, guidance, model, sampler, and LoRAs (`mode: all`) are script-changeable in this slice. The pinned upstream field model decodes the full configuration, but changing another field is rejected before that generation, rather than silently discarded. An empty model is explicitly rejected. A `mask` is rejected until #116. For headless forms, `--answers` supplies ordered answer lists or usable initial values are applied; native mid-run interaction is outside this PR. File hooks operate within `<state>/script_files/<run-id>/`, not the user's Pictures directory. Canvas saves expose the latest committed result only; full virtual canvas behavior belongs to #116.

On Windows source execution, the owner reported a successful fake-backend run with two linked attempts and a final PNG, plus a rejected empty-model run with zero attempts (2026-11-02). These two manually invoked fixtures are tracked in `tests/fixtures/scripting/`; no automated scripting tests were added or run for this PR. This evidence does not establish local/cloud backend acceptance, native Qt UI, interactive forms, arbitrary Tier A sample compatibility, or Windows packaged-executable acceptance for this new slice.

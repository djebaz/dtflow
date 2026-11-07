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

The optional DTFlow-only `pipeline.runBatch(jobs, maxConcurrent)` hook validates bounded independent requests and returns ordered per-job outcomes. It counts jobs against the existing run ceiling, not a separate parallel-generation allowance. Queue child lanes use attempts schema 6: the parent holds one reservation, the first child reuses it and further children compete with ordinary queues for shared backend capacity. Backend/session types stay outside the JS facade. Per-job configuration/evidence and committed outputs use the existing generation service and attempt ledger; the ScriptRun ordinal allocation is transactional under concurrent linkage.

Since #220 a failed, unknown or not-saved job no longer closes intake; each job keeps its own status. Reservation, child-link and lane-release failures still close intake, which waiting workers recheck after reservation and child-link awaits. Explicit worker ownership, all-results gathering and cancellation-safe draining prevent parent settlement/backend closure before admitted siblings finish, including lane-release failures. No automatic retry or remote cancellation is introduced. Restart clears stale lane bookkeeping under the existing exclusive owner, preserving durable attempts and no-replay semantics. Older database writers refuse schema 6; migration retains the existing pre-upgrade backup policy.

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

## ScriptRun ledger

`persistence/script_runs.py` keeps `<state>/script_runs.sqlite3`, versioned by `PRAGMA user_version` independently of
the application. States: running, completed, failed, stopped, interrupted. A run left running by a crash becomes
interrupted when the store is next opened and is never resumed or replayed. Child attempts are linked in order, before
submission, and never removed. `run_recorded` (in `runner.py`) ties the ledger to a run: it creates and locks the row
before the child starts, settles it from the result (completed, failed or stopped) and settles it as failed if anything
raises; if settlement itself fails, ownership is still released, the original outcome or error is kept and the row is
recovered as interrupted on the next open; hooks receive `link_child(attempt_id)` to record child attempts before submitting them. Liveness is decided by an OS file lock per run in `script_run_locks/` (`flock` on POSIX,
`msvcrt.locking` on Windows) taken before the row exists, so a second app instance does not interrupt a run whose lock is
held and process-id reuse cannot confuse ownership. Only a genuine "lock is held" error counts as held; on a filesystem
without lock support (some network mounts, WSL1 `/mnt/c`) creating a run fails with a clear error and crashed runs on
that store stay `running`, listed in `ScriptRunStore.unverified` and logged, instead of being guessed at.

## Testing status

Per project convention the first slice adds no tests; CI runs the existing suite. Tests and edits follow after the
smoke test has been validated.

## Scripts Qt queue integration (PR #215)

The native Scripts page imports immutable content-addressed `.js` versions into the managed library, runs a source-only Acorn/hook inspection, and explicitly creates a one-item script queue through the existing Queues controller. The queue item's `run_id` links one ScriptRun to child attempt records before submissions. The UI polls durable state and shows child results and history; success uses the shared activity bar, and pause is a queue state rather than a separate warning overlay. The Queues parent entry is a run-level summary whose thumbnail previews the last successfully committed child, while image opening stays attached to child attempts. Render fixtures are offline and do not establish Windows acceptance.

Current hook scope adds virtual `clearCanvas`, zoom/size/move and empty `clearMoodboard` geometry bookkeeping only; no pixel canvas, masks, source image handoff or non-empty guidance. No real-time native `requestFromUser` form binding is delivered here. Image and broader configuration/LoRA compatibility remain deliberately fail-closed until their owning issues land. The original #199 owner fake-backend smoke does not verify #215's UI or local/cloud implementation.

## Tier A headless generation (PR #199)

The ScriptHost in `scripting/host.py` implements the subset of upstream hooks for read-only configuration/prompt lookup, generation, model/LoRA lookup, headless form answers, and confined file access. One `generateImage` maps to one existing `run_generation` / `GenerationService` attempt. A caller-named attempt identifier is linked to the ScriptRun before the provider submission can begin. A failed or unconfirmed step does not block the rest of the run: with the interactive form bridge the host opens a host-owned form (Retry with the same settings, or Continue without the image), and a retry links a new child whose request carries `script_retry_of` evidence naming the attempt it replaces; the item outcome is decided by the retry, not the replaced try. Retries are bounded by the run's generation cap. Headless runs raise the step's error to the script; Stop only stops local waiting, and the service drains submitted attempts before closing. `--max-generations` defaults to 30 per run (maximum 32); this limits calls per run, not simultaneous generations.

The start configuration is resolved with the normal CLI validator before JS evaluation and never mutates the live Generate form. Only width, height, seed, steps, guidance, model, sampler, and LoRAs (`mode: all`) are script-changeable in this slice. The pinned upstream field model decodes the full configuration, but changing another field is rejected before that generation, rather than silently discarded. An empty model is explicitly rejected. A `mask` is rejected until #116. For headless forms, `--answers` supplies ordered answer lists or usable initial values are applied; native mid-run interaction is outside this PR. File hooks operate within `<state>/script_files/<run-id>/`, not the user's Pictures directory. Canvas saves expose the latest committed result only; full virtual canvas behavior belongs to #116.

On Windows source execution, the owner reported a successful fake-backend run with two linked attempts and a final PNG, plus a rejected empty-model run with zero attempts (2026-11-02). These two manually invoked fixtures are tracked in `tests/fixtures/scripting/`; no automated scripting tests were added or run for this PR. This evidence does not establish local/cloud backend acceptance, native Qt UI, interactive forms, arbitrary Tier A sample compatibility, or Windows packaged-executable acceptance for this new slice.

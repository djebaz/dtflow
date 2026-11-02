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
- The child cannot outlive the app: Linux parent-death signal, Windows kill-on-close Job Object (not yet verified on
  Windows). The frozen entry point calls `multiprocessing.freeze_support()`.
- `console.info`/`console.debug` and a cancellable `__dtSleep` are a small DTFlow overlay kept outside the vendored file.

## Analyzer (`dtflow script <file>`)

The analyzer never runs the script. The source is **parsed** with the vendored Acorn 8.14.0 inside a memory-capped,
hook-less QuickJS child; the script is passed to `acorn.parse` as a string value, never concatenated into code, compiled
or evaluated. Options: latest ECMAScript, script source type, top-level `return` and a leading `#!` line allowed.
Caveat: Acorn follows the newest ECMAScript, so syntax the execution engine does not support yet can parse cleanly and
still fail at run time. The report lists facade calls found by a conservative static scan; calls reached through dynamic
expressions are not visible and the report says so.

Acorn is vendored unmodified (MIT). Version, source URL, checksums and the licence text are in
`src/dtflow/scripting/resources/acorn.NOTICE.txt`.

## Engine self-check

`dtflow script --check-engine [-o result.json]` (and `--dtflow-script-selfcheck [result.json]` in the packaged
executable) runs seven checks in the real child process: import, console and cancellable sleep, an unsupported hook,
the script wrapper, Stop, the segment deadline and the memory limit. It reports child start-up time, CPU name and CPU
count, so it doubles as a Windows and older-CPU compatibility probe.

## ScriptRun ledger

`persistence/script_runs.py` keeps `<state>/script_runs.sqlite3`, versioned by `PRAGMA user_version` independently of
the application. States: running, completed, failed, stopped, interrupted. A run left running by a crash becomes
interrupted when the store is next opened and is never resumed or replayed. Child attempts are linked in order, before
submission, and never removed. Liveness is decided by an OS file lock per run in `script_run_locks/` (`flock` on POSIX,
`msvcrt.locking` on Windows) taken before the row exists, so a second app instance never interrupts a live run and
process-id reuse cannot confuse ownership.

## Testing status

Per project convention the first slice adds no tests; CI runs the existing suite. Tests and edits follow after the
smoke test has been validated.

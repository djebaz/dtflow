# PR 105 logging review

Oct 19, 2026 · @Djé

## Summary

Three independent reviews of PR 105 found 12 issues; four should be fixed before merge. None are fixed yet. The only change pushed is the pytest fix (843b36b), and CI is green on it. The full text of each review follows this summary.

### Fix before merge

1. **A rotation or write failure turns the sink off for the rest of the session.** `_diagnostic_writer.py:171-202,222-226`. On Windows the rename fails while any reader holds the file open. After that, no loss is counted and no `session.closed` seal is written, so the file looks like a crash.
2. **A failed prune disables logging for the new session.** `logging_setup.py:85-98,139-143`. Pruning by mtime can also unlink the file of a live but idle session in another process.
3. **The packaged `MessageBoxW` is called from the listener thread.** `ui/desktop.py:40-42,67`. It blocks the writer while open, and it is a notice outside the main window, which AGENTS.md rules out.
4. **Path redaction over-redacts.** `_diagnostic_schema.py:41-42,149,169-179`. It hides `image/png`, `3/4` and model ids, rejects a `"%d/%d"` template, drops traceback filenames, and can leak the word after a space in a path.

### Performance

5. **Producer-side cost under `-x` is about 180 µs per record, against about 40 µs before.** Fix: cache `Path.home()` and the secret values, and skip plain keys and short strings.
6. **Each in-flight capture reserves 1 MiB** (`_diagnostic_writer.py:93-96`). That caps concurrency at about 7 and produces spurious `queue_bytes` losses.
7. **`MAX_RECORD_BYTES` (1 MiB) is larger than the segment `MAX_BYTES` (1,000,000).** Under `-x`, `session.started` also rotates out of the live file. A short identity header per segment would fix it.

### Gallery spans

8. There is no separate decode span, `build_wall_ms` includes filesystem I/O, `query_start` has no trigger field, snapshot recovery retries and late skips are not logged, and `reasons` is a joined string with only 4 of 8 reasons documented.

### Lost detail and smaller issues

9. Lease names are dropped, `images_added` mixes an int with `"unknown"`, and long event names are truncated.
10. `gui_gap` and `scroll_intent` are each counted three times. Some aggregation phases have no producer. An idle aggregate batch never ages out after 10 s.
11. `write_record` can race shutdown because it runs without the handler lock.
12. The startup line at `desktop.md:59` is outdated, and gallery CSV references remain.

### Already fixed (843b36b)

- 8 pytest failures: seven tests still read text logs and now parse JSONL. One was a real regression: `generation.failed` and `generation.inventory_refresh_failed` had lost the failure `code` and `category`, now restored through `failure_fields()` in `ui/generation_page.py`.

## Review 1: spans and correctness (head f41e937)

Scope: the implemented Phase 1 (JSONL writer, capture/privacy, typed producers) measured against the plan in `devdocs/plans/2026-10-19-structured-log-viewer.md` (G1–G5, P1.x) and the gallery timing needs (SQL / build / decode spans, auto-refresh). The repo plan has no "Phase 0"; I read that as the existing `-x` gallery timing contract this PR must carry over. Read-only review: nothing pushed, no tests run. One small Python snippet exercised `capture()`/`sanitize()` directly to confirm findings 2 and 3.

### What is correct

- **Aggregation happens once now.** Listener p95 is true nearest-rank over the raw batch (`_diagnostic_writer.py:305`), the same formula as #104. Source summaries (`gallery.source_summary`) are no longer re-aggregated by the listener (`_diagnostic_writer.py:338-340`). In #104, `thumbnail_end_to_end` summaries were fed back into the listener (the phase was in `_AGGREGATED`), producing a summary of summaries. Source summaries carry no invented p95 (`diagnostics.py:57-90`).
- Admission/loss accounting is sound: capture bytes are reserved before capture, released on every path, and inflight is drained before the seal (`_diagnostic_writer.py:85-135, 395-433`). The seal is withheld on writer failure or deadline.
- Source timestamps and `mono_ns` are taken at the producer, so the 10 s window uses source time (`_diagnostic_schema.py:184,209`).
- The SQL/build split exists (`services/gallery.py:143-162`). Payload dumps in the backend were replaced by counts (`backends/drawthings/backend.py`). I found no prompt, path, credential or image data in any typed `data` projection.
- `-x` maps to DEBUG and every gallery measurement stays behind `dev_enabled()`. `-q/--logs` reaches `configure_logging` in both the CLI (`cli.py:679`) and desktop (`ui/desktop.py:63`) launchers, and the help text matches `cli.md`/`building.md`.

### Findings (most severe first)

1. **One rollover failure silently ends logging for the rest of the session (Windows).** `JsonlHandler.write_record` rolls over inside the try block (`_diagnostic_writer.py:184-185`). Any `OSError` sets `self.failed = True` permanently (`:199-202`). After that, every later record raises "sink disabled" and only one `writer_failures` count is recorded (`:222-226`). On Windows, the rename fails if any reader has the active file open: the planned Phase 3 Follow poller, a benchmark tail, an editor or antivirus. One rollover collision would then silently end diagnostics for the session. The old `RotatingFileHandler` path failed per record and kept going. Fix: retry the rollover, or keep writing to the current segment on rename failure, and count the records dropped after a failure.
2. **Tracebacks lose every filename under `-x`.** `_exception` sanitizes each `File "..."` line (`_diagnostic_schema.py:149`). The new path patterns (`:41-42`) then turn every frame into `File "[redacted-path]"` and drop source lines. #104 kept paths, with only the home directory folded to `~`. Every `exc_info=args.dev` traceback (`cli.py:736,763`) is now useless for debugging. Fix: relativize frames to the package root or `site-packages` before sanitizing, instead of redacting them.
3. **The path pattern over-redacts, and one template form is rejected.** Checked with `capture()`:
   - `"progress %d/%d"` is rejected outright (`unsupported_value`). The template is sanitized before formatting (`:169,179`), so `%d/%d` becomes `[redacted-path]` and the `%` operation fails. All producers in this PR use typed events, so nothing is rejected today, but generic `logging.message` support (P1.1) is broken for any `a/b` format string.
   - Any string with a slash is redacted: `image/png`, `3/4`, and a model id like `sd_xl/base.ckpt` (`:42`). This applies to typed `data` values too (`:96`), so it can erase legitimate evidence.
   - A path with a space outside home can leak a folder-name word. For example, in `D:\Shared\My Pictures` the trailing `  Pictures ` survives, because the patterns stop at whitespace.
   - Fix: format the template with the raw args first, then sanitize the result. Restrict the relative-path pattern to segments that look like files or directories (a drive letter, a `~`, a leading `.`/`..`, or a file extension).
4. **The record limit exceeds the segment size, and `session.started` rotates away.** `MAX_RECORD_BYTES = 1 MiB` (`_diagnostic_schema.py:17`) is larger than `MAX_BYTES = 1,000,000` (`logging_setup.py:41`). A large record gets a segment of its own and pushes three segments of history out. More generally, `session.started` is written once, so any `-x` gallery session over about 4 MB deletes it with `.3` (`logging_setup.py:42`). The Phase 2 reader then cannot verify identity or policy. G4 acknowledges this as "incomplete evidence", but at `-x` volume it will be the normal case. Suggest repeating a compact identity header at the start of each segment, or rotating the listener's own copy of it.
5. **Packaged builds can show a modal dialog from the listener thread.** The desktop `report_error` is a `MessageBoxW` when no console is attached (`ui/desktop.py:40-42,67`). `sink.warn()` now fires on any loss or capture rejection (`_diagnostic_writer.py:293`), not only on write failure, and it runs on the listener thread. That blocks the listener while the box is open, the queue fills, and close times out unsealed. Fix: report to the GUI through a queued signal, or limit the dialog to setup and write failures.
6. **Spans needed for gallery diagnosis are missing or mislabelled.**
   - There is no separate decode span. `thumbnail_workers` times lease wait, resolve, read, cache lookup and decode as one wall time (`ui/gallery_page.py:418-428`). `thumbnail_read_slow` only appears when the lease is held at least 500 ms (`:212`).
   - `build_wall_ms` is not pure Python construction: it includes `ArtifactReadPaths.discover()` and a per-row `is_file()`/`stat()` in `item_from_record` (`services/gallery.py:152-157, 99-101`). On WSL or Windows shares that is I/O. `development.md` calls it "Python gallery-record construction". Split the filesystem time out, or correct the doc.
   - `gallery.query_start` has no trigger field (`ui/gallery_page.py:1618`). Manual Refresh, auto-refresh, Trash/Delete (`image_recycle_panel.py:328`), retry and filter refreshes are indistinguishable, so the log cannot show that manual and recycle refreshes bypass the auto-refresh gate.
7. **Auto-refresh logging gaps.**
   - The snapshot/failing-episode branch refreshes without any `gallery.auto_refresh` event (`ui/gallery_page.py:1443-1447`). Live-recovery retries are invisible except through `query_start`.
   - Late skips in `_auto_signature_done` (`:1451-1463`) and signature-read errors are not logged.
   - `reasons` is a comma-joined string instead of a list (`:1436-1440`). There are 8 reasons (`preparation`, `mount`, `page_jump` and `signature_check` were added), but `development.md` lists 4 and omits `action=unchanged`.
8. **Some diagnostic detail is lost beyond what privacy needs.**
   - Lease events dropped the fixed lease name (`original_images` vs `image_recycle_operation`). Those names are not sensitive, and the earlier "PersistenceError" investigation relied on them (`persistence/original_io.py:88,126,153`).
   - `images_added` / `images_removed` mix int and `"unknown"`.
   - Event names were truncated, for example `gallery.snapshot.publication_worker_unavailable_during`.
9. **The duplicate populations need a reader rule (Phase 2 handoff).** Every gap of at least 100 ms appears three times: in the `gui_gaps` source summary, as an individual `gallery.gui_gap`, and in the listener `gui_gap` aggregate. Every `scroll_intent` is now written individually and aggregated too (`_diagnostic_writer.py:360-361`; #104 only did this for gui\_gap). The listener `gui_gap` aggregate therefore contains only records that are already individual. This matches the plan, but it is easy to double-count. The #104 `thumbnail_median_recorded_ms` read `thumbnail_end_to_end_summary/elapsed_ms` (`tools/ui/windows_gallery_benchmark.py:155`), a metric the old summaries never carried, so it was always null. Phase 2 should map it to `gallery.source_summary` with `phase=thumbnail_end_to_end` rather than preserving it.
10. **Docs.** `desktop.md:59` still describes one `dtflow.startup` line that includes the background library load; that is now two events (`app.startup_timings`, `app.provider_preloaded`). The gallery CSV references in `development.md:140,156` and `tools/ui/README.md` are acknowledged as Phase 2 work.

## Review 2: logging overhead (head f41e937)

Scope: runtime cost of the Phase 1 JSONL pipeline on the GUI thread and hot paths, with `-x` (dev) on and off. No tests were run. The timings below come from a standalone CPython micro-benchmark on Linux that imports only the stdlib-only logging modules. Treat them as relative numbers, not Windows measurements.

### Summary

- **`-x` off is fine.** Every gallery `measurement(...)` call site is behind `dev_enabled()`, and `diagnostics.event()` returns early through `isEnabledFor`. The measured cost is about 0.35 µs per guarded call. INFO events cost about 140 µs each, but they only fire on lifecycle actions such as connection checks, generation submit, export done and snapshot publish. None of them fire on a scroll or paint path.
- **`-x` on regresses GUI-thread cost about 4.5×.** Each emitted record costs about 180 µs on the calling thread. On main the same scroll\_intent debug line cost about 40 µs. The extra cost comes from running the whole capture and sanitize step synchronously on the producer, which is the Qt GUI thread for every gallery measurement.
- There is no blocking file I/O on the GUI thread. Writes, flushes and rotation all happen on the listener thread, and the bounded queue drops records instead of blocking.

### Findings

#### 1. Capture and sanitize run on the producer (GUI) thread. Medium severity, `-x` on only.

`src/dtflow/_diagnostic_writer.py:85-100`: `CaptureHandler.emit` calls `capture(record)` inline. `capture()` (`src/dtflow/_diagnostic_schema.py:157-220`) walks the data, context and aggregation dicts and runs `sanitize()` on **every string, including every dict key** (`_diagnostic_schema.py:116`). It then runs `validate_record` before the record is queued.

- A 4-field `gallery.scroll_intent` makes 9 `sanitize()` calls at about 12.6 µs each. `capture()` takes about 130–155 µs, and a full `measurement()` emit takes about 180 µs.
- Most of each `sanitize()` call is fixed per-call work that never changes during a session: `Path.home()` takes about 5.3 µs and the two secret `os.environ.get` lookups take about 1.2 µs (`_diagnostic_schema.py:57-64`). The 6 regexes together cost under 2 µs on short strings.
- Producers on the GUI thread with `-x` on: `gallery.scroll_intent` fires on every wheel event (`ui/gallery_page.py:3713-3721`), and trackpad or high-resolution wheels send many per second. Others are `render_page`, `grid_layout`, `grid_mount_complete`, `zoom_reflow`, `thumbnail_resize`, `adjacent_*` and `query_*`. There is also `source_summary`, which carries a nested `agg` dict, from the 2 s heartbeat flush (`ui/gallery_page.py:1406-1408`).
- Why it matters: `-x` is the benchmark mode. The instrumentation now adds noticeably more of the GUI-thread time that `gui_gap` and the #104 Windows benchmarks measure, so new sessions are not comparable with older text/CSV evidence.
- Suggested fix, as a prototype rather than a measured change: compute the home path and the secret values once in `configure_logging`. Apply the secret replacement first. Then skip the regexes for short strings matching `[A-Za-z0-9_.-]{0,64}`, and skip `sanitize` for keys that already match the `[a-z][a-z0-9_]*` field-name shape. In the same micro-benchmark this cut `capture()` from about 132 µs to about 30 µs. The alternative is to keep only a cheap shallow copy on the producer and move sanitize and validate to the listener, as main did with its `_Formatter`.

#### 2. A 1 MiB reservation per in-flight capture limits concurrency to 7. Low severity.

`_diagnostic_writer.py:93-96`: every emit reserves `MAX_RECORD_BYTES + 1024` (about 1 MiB) of the 8 MiB `QUEUE_BYTES` budget for as long as the capture runs. The 8th concurrent producer is therefore dropped as `queue_bytes` loss even when the queue is empty. Captures now take about 150 µs under the GIL, so overlap between thumbnail workers, the async runner, generation and the GUI is plausible during bursts. The result is a spurious `diagnostics.loss` that makes `session.closed` report incomplete evidence. Reserving a small estimate and checking the real size after capture would avoid this, and line 123 already does that check.

#### 3. A rotation failure permanently disables the sink. Low severity now, likely to matter in Phase 3.

`_diagnostic_writer.py:184-185,199-201`: `doRollover()` renames the active file. On Windows, if any other process has the active `.jsonl` open without `FILE_SHARE_DELETE`, the rename raises. The planned Logs workspace reader, or klogg or `tail`, would hold it open that way. That sets `failed=True`, and every later record is lost for the rest of the session. With `-x` on, the 1 MB rotation point is reached quickly. Phase 3 needs either share-delete reads or a rollover that retries or skips instead of failing the sink.

#### 4. Per-record `flush()` on the listener. Informational.

`_diagnostic_writer.py:190`: each record does `write` and `flush`, so with `-x` on there is one syscall per record. This runs on the listener thread, so it does not stall the GUI. Under a heavy `-x` burst on Windows with antivirus scanning, though, the writer can fall behind and the 1024-record queue drops records instead of blocking. That is acceptable, but expect `queue_records` losses under burst load, and those make benchmark sessions read as incomplete.

#### 5. Duplicate and dead aggregation work. Informational.

- `_diagnostic_writer.py:22`: `thumbnail_worker`, `thumbnail_end_to_end` and `prepare_slice` have no `measurement()` producers. Those phases are aggregated in-process by `_PerfBatchStats` (`ui/gallery_page.py:2916`, `2417`) and emitted as `gallery.source_summary`, so these listener branches are dead. The set was carried over from main.
- `gui_gap` is aggregated twice: in-process for gaps of 50 ms or more (`gallery_page.py:1396`), and again in the listener over only the raw gaps of 100 ms or more (`gallery_page.py:1397-1406`, `_diagnostic_writer.py:360-391`). The listener's `gui_gap` aggregate covers an outlier-only population and is written as well as each raw record. The cost is negligible, but the two aggregates describe different populations under the same phase name, which Phase 2 readers need to keep apart.
- `_PerfBatchStats.observe` (`gallery_page.py:380-381`) now formats `datetime.now().isoformat()` on every observation, which takes about 2 µs on thumbnail workers. This is negligible.

#### 6. The listener loop takes locks on every iteration. Informational.

`_diagnostic_writer.py:395-420`: `_flush_losses()` runs after every record and every 50 ms idle tick. It takes `admission.lock` twice and copies two small dicts. Producers contend for the same lock 3 times per emit. Each hold is a few microseconds, so this is not a stall risk at current volumes.

### Not issues

- Shutdown: `LogSession.close` (`ui/desktop.py:96`) joins the listener for up to 2 s after `run_gui` returns, so the window has already gone. Process exit can be delayed by up to 2 s, but the UI does not freeze.
- Backend and generation producers (`backends/drawthings/backend.py`, `services/generation.py`) emit small projected dicts at DEBUG or INFO, once per attempt or stream stage. They are not hot.

## Review 3: correctness and rotation (head f41e937)

This was a static review only. No tests or validation ran, nothing was pushed, and no generation or billable action ran. Line numbers refer to the PR head. The scope covers the JSONL format and schema, rotation and size limits, thread and process safety, write-failure handling, and startup/shutdown flushing.

### Should fix

1. **A failed prune turns logging off for the new session.** (`logging_setup.py:85-98`, `:139-143`)
   - `_prune` runs inside the `try` that turns any `OSError` into `LoggingSetupError`. Two cases raise: `path.lstat()` gets `FileNotFoundError` when another dtflow process rotates its file (`.jsonl` to `.jsonl.1`) or prunes between `iterdir()` and `lstat()`; and on Windows, `unlink` gets `PermissionError` when another handle holds a stale segment open, such as antivirus or a future Phase 2 reader.
   - The run then has no log file. It also leaves the empty `dtflow-<id>.jsonl` that line 139 already created.
   - A related problem: pruning sorts by mtime, so a GUI that has been idle for a while can be the 20th-oldest "session" while CLI runs pile up. On POSIX its live file is then unlinked while still open, and the rest of that session's evidence is lost.
   - Suggestion: prune per file, best effort (catch `OSError` per path, never fail setup), and skip groups whose base `.jsonl` is still open or was written recently.
2. **In the packaged app, the first logging problem opens a native modal box on the listener thread.** (`ui/desktop.py:67`, `:41`; `_diagnostic_writer.py:153-168`)
   - `report_error` goes to `_notice`, which calls `MessageBoxW` when there is no console. The caller is `JsonlHandler.warn()`, which runs on the listener thread, from `_flush_losses` (`:293`), `_write` (`:237`) and `write_record` (`:201`).
   - The listener is blocked until the user clicks OK. The queue fills (1,024 records), so more records are dropped as `queue_records`. If the box is still open at exit, `stop()` waits 2 s and gives up (`:452-456`), so the session is never sealed.
   - Any single dropped record triggers it in the middle of a session.
   - It is also a DTFlow-owned notice outside the main window, which AGENTS.md rules out.
   - Suggestion: have `warn()` only set a flag or post a thread-safe signal, and let the GUI show the shared inline amber row. Or report it once at close.
3. **One write error disables the file for the rest of the session.** (`_diagnostic_writer.py:171-172`, `:184-185`, `:199-202`, `:222-226`)
   - `doRollover` renames the live file. On Windows the rename fails with `PermissionError` when any other handle is open without share-delete. Python's own `open()` is such a handle, so a Phase 2 reader or Phase 3 viewer following the live session would trigger it, as would antivirus.
   - After that, `failed=True` and every later record is silently discarded: `writer_failures` counts only the first failure, no loss is counted for the records that follow, and no seal is written.
   - Suggestion: when a rollover fails, keep appending to the current file past the cap, write a `diagnostics.loss` marker, and retry rotation later. Disable the sink only for real write failures. Phase 2/3 readers should read and close quickly, or open with share-delete on Windows.

### Worth checking

4. **The maximum record size is larger than a whole segment.** `MAX_RECORD_BYTES = 1_048_576` (`_diagnostic_schema.py:17`) is larger than `MAX_BYTES = 1_000_000` (`logging_setup.py:41`). A maximum-size record forces a rollover and makes a segment larger than its cap. With 3 backups, a few large tracebacks push most of the session out of retention. Plan item G3 asks for one size policy. Suggestion: cap records at about 256 KiB, or at most `MAX_BYTES / 4`.
5. **Each record being prepared reserves a full 1 MiB of the queue's byte budget.** (`_diagnostic_writer.py:93-96`) The budget is 8 MiB, so at most 7 records can be prepared at the same time, and fewer while records are already waiting in the queue. One more record is then dropped as `queue_bytes` even though the queue is nearly empty. Readers must reject a session with any loss, so one such drop invalidates a benchmark session. This is unlikely today because most gallery measurements are batched on the GUI thread. A smaller reservation, or a check only after the record is prepared, would remove it cheaply.
6. **Aggregate batches never age out while the app is idle.** (`_diagnostic_writer.py:364-393`, idle branch `:406-412`) A batch is flushed only on a new sample for the same key, 64 samples, or eviction once there are 64 groups. A few samples followed by quiet stay in memory until shutdown. The "10 s window" in `AGGREGATION_POLICY` is not honored, a follow-mode viewer will not see them, and a crash, a kill or the 2 s shutdown timeout loses them, although `aggregate_source_samples` has already counted them. Suggestion: flush batches older than 10 s from the idle branch.
7. **Race at exit when shutdown times out.** If the `stop()` join times out (`:452-456`), the listener keeps running. At interpreter exit, `logging.shutdown()` closes `JsonlHandler`, because it is still in logging's handler list. `write_record` (`:170-198`) writes without the handler lock, so the close can hit a write in progress. The resulting `ValueError` or `AttributeError` is recorded as a `serialization_error` with a `diagnostics.record_rejected` marker that blames an innocent record. This is low impact. Taking `self.lock` around `write_record` would fix it.

### Nits

8. **The path pattern redacts any value containing `a/b`.** (`_diagnostic_schema.py:42`) For example, `image/png`, `3/4` and `Trash/Delete` all become `[redacted-path]`. Producers already choose safe fields, so this over-redacts typed data in the viewer.
9. **`sanitize` repeats lookups for every string.** (`_diagnostic_schema.py:55-68`) It calls `Path.home()` and reads the secret environment variables for every string, including dict keys (`:111`), on the producer thread, which is often the GUI thread. These could be cached when logging is configured.
10. **Old log files are no longer pruned.** Text `dtflow-*.log` and `-gallery.csv*` files are no longer matched (`_SEGMENT`, `logging_setup.py:44`). Before, they were bounded at 20 sessions; now they stay forever. The PR says this is intended, so it should be written in the guide.

### Checked, no problems

- **Format:** JSONL lines are compact, UTF-8 and LF-terminated, with `allow_nan=False`. Every record is validated before it is written, `seq` is monotonic, and the seal is withdrawn if it lands after the deadline.
- **Session files:** they are created exclusively (`xb`) under unique IDs, so processes never collide on rotation. Retention is about 20 sessions × 4 segments × 1 MB.
- **Producers:** all are migrated. No raw `log.debug/info/...` calls remain in `src`. No data key added by the PR matches the private-key pattern. `StrEnum` values pass capture.
- **Admission and lifecycle:** admission stops before `stop()`, `close()` is idempotent, the listener thread is a daemon, and `session.started` is written before any queued records.

# Native Windows gallery performance autotuning

Date: 2026-10-18  
Status: experimental test tooling on PR #104 (native Windows acceptance pending)

## Objective and scope

Automate the real DTFlow PySide6 Images UI with **pywinauto**, using the existing source launch diagnostics `-x`, per-session `gallery_perf` CSV and rotated text logs. The tuning targets are the **eight groups in the owner's screenshot**, *not* pagination `-ml` or `-mc`. Hold `-ml 20 -mc 100 -format jpg` fixed across the matrix. Run the same images, DPI, viewport, input sequence and cache condition for each comparison.

| Group | Original production defaults | Benchmark profile keys |
| --- | --- | --- |
| Thumbnail decoding | 6 workers | `thumbnail_workers` |
| Prefetch/lookahead | 3 viewport screens | `lookahead_screens` |
| GUI preparation and mount | 4 ms each | `preparation_budget_ms`, `mount_budget_ms` |
| Thumbnail resolution | 600 px max side | `thumbnail_side_px` |
| Pending cache encodes | 256 queued writes | `pending_cache_writes` |
| Disk-cache eviction | 20,000 cap / 16,000 keep / 500 writes | `cache_entries`, `cache_prune_to`, `cache_prune_every` |
| Background refresh | 5 s poll / 30 s full read | `refresh_ms`, `full_refresh_s` |
| Input debounces | 300 ms filter / 250 ms save / 40 ms page jump | `filter_debounce_ms`, `save_debounce_ms`, `page_jump_debounce_ms` |

Safety/lease deadlines, SQLite query bounds, original-file memory caps and provider behavior **are not tuning variables**.

## Design

`tools/ui/gallery_perf_launch.py` validates a complete per-run profile and changes only process-local constants before constructing the real GUI. No production defaults, user-facing CLI options, configuration schemas or installed runtime dependencies change. Explicit bounds and the cache-prune invariant reject unsafe profiles. The Python module drives `dtflow.cli.main` with fixed `-ml 20 -mc 100 -format jpg`, `-x` and a fresh log directory.

`tools/ui/windows_gallery_benchmark.py` launches this child on an interactive Windows desktop; pywinauto attaches to that exact new process PID, selects Images, exercises Grid/List/Detail, page chips, scrolling and prompt-filter changes. The test does not click Generate, queues, Export, Delete or cache-clearing controls. It exits after finishing, with a timeout; failure retains per-trial evidence and has a nonzero final exit code.

Run the following one-line command from a Windows source checkout with a prepared uv environment:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp/gallery-bench-01 --profiles baseline workers-3 lookahead-1 preparation-2ms mount-2ms thumbnails-480px pending-writes-64 cache-prune-earlier refresh-10s debounce-faster --runs 2 --cycles 2 --ack-user-state
```

For a quick **smoke of the harness**, not a tuned-performance conclusion:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp/gallery-bench-smoke-01 --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

For a custom experiment, supply `--profiles-file path/to/profiles.json`, where JSON is a mapping of profile names to per-parameter overrides. Defaults remain fixed for all omitted keys. This permits controlled sweeps instead of editing source constants between runs. `--no-cache` is available to isolate decoding but makes cache-write/eviction profiles irrelevant.

`--ack-user-state` is deliberate: DTFlow reads the existing gallery and can update **viewer preferences and disposable thumbnail cache**, including benchmark-profile eviction. Use disposable image data/state or back up your state and avoid concurrent DTFlow writers. **The harness does not isolate the app's existing catalogue**. Do not confuse this with a destructive dataset test. Windows must be interactive; clear `QT_QPA_PLATFORM=offscreen` and record scaling, CPU/storage and any other competing workload. The harness overwrites that environment setting only in its own child.

## Output and interpretation

Each trial retains `profile.json`, `app-stderr.txt`, sanitized source DTFlow log and `-gallery.csv`, and `trial.json`. `results.csv`/`results.json` retain per-trial outcomes; `summary.csv` groups profiles and reports successful-trial counts and the **largest observed gap above the 50 ms heartbeat threshold**. Empty gap data does *not* establish zero latency or Windows smoothness. The existing listener aggregates `gui_gaps`; a >250 ms outlier can also appear individually. Query duration, layout and thumbnail timing are separate diagnostic observations and do not prove causal attribution. `dispatch_ms` measures automation input dispatch only, **not** end-to-end click-to-paint latency.

Interpretation limits:

- Thumbnail worker count, lookahead, slice budgets and pixel size can affect 100–200-image navigation. Change one family at a time, repeat trials and review the maximum UI gap alongside total loading time and visual smoothness.
- A 20,000-entry cache limit or a 256-write backlog normally **cannot be exercised by 100–200 distinct cached images**. These profiles show how to inject values; they do not establish an optimal cap or queue size without a dedicated larger disposable-cache stress fixture. Do not induce pruning of a user's real large cache solely to collect measurements.
- Five-second polling versus a 30-second full refresh requires a sufficiently long run; a brief smoke is not refresh-performance evidence.
- The filter test deliberately enters a distinctive nonmatching positive-prompt query and clears it. Stored filter/sort/view settings may change; the saved image content is not edited.
- No native Windows benchmark results are asserted merely because the script or Linux CI passes. Treat GUIA/UIA accessibility compatibility, DPI, visual correctness and touchpad scroll as owner native-smoke gates.

## Validation gates

- Python syntax, import, CLI `--help`, profile and log-report unit tests on Linux.
- Fixed-default and custom-profile tests, failure evidence and help/invalid-input cases.
- Actual native Windows source smoke with at least 100–200 real saved outputs, followed by repeated config trials. Record exact commit/build identity and environment.
- No model download, provider call, inference/training, original-file mutation or potentially billable generation is necessary.

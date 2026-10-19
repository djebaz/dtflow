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

`tools/ui/windows_gallery_benchmark.py` launches this child on an interactive Windows desktop; pywinauto attaches to that exact new process PID, selects Images and exercises Grid/List/Detail, verified page-chip changes, up/down scrolling, fullscreen enter/Escape exit, and thumbnail slider movement. Prompt-search text entry was intentionally removed because it is not needed to benchmark ordinary browsing. The test does not click Generate, queues, Export, Delete or cache-clearing controls. Each nonessential missed click, wheel verification or slider movement adds a `warnings` entry and processing continues. Such a trial is marked `warning` rather than `ok`, and is excluded from the clean performance comparison; initial GUI readiness and missing gallery data still fail. Each action is bounded; failure retains per-trial evidence and results in a nonzero final exit code.

Run the following one-line command from a Windows source checkout with a prepared uv environment:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --profiles baseline workers-3 lookahead-1 preparation-2ms mount-2ms thumbnails-480px pending-writes-64 cache-prune-earlier refresh-10s debounce-faster --runs 2 --cycles 2 --ack-user-state
```

For a quick **smoke of the harness**, not a tuned-performance conclusion:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

For a custom experiment, supply `--profiles-file path/to/profiles.json`, where JSON is a mapping of profile names to per-parameter overrides. Defaults remain fixed for all omitted keys. This permits controlled sweeps instead of editing source constants between runs. `--no-cache` is available to isolate decoding but makes cache-write/eviction profiles irrelevant.

`--ack-user-state` is deliberate: DTFlow reads the existing gallery and can update **viewer preferences and disposable thumbnail cache**, including benchmark-profile eviction. Use disposable image data/state or back up your state and avoid concurrent DTFlow writers. **The harness does not isolate the app's existing catalogue**. Do not confuse this with a destructive dataset test. Windows must be interactive; clear `QT_QPA_PLATFORM=offscreen` and record scaling, CPU/storage and any other competing workload. The harness overwrites that environment setting only in its own child.

## Output and interpretation

Each trial retains `profile.json`, `app-stderr.txt`, sanitized source DTFlow log and `-gallery.csv`, and `trial.json`. `results.csv`/`results.json` retain per-trial outcomes; `summary.csv` groups profiles and reports successful-trial counts and the **largest observed gap above the 50 ms heartbeat threshold**. Empty gap data does *not* establish zero latency or Windows smoothness. The existing listener aggregates `gui_gaps`; a >250 ms outlier can also appear individually. Query duration, layout and thumbnail timing are separate diagnostic observations and do not prove causal attribution. `dispatch_ms` measures automation input dispatch only, **not** end-to-end click-to-paint latency.

Interpretation limits:

- Thumbnail worker count, lookahead, slice budgets and pixel size can affect 100–200-image navigation. Change one family at a time, repeat trials and review the maximum UI gap alongside total loading time and visual smoothness.
- A 20,000-entry cache limit or a 256-write backlog normally **cannot be exercised by 100–200 distinct cached images**. These profiles show how to inject values; they do not establish an optimal cap or queue size without a dedicated larger disposable-cache stress fixture. Do not induce pruning of a user's real large cache solely to collect measurements.
- Five-second polling versus a 30-second full refresh requires a sufficiently long run; a brief smoke is not refresh-performance evidence.
- Text search is out of scope. The filter-debounce override is configurable but **not exercised** by this suite; never claim that this smoke optimized filter typing latency. Saved sort/view/thumbnail-size preferences may change; slider movement attempts to restore its original value. No saved-image content is edited.
- No native Windows benchmark results are asserted merely because the script or Linux CI passes. Treat GUIA/UIA accessibility compatibility, DPI, visual correctness and touchpad scroll as owner native-smoke gates.

## First Windows 10 smoke and diagnostic follow-up

Owner-run source smoke (2026-10-19, no successful perf result):

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp/gallery-bench-smoke-01 --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

Observed: dependency overlay installed, pywinauto emitted a third-party `SyntaxWarning` about an invalid escape sequence, then `baseline trial=1 status=failed`. Owner additionally observed that the application **did not navigate to Images**. The exact exception, its UIA selector target and whether the child window opened were not included in the pasted console output, so the failing condition is **not established**. The warning is not evidence of the cause.

Follow-up commits make navigation accept Qt's Windows UIA accessibleName (`Show saved Images`), the visible button name (`Images`), or its `imagesNavigationButton` automation ID; reject ambiguous candidates rather than clicking an arbitrary target; assert the Grid control appears after navigation; and preserve the currently attempted action as `step`, the sanitized `error_message`, and exception class in `trial.json` while displaying failure details on stderr. A successful Windows rerun is still required before claiming that UIA works.

When a trial fails, review `tmp/gallery-bench-smoke-XX/baseline-r01/trial.json` (`step`, `error_type`, `error_message`), then `baseline-r01/app-stderr.txt` and the per-session `logs/`. Avoid sharing personal paths or unrelated application data without review. Use a **new** `--out` directory on every rerun.

Corrected quick smoke command, run from a Windows checkout with the latest PR #104 code:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp/gallery-bench-smoke-02 --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

## Windows virtual-environment PID follow-up (2026-10-19)

Owner smoke `tmp/gallery-bench-smoke-03` failed at `find-dtflow-window` with no actions recorded, although the owner saw DTFlow open and its session log recorded successful gallery queries. A process-only probe in the local uv environment reproduced a Windows virtual-environment `python.exe` redirector spawning a separate interpreter: `Popen.pid` need not be the PID owning the Qt window. This is evidence for the PID-selection defect, not a successful native benchmark.

The benchmark child now atomically publishes its own interpreter PID to the fresh trial's `app-process.json` before GUI setup. The driver waits for that report, fails clearly on early launcher exit or missing/invalid PID, and restricts UIA window lookup to the reported PID with the exact `DTFlow` title. `trial.json` records `launcher_pid` and `app_pid`; window-readiness timeouts include both. It never falls back to another running DTFlow instance. Failure cleanup on Windows uses `taskkill /PID <live-launcher-pid> /T /F` for that trial's process tree rather than terminating only the redirector. A cleanup failure is recorded and stops the matrix before another trial starts.

Regression coverage was added for direct/redirected PID selection, PID publication and failure boundaries, and targeted process-tree cleanup. Per owner instruction, no local tests or benchmark reruns were executed for this fix; CI and an owner interactive Windows rerun remain required. Use a new directory such as `tmp/gallery-bench-smoke-04` with the same smoke command above. No production UI, logging format, tuning default or dependency changes are included.

## Checkable Qt control roles (smoke-04 follow-up, 2026-10-19)

Both owner trials in `tmp/gallery-bench-smoke-04` now found/focused the child window but failed at `find-images-navigation`, recording distinct launcher/interpreter PIDs and no actions. The owner saw DTFlow remain on Connection. Canonical `workspace_shell.ui` defines Images as a checkable, auto-exclusive QPushButton; `gallery_detail_page.ui` defines Grid/List/Detail as checkable QPushButtons. Qt's [button accessibility implementation](https://github.com/qt/qtbase/blob/6.12/src/widgets/accessible/simplewidgets.cpp) returns RadioButton for the former and CheckBox for the latter, and its [Windows UIA mapping](https://github.com/qt/qtbase/blob/6.12/src/plugins/platforms/windows/uiautomation/qwindowsuiautils.cpp) preserves those control types. The driver's Button-only search excluded these controls even with correct names and PID.

The shared selector now accepts Button, CheckBox and RadioButton roles inside the already selected trial window, still requiring an exact expected name or automation ID, visible/enabled state and a unique match across roles. It ignores similarly named text and inactive controls; no coordinates, fuzzy names, production UI changes or arbitrary-window fallback are introduced. Existing selector contracts cover all three roles plus the gallery toggles and cross-role ambiguity. No local tests or native rerun were executed, per owner instruction; lint/help/diff checks are separate from acceptance. Retry with a new `tmp/gallery-bench-smoke-05` output directory; CI and owner native smoke remain required.

## Persisted sort/page setup (smoke-05 follow-up, 2026-10-19)

Owner smoke-05 reached Images and read 20 records, then failed at `verify-page-two`. Its initial query started at offset 140 (batch 8 at the fixed 20-record size). The pager deliberately includes only the first/last chips and current neighbors, so page 2 is not guaranteed to exist when a later page is restored. At the owner's request, setup now selects Grid and changes the real Sort dropdown, ending on Newest first: select Oldest first unless it is already selected, then select Newest first. At least one real change is required; reselecting a persisted value alone does not reset paging. Each selection waits for a fresh successful query completion, then setup verifies page 1 is checked before requiring page 2. Setup dispatches are recorded separately under `setup_actions`; they are not click-to-paint measurements. Existing filters remain unchanged, so a catalogue/filter with fewer than two batches still cannot run the paging scenario.

The query-readiness reader distinguishes newly appended completions from prior records across rotation using the opened file identity and match position/content. A startup log without any completion now returns zero rather than raising from an empty `max` argument expansion. Regression contracts cover restored sort choices and fresh-query readiness across rotation; no local tests or benchmark reruns were run. Retry with a fresh `tmp/gallery-bench-smoke-06`; CI and owner Windows acceptance remain pending. This changes only the development harness, not production persistence or gallery paging behavior.

## Qt Sort popup access (smoke-07 follow-up, 2026-10-19)

Owner smoke-07 located the Sort combo but pywinauto `ComboBox.select("Oldest first")` raised `IndexError` at `setup-sort-Oldest first`, before a sort action was recorded. Replace UIA item lookup with keyboard input directed through the exact, visible/enabled Sort combo: F4 opens Qt's popup, Home selects the first option, Down chooses the second for Oldest first, and Enter commits. Newest first uses F4/Home/Enter. These are the first two canonical `SORT_LABELS`; selected text must match the requested label before proceeding, otherwise setup fails explicitly. Fresh-query and checked-page-1 verification remain required. There is no desktop-wide item search, guessed click position or production UI change.

The existing restored-sort contract now checks the keyboard sequences rather than mocking the unsupported item-selection path. No local tests or benchmark reruns were executed, per owner instruction. Static/help checks are not Windows acceptance. Retry with a fresh `tmp/gallery-bench-smoke-08` directory. Qt's [QComboBox keyboard implementation](https://github.com/qt/qtbase/blob/6.12/src/widgets/widgets/qcombobox.cpp) confirms F4 popup opening and popup Enter commit; the owner's native rerun must establish actual UIA/keyboard behavior.

Keyboard input requires confirmed focus on the identified Sort combo; if focus cannot be verified, setup fails without sending keys. It does not refocus the main window while opening/selecting within the popup.

## Recorder-backed Sort interaction and timestamped runs (2026-10-19)

Owner smoke-08 failed the combo keyboard-focus check in both profiles. The owner's first pywinauto-recorder script then captured a real Images navigation click, a Sort combo click, and `ComboBox -> List -> Newest first ListItem` selection, followed by a page-2 chip click. This is evidence for the actual interaction hierarchy, not acceptance of the automated benchmark. The harness now opens the identified Sort combo with `click_input`, waits for the exact requested ListItem inside that combo, and clicks it. Selected-text, fresh-query and checked-page-1 verification remain in place. This supersedes the prior keyboard-focus approach; no keyboard input, screen-coordinate guesses or desktop-wide item searches are used for Sort. The recording stays local; unrelated Brave/tray/recorder-stop actions, anonymous group clicks and the recorded wheel magnitudes are not copied into the benchmark. No recorder runtime dependency is added.

At the owner's request, `--out` now names an output root and defaults to `tmp`. Each invocation reserves and prints `<root>/gallery_bench_smoke/MMddHHmm/` in local time, for example `tmp/gallery_bench_smoke/10120040/` on October 12 at 00:40. All reports and profile/trial directories live under that run. An existing root is accepted; a timestamp collision fails without merging or overwriting evidence. Wait until the next minute or use another root. The earlier numbered smoke directories/commands in the evidence sections are historical and are not the current output convention.

Selector regression coverage now models physically opening the popup and waiting for its item, including a missing-item failure. Directory coverage checks exact timestamp formatting, existing-root support and collision preservation. No local tests, recording replay or benchmark runs were executed. Static/help checks are not native acceptance; CI and the owner's Windows rerun remain required. Current smoke command:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

## Validation gates

- Python syntax, import, CLI `--help`, profile and log-report unit tests on Linux.
- Fixed-default and custom-profile tests, failure evidence and help/invalid-input cases.
- Actual native Windows source smoke with at least 100–200 real saved outputs, followed by repeated config trials. Record exact commit/build identity and environment.
- No model download, provider call, inference/training, original-file mutation or potentially billable generation is necessary.

## Recorder-backed browsing scope update (2026-10-19)

Owner results from baseline and workers-3 both showed the full Grid/List/Detail and page/scroll sequence completing through `cycle-1-filter`, then timing out because Qt's prompt field was not exposed as the expected UIA `Edit`. This is a test-harness lookup failure, **not** a gallery benchmark or proof of a QT hang. Subsequent recordings provide `Full screen image view||CheckBox` and `ThumbnailSizeSlider||Slider`. The driver now drops text search and covers page 2 → page 1 (checking the selected chip), Grid/List scroll down/up (checking the existing dev `scroll_intent` wheel log), fullscreen enter/Escape exit, and native slider drag/restore. All individual UI interaction failures become warnings and the sequence continues; those trials are not ranked as successes. The screenshot's filter debounce remains a profile-only, unmeasured parameter. Windows replay of the new sequence is pending.

## Larger browsing sweep and Grid fullscreen (2026-10-19)

After a successful owner Windows baseline smoke (the separate workers-3 run was voluntarily closed), the owner requested a stronger interaction sequence. The test now issues **192 Windows wheel notches down and up** in each of Grid and List (16 bounded bursts per direction, configurable with `--scroll-notches` in 12..240). Wheel dispatch and whether an optional `scroll_intent` event appeared are logged separately; wheel distance is not treated as automatically verified if the log event is absent.

In Grid, the benchmark drags the accessible `ThumbnailSizeSlider` toward **55% and 210%**, allowing layout to settle between changes, then attempts to restore the saved starting position; significant failure to restore is recorded as a warning. The **Full screen image view** toggle is now entered and exited while **Grid** is selected, before switching to List. Detail remains an ordinary view-switch test but no longer owns fullscreen. No production defaults, paging limits or generated image content change. This new sequence requires native Windows replay before any new performance conclusions are made.

## Dedicated Grid limit matrix (2026-10-19)

The product's original 20/100 limit is still fixed across the **original eight performance-parameter tuning profiles**. A separate opt-in `--grid-matrix` mode now varies the **real CLI `-ml` batch size and `-mc` mounted-card cap** in independent native Windows runs, without changing production defaults or combining with those tuning profiles.

Default cases: `20:50`, `20:100`, `50:150`, `100:300`, `200:600`, `300:1000`. The validated product resolver rounds mounted cards down to a whole batch: effective caps are respectively 40, 100, 150, 300, 600, 900. Each run preserves requested and effective values in `trial.json`/`results.csv`, under a unique named case folder. A custom matrix accepts at most 16 nonduplicate cases with `-ml` 1..300, `-mc` 1..1000 and `mc >= ml`; no matrix/tuning-profile combinations or more than 40 total trials per invocation.

**Grid-only actions:** open Images, choose Grid, **physically click page 1**, then perform a bounded number of down-wheel steps followed by the same number upward. Steps adapt to `ceil(effective_mc / ml) + 2` (bounded 4..12), using `max(12, --scroll-notches / 3)` integer wheel notches per step. When finished, **physically click page 1 again**, close only the launched trial process, and collect the same diagnostic CSV and -x logs. **Do not touch the Sort dropdown.** This mode never touches text search, List, Detail, fullscreen or the thumbnail-size slider.

The first-query record count is not necessarily the total catalogue size. It only indicates how much of the requested initial batch loaded; a partial first batch (`returned < ml`) is recorded as a warning and excluded from clean comparisons. A larger `-mc` cannot be fully exercised unless the existing catalogue contains enough outputs to reach that cap. The number of wheel messages is recorded, but **actual image displacement remains unverified** unless independent Qt evidence proves it; missing raw scroll-intent lines are not treated as a Windows scroll failure. GUI gap maxima include startup unless phase-scoped separately. Do not call a profile fast or stable from startup maxima, dispatched wheel count alone or one run. User preferences can still be updated by normal gallery navigation; this is not a sandboxed copy of the user's library.

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix 20:50 20:100 50:150 100:300 200:600 300:1000 --runs 1 --cycles 1 --ack-user-state
```

This is opt-in offline interaction testing; it does not invoke generation/training/provider APIs. Native acceptance remains pending.

## Position-driven Grid stress benchmark (2026-10-19, supersedes fixed-step matrix)

Owner requested a much shorter, **faster continuous scroll between the same saved-image indices for every `-ml/-mc` pair**, after finding the previous resident-batch-dependent wheel-step matrix too slow. The former `steps=ceil(mc/ml)+2` algorithm is removed from `--grid-matrix`; only its requested/effective case parameters and isolated launch/reporting model remain.

With the default `--grid-target-card 300`, every case follows exactly the same target itinerary: real Grid → click page 1 → scroll down until **saved image #300 is within the observed visible range** → reverse until **first visible image is #1** → click page 1 again. Position comes primarily from the owner's recorded UIA `Visible image range||Text` control inside `Gallery batch navigation`. The UIA `Text` element may expose only its accessible name instead of the changing QLabel text; the fallback is a low-overhead benchmark-child hook that logs `GalleryPager.present`'s existing `visible_start/visible_end/total/loaded` values, and does not alter UI layout or production defaults.

Continuous wheel input uses short pulses (default at most 12 notches per pulse), adaptive reduction near image #300, and 15 ms between pulses, with **no batch-load barrier and no 250-ms pause after each step**. Each direction is bounded by `--grid-seek-timeout` (default 12 seconds), and by 150 wheel pulses. If an exact position cannot be read, the app contains fewer than #300 matching images, or the requested end range is not reached, the test writes a recoverable warning and does not treat the journey as verified. Logs retain both the position trace and number of wheel events; the runner does not interpret wheel dispatch as proof of card displacement. Final page-1 click occurs **after** trying to verify that the return scroll reached #1 and is never used to fake a completed return.

Matrix cases remain `20:50 20:100 50:150 100:300 200:600 300:1000` with effective card caps `40 100 150 300 600 900`. Sort stays untouched and the normal multi-view tuning mode is unchanged. Use identical catalogue, sort preference, scale, viewport, cache state and target image for comparisons. This new sequence has not been Windows-smoke accepted.

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix --grid-target-card 300 --grid-seek-timeout 12 --runs 1 --cycles 1 --ack-user-state
```

### Owner Windows evidence: 10190234.zip and fast-pulse follow-up (2026-10-19)

Inspected owner-native archive `10190234.zip` (two actual cases, not the whole default matrix): `10:50` (`-ml 10 -mc 50`) reached first visible image 241 downward after 63 wheel pulses / 12.04 s, then first visible 16 upward after 60 pulses / 12.17 s; both directions correctly warned. `50:150` reached visible range 286–300 after 58 pulses / 11.32 s and returned to first visible 1 after 66 pulses / 11.74 s; it completed without warnings. Both trials had 833 matching outputs. Two >1 s individual GUI-gap records occurred during startup with mounted=0, not during continuous scrolling. Hence success status does not establish acceptable sweep speed.

The follow-up reduces explicit inter-pulse sleep from 55 ms to 15 ms and allows 12-notch coarse pulses (instead of 6) when far from the target, tapering to six/two notches near it. The down-leg acceptance now requires card #300 to fall **inside** the actual visible range, not merely a ±15–25 index band. Each seek records `wheel_input_ms`, `position_probe_ms`, `duration_ms`, pulse count, and real before/after visible indices. Both limits, the catalogue, and the return-to-card-1 check remain unchanged. Performance impact and Windows behavior of the follow-up **remain unverified**; the previous zip is evidence only for the superseded 55-ms driver.

## Query-independent automated user / direct result (2026-10-19)

The Windows owner's observation was confirmed in source: earlier Grid matrix automation called `_await_query` **before** clicking page 1 and starting scroll input; additionally the initial `_click_grid_page_one` could wait several seconds for the enabled pager chip. These waits were *test-harness behavior*, not proof that the thumbnail decoder blocked Qt.

Grid matrix now attempts page 1 just once with a short 0.3-s UIA lookup, recording `not-ready` (expected while the first batch is still unavailable); it starts its wheel sender **without awaiting query completion**. A dedicated wheel-sending thread runs every 15 ms independently of costly UIA or fallback pager log reads in the position-probing thread. The initial query count is only sampled **after** the entire round trip for diagnostics. A Grid with no mounted cards cannot physically move, but the test no longer pauses or waits for a query to finish before sending input. On subsequent neighbor reads and card preparation, wheel sending remains concurrent, even if the Qt viewport temporarily stops advancing. The only accepted endpoint is an observed image #300 in the visible first-to-last range, followed by an observed first image #1. Each direction is time-bounded and its sender is always stopped/joined. Production behavior and the normal multi-view smoke are not changed. The fast wheel target uses the launched window's lower-middle Grid region without enumerating every thumbnail's UIA descendants.

`write_report` updates `benchmark_report.txt` and `best_combination.json` after each case; `main` prints the same comparison in the console when the matrix ends. Only *clean complete* journeys with source `-x` evidence are eligible. A browsing-only Grid heartbeat gap counts when the individual `phase=gui_gap` event has `mounted > 0` and occurred within the recorded wheel-action windows; startup gaps are excluded. Rank by observed >=250 ms gap count per trial, then worst gap, then median round-trip duration. Fewer than three clean repeats per case, any missing/incomplete case, or mixed destinations produces only a provisional or insufficient-evidence assessment. No interpretation that missing >=250 ms events proves a jitter-free UI.

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix --grid-target-card 300 --grid-seek-timeout 12 --runs 1 --cycles 1 --ack-user-state
```

No new dependency, source-image mutation, training/inference or provider call. Native-Windows replay is still required.

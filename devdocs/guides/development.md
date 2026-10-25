## Startup responsiveness follow-up (#168 / #169, 2026-10-25)

Startup now queues a one-shot, non-generative connection check after the first event-loop turn. It does not wait for connectivity before admitting normal interaction. Optional credential/preset/custom-LoRA summaries read on workers and reject superseded deliveries; startup results must not erase a typed key. Saved-vault discovery for Generate/Queues is deferred until worker credential resolution. Logs form/controller construction remains on the GUI thread but happens only on first request; normal navigation must not eagerly access its lazy properties.

Each connection check has a request-owner token as well as its configuration revision. Changing backend/endpoint or saving/removing a key retires the old token before cancelling its future, enabling a fresh manual check immediately; obsolete callbacks must not clear the new owner's busy state. Process environment changes are compared in memory before accepting results, never logged. External Credential Manager changes are not watched; explicit rechecks read current credentials. Do not present last-check status as continuous credential monitoring.

Session restoration and Qt widget construction remain synchronous. Existing startup timings now split Generate controller creation, queue/image-operation controllers, page wiring, session restoration and restored backend/catalogue application. The historical `first paint` mark is a queued event-loop milestone, not certified usable-window or actual-paint timing. The first gallery heartbeat also includes construction after the timer is armed: the reported 30.29-second sample is not an additional independent gallery freeze. Heartbeat thresholds and the gallery benchmark protocol are unchanged.

The owner log `dtflow-d837d8c2195e` recorded 50.26 seconds total, including broad 18.26-second wiring/restore, 8.19-second final-initialization and 6.11-second Images-form phases. Its source commit is unknown. This evidence motivates deferred work but does not prove which individual call caused the owner's delay, or that the change fixes it. Compare repeated cold/warm native Windows launches on exact builds with comparable state and font/platform settings; record startup milestones and first usable interaction separately. Offscreen fixture timing, native Windows/DPI and packaged acceptance remain distinct; #169 retains the measurement gate and any remaining bottlenecks. #106 retains the independent Logs memory-budget work.

### Owner Windows smoke observations (2026-10-25)

The owner supplied the following source-run logs and confirmed that autoconnect works in the correct credential context, then authorized squash merge of PR #171. All three report Windows, Qt 6.12.0, 96 DPI and DPR 1, with no WARNING/ERROR/CRITICAL records in the inspected snapshots. The logs identify version 5.2.0 and source modification time `2026-10-25T17:22Z`, but `source_commit` is unknown: do not promote these into exact-head build attestation.

| Diagnostic session | Recorded startup milestone | Startup cloud check |
| --- | ---: | --- |
| `3f96de85acf2` | 12.31 s | `ok`, approximately 5.97 s after check start |
| `7ac3750f2877` | 8.30 s | `unknown`, no provider API call recorded; owner confirmed the credential context was absent |
| `86183bee6cc3` | 7.06 s | `ok`, approximately 4.79 s after check start; clean close counters report no rejection/drop/write/serialization loss |

These launches support the scoped autoconnect smoke result and show shorter recorded startup milestones than the earlier 50.26-second incident. They do not isolate the effect of code changes from warm caches, environment or workload differences, and do not establish a repeatable speedup or completion of #169. First-use Logs navigation, other DPI settings and packaged execution were not demonstrated by these logs. No additional tests, renders or provider requests were run to inspect this owner evidence.

## PR #104 handoff — current Grid benchmark and next optimization (2026-10-19)

**Current behavior overrides earlier interim descriptions in this guide.** The interactive Windows Grid matrix polls an enabled numbered page-1 chip roughly every 200 ms after the spinner, clicks once, verifies first visible image **#1**, and then drives a time-bounded, continuous down/up wheel journey independent of subsequent batch queries. Position acceptance uses the **first visible saved-image index**, not `total`, `buffered`, or an oversized initial `last` index. The final physical page-1 click remains. The matrix never changes Sort. The separate eight-profile multi-view benchmark still exists.

Console and saved JSON/text reports identify the *provisional* best `-ml/-mc` combination from clean completed journeys, prioritized by Grid GUI heartbeat gaps **≥100 ms** during actual scrolling, worst gap and median round-trip time; startup is measured separately. In owner Windows `10190342.zip`, `100/300` completed in 7.45 s with one >=100 ms stall (worst 111 ms), and `200/600` completed in 6.93 s with one (worst 385 ms). Initial page-1 readiness was 3.23 s versus 10.70 s. These are one-run owner observations, not a definitive production default or a statistically verified winner.

The **next PR** should isolate startup readiness/first usable page, particularly the query/preparation/Grid-mount path at higher `-ml`, without changing the completed PR #104 benchmark protocol or prematurely tuning production defaults. Require repeated native Windows measurements, stable real navigation and continuous scrolling across batch boundaries.

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix --grid-target-card 300 --runs 3 --cycles 1 --ack-user-state
```

---

# Development tools and conventions

See [AGENTS.md](../../AGENTS.md) for repository rules, [current development status](../current_dev_status.md) for implementation orientation, and [testing](testing.md) for validation commands, and [version bump and release](releasing.md) for the uv recipe and owner build handoff.

## Import conventions

Group imports into standard-library, third-party, and local-project sections, separated by blank lines. Within each group, alphabetical sorting is optional: preserve deliberate import initialization order where it matters. Ruff does not enforce import sorting (`I001`); unused imports (`F401`) and the remaining lint rules stay enabled. The formatter and 120-character line-length target are unchanged.

## Design principles

These are implementation requirements, not a claim that every feature exists today.

- **Reproducible.** Record resolved prompts, seeds, model and LoRA identifiers, effective parameters and output references.
- **Honest about outcomes.** Require successful terminal status, valid final outputs and durable saving before reporting application success. Record ambiguous remote attempts without silently replaying them; keep previews distinct.
- **Explicit selection.** Do not fall back to another backend or credential after failure. Initial cloud generation does not opt into PAYG; subscription quota may still be consumed.
- **Separated layers.** Keep UI, services, backend-neutral core, persistence and provider adapters separate. Provider types stay inside the integration boundary.
- **Offline by default.** Ordinary tests must not submit real or potentially billable generation.

## Environment and compatibility evidence

Run project tools through `uv run` so they use the project environment. The lockfile records resolved dependencies. The current compatibility path pins NumPy 2.3.5 for the tested legacy Windows machine; see the [recorded dependency findings](../research/drawthings-grpc-proof.md#observed-compatibility-evidence). Source execution does not establish packaged Windows compatibility.

## Agent environment setup

For bounded Ubuntu/WSL 1 testing on the Pentium E6500, see the
[2026-10-16 compatibility evidence and limitations](../research/wsl1-e6500-smoke-2026-10-16.md).
This workaround is not a replacement for the locked environment or general Linux support.

Check readiness first; custom environment startup may already have provisioned
the checkout.

In every agent shell, including the first, apply these settings before running
uv or Python:

```bash
export UV_LINK_MODE=copy
export QT_QPA_PLATFORM=offscreen
export UV_SYSTEM_CERTS=true
export UV_PYTHON_PREFERENCE=only-managed
```

From the target checkout root, check the selected Python version, SQLite,
development dependencies and headless Qt initialization:

```bash
uv run --frozen python -c 'import pathlib, sqlite3, sys; import dtflow, pytest, pytestqt; from PySide6.QtWidgets import QApplication; expected = pathlib.Path(".python-version").read_text().strip(); actual = ".".join(map(str, sys.version_info[:len(expected.split("."))])); assert actual == expected, (actual, expected); app = QApplication([]); print("Environment ready:", sys.version.split()[0], "SQLite", sqlite3.sqlite_version)'
```

`uv run --frozen` synchronizes from the committed lockfile as needed. If the
check passes, run the requested command without rerunning setup. This checks
basic test readiness, not native desktop acceptance. If prerequisites are
missing, use setup in a disposable agent environment, then repeat the check:

```bash
bash tools/setup-agent.sh
```

Setup defaults to the checkout containing the script. `DTFLOW_PROJECT_ROOT`
optionally selects another checkout; run readiness checks and project commands
from that checkout. To select it, pass its absolute path:

```bash
DTFLOW_PROJECT_ROOT="/path/to/dtflow" bash "/path/to/tools/setup-agent.sh"
```

The target must contain `pyproject.toml`, `uv.lock` and `.python-version`.
Python comes from that file rather than a hard-coded checkout path or Python
version. Setup installs a uv-managed interpreter, runs `uv sync --frozen` and
prints the Python/SQLite versions. It does not update the lockfile. Printing a
SQLite version is diagnostic information, not acceptance of persistence behavior.

On Debian/Ubuntu Linux, setup installs `libegl1`, `libgl1`, `libxkbcommon0`,
`libdbus-1-3` and `libfontconfig1` through apt-get. Root or non-interactive
sudo is required. Both APT commands use `-o APT::Sandbox::User=root` to avoid
switching to the `_apt` user in disposable containers that block user/group
changes. This does not grant container permissions or package-mirror access;
APT failures still stop setup. Other Linux distributions fail clearly and require manual
system-library setup. macOS and Windows Bash (Git Bash/MSYS/Cygwin) skip the Linux
package step; this platform routing is not evidence of native runtime acceptance.
Windows needs Bash to run this file; it is not a PowerShell script.

Existing uv is reused. When missing, Python with venv/pip support bootstraps
`uv==0.12.23` in ignored `tmp/agent-bootstrap/`, separate from the project's
`.venv`. Setup requires network access to package repositories and Python
downloads. Report failures without hiding them with `|| true`; stop dependent
commands only while their prerequisites remain unavailable.

These settings copy cached packages instead of linking, run Qt headlessly, use
system TLS certificates for uv, and select only uv-managed Python. Setup exports
them for its own subprocesses; executing it with Bash does not export them back
into the caller or future shells. If uv was bootstrapped, also export
`PATH="$DTFLOW_PROJECT_ROOT/tmp/agent-bootstrap/bin:$PATH"` after setting
`DTFLOW_PROJECT_ROOT` to the absolute checkout path (use `Scripts` instead of
`bin` in Windows Bash).

Use `uv run --frozen` for later agent commands to retain the committed lockfile.
Unset `QT_QPA_PLATFORM` or select the native platform when explicitly performing
interactive desktop acceptance. This setup is not intended for immutable
production/prebuilt runtimes. It runs no tests, UI renders, training, inference,
model downloads or provider calls.

## Images performance diagnostics (`-x --dev`)

Use the normal DTFlow launch command with `-x` (equivalent to `--dev`) to enable sanitized debug timing in the shared rotating `dtflow-<session>.jsonl` stream. **Without `-x`, development gallery measurements are not emitted.** The writer, shared JSONL readers and #104 benchmark migration are implemented in PR #105. See the [delivery audit](../plans/2026-10-19-structured-log-viewer.md) for ordinary CI evidence; repeated native replay, fault handling, precision parity and storage/policy acceptance remain [#107](https://github.com/djebaz/dtflow/issues/107).

New normal/development sessions produce JSONL only, asynchronously through the existing logging authority; automatic text/gallery CSV fanout is removed. Historical files stay untouched and are not converted or pruned as JSONL. Listener aggregates and source-side thumbnail/GUI summaries retain distinct populations and source windows; individual wheel receipts and GUI gaps >=100 ms remain visible. Admission rejection/overflow, failed writes, missing startup/segments or a missing close seal mean incomplete evidence, not zero stalls. The initial queue/capture/aggregation bounds are safety defaults, not measured performance. See the [implemented-source boundaries and counter policy](../architecture/structured-diagnostics.md#ownership-and-dependency-boundaries).

Look for dotted `gallery.*` events with typed `data`; never parse `msg` to recover fields. Most durations are elapsed wall milliseconds observed by the application, not benchmarks or GPU/inference timings; explicitly named CPU fields use worker-thread CPU time. Source summaries use `gallery.source_summary` with `data.phase`, emitted at most once per minute per phase (samples accumulate between emissions; close forces a final one, so up to a minute of summaries is lost if the app is killed; individual gaps of 100 ms or more are still logged immediately); listener batches use `gallery.performance_aggregate` with actual source statistics in `agg` (no inferred global p95):

- `runtime`: OS platform, Qt version and main-screen device-pixel ratio/logical DPI (no hostname or paths).
- `gui_gaps` source summaries / `gallery.gui_gap` measurements: the development-only heartbeat retains the existing >=50 ms summary sampling and >=100 ms individual-event thresholds, with recorded `Grid`/`List`/`Detail` mode, mounted count and busy flags. These are observations, not proof of a particular cause; scheduling/sleep can also delay a timer. Earlier documentation saying 250 ms was stale; the inspected #104 source already uses 100 ms, and this migration does not change that threshold.
- `query_start` / `query_done`: query window offset/limit, total async elapsed time, returned count and live/snapshot source.
- `query_locate` / `query_read` / `query_extension` / `query_facets`: separate timed anchor lookup, bounded primary read, optional extra read and filter-choice work.
- `query_worker`: separate `sql_wall_ms` / `sql_cpu_ms` for SQLite and `build_wall_ms` / `build_cpu_ms` for gallery-record construction, including path discovery and availability filesystem checks. Build wall time is not a pure Python CPU/decode span.
- `query_delivery`: delay between completion of the async read and delivery to the GUI thread. This distinguishes worker/read time from a busy Qt event loop.
- `prepare_slice` / `prepare_complete`: sliced widget preparation cost, total preparation time, created/reused counts and success.
- `auto_refresh`: five-second polling emits `started`, `skipped`, sampled `unchanged`, `signature_error` and `late_skipped` actions. Skip reasons are a structured list (`query`, `adjacent`, `preparation`, `mount`, `page_jump`, `anchor_restore`, `filter_debounce`, `signature_check`); late skips can also name `inactive`/`disabled`. Snapshot/failure retries explicitly use `live_recovery`. `gallery.query_start.data.trigger` distinguishes `manual`, `filter`, `auto`, `live_recovery`, `retry`, `removal` (completed or failed Trash/Delete), `removal_reconcile`, `activation`, `snapshot_mismatch` and `navigation` requests; the default `refresh` marks an unlabelled caller. A deferred refresh keeps the merged request's latest trigger, except that an `auto` poll never relabels an explicit pending request. These diagnostics do not alter refresh gates or mutation authority.
- `adjacent_start` / `adjacent_read`: adjacent configured-size batch fetch (20 records by default), direction, returned count and async elapsed time.
- `adjacent_reconcile` / `render_page` / `grid_layout`: Qt update time and mounted/added/removed card counts. `grid_layout` is the explicit synchronous fallback (e.g., deterministic fixtures); `grid_mount_complete` reports the elapsed wall-clock period for GUI-sliced live mounting and retained/inserted/moved card counts. The elapsed period spans event-loop yields and is **not** one continuous GUI freeze.
- `thumbnail_end_to_end`: worker enqueue-to-result delay (includes worker queueing and decoding), success and concurrency count. Up to six thumbnail tasks remain active. Thumbnail phases use source summaries, not listener reaggregation. `thumbnail_decode` records wall and worker-thread CPU time for each attempted Qt image-reader decode (including reader setup, header/scaling work and any plugin I/O); it does not measure lease wait, path discovery or cache lookup and is not a claim of pure codec CPU cost. A cached-decode failure followed by an original retry counts two attempts. This added population is separate from the unchanged worker/end-to-end populations and has no inferred p95. `thumbnail_cache_hit`, `thumbnail_cache_miss` and `thumbnail_cache_write` distinguish cache lookup from background publication.

For a Windows smoke test, preserve the rotating log and the exact PR/build revision. Exercise default 20-record page changes and scrolling up to the 100-card cap with Auto-refresh On. For the historical stress comparison, explicitly launch with `-ml 100 -mc 300` and perform a 100→200 append. In both cases, compare nearby `gui_gap` events against `query_delivery`, `prepare_complete`, `render_page` and `grid_layout` timing. Distinguish long database reads, GUI delivery delay and Qt layout stalls instead of attributing every pause to scrolling.

**Privacy:** these metrics never include prompts, image/output/metadata paths, selected IDs, request JSON, SQLite parameters, or credentials. They don't change query cadence, rendering behavior, retry semantics or provider billing. A burst of `render_page` and `grid_layout` entries on unchanged five-second polls is a useful signal of unnecessary repaints. Inspect timing patterns alongside visual smoke evidence rather than assuming every repaint is expensive.

## Native Windows Images performance matrix (experimental PR #104)

**PR #105 reader migration:** the tools now consume the shared JSONL reader; historical text/gallery CSV is not supported or converted. The current Grid contract at the top of this guide supersedes the chronological interim journey notes below. Native benchmark acceptance remains #107.

The [Windows gallery benchmarking plan](../plans/2026-10-18-windows-gallery-benchmarks.md) describes the **separate** development-only pywinauto driver. This drives the actual PySide6 Images interface on an **interactive Windows desktop**, unlike offscreen UI renders or the synthetic transition microbenchmark. Its readers consume new structured JSONL sessions through the shared reader; historical text/per-session gallery CSV is unsupported. New sessions do not create automatic gallery CSV; explicit benchmark CSV reports remain separate. No second Qt timing subsystem is introduced.

**This matrix varies the gallery tuning constants, not the paging defaults.** All runs use source `-ml 20 -mc 100 -format jpg`. Built-in profiles exercise worker count, lookahead, GUI preparation/mount budgets, thumbnail side length, pending disk-cache writes, disk-cache pruning, refresh cadence and filter/save/page-jump debounces. `--profiles-file` accepts named JSON overrides; missing fields use the unchanged defaults. Only one tuning family should change at a time for interpretable comparisons.

Run from the checked-out PR #104 source branch after `uv sync --frozen`, in PowerShell with the desktop unlocked and no competing DTFlow process:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

To exercise the built-in matrix (longer, not a quick smoke):

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --runs 2 --cycles 2 --ack-user-state
```

`--out tmp` selects the output root (also the default). Each invocation prints and creates `tmp/gallery_bench_smoke/MMddHHmm/` using local month/day/hour/minute; a same-minute collision is refused without overwriting evidence, so wait a minute or choose another root. Reports are `results.csv`, `results.json` and `summary.csv`, with each trial’s `profile.json`, `trial.json` and source evidence retained beneath that timestamped directory. Historical runs included text/gallery CSV; new JSONL evidence uses the shared reader, with native/precision acceptance tracked in #107. A failed trial does not count as a performance win. The automation’s `dispatch_ms` measures action dispatch, not click-to-paint time, and heartbeat gaps under 50 ms are not recorded as freeze samples.

Windows virtual environments can use a launcher PID different from the interpreter hosting Qt. The harness reads the child-published `app-process.json` and records both `launcher_pid` and `app_pid` in `trial.json`; it never attaches to an arbitrary existing DTFlow window. Failed-trial cleanup targets only that launcher's process tree, and a cleanup failure stops the matrix. See the [PID follow-up and pending native smoke](../plans/2026-10-18-windows-gallery-benchmarks.md#windows-virtual-environment-pid-follow-up-2026-10-19).

**Safety and reproducibility:** `--ack-user-state` is mandatory because the GUI uses the normal source-checkout state and may modify saved viewer preferences or disposable thumbnail cache. This harness does **not** sandbox that existing catalogue; use disposable state where possible and back up important state before running. Do not run a cache-eviction trial against a valuable populated cache. The driver automates Images view switching, verified page-chip changes, up/down scrolling, fullscreen enter/Escape exit and thumbnail-size slider movement. It does not interact with text search. Missed UI interactions become recorded warnings rather than aborting later steps; a warning trial is not a clean benchmark result. It does not click generation, export, removal or cache-clear actions; no inference or provider submission is intended. Pin the source revision, Windows/DPI, image set, cache condition and input sequence, and do not compare different cache conditions as though they were controlled. The test is **not Windows-accepted or benchmark-validated** until the owner runs it; review the complete report and actual GUI behavior.

A gallery with only 100–200 images cannot meaningfully test a 20,000-entry disk-cache eviction threshold or saturate a 256-entry write queue; those variables need a distinct safe stress scenario. Likewise, refresh profiling needs runs long enough for multiple polls/full refreshes. Keep these results marked *unmeasured* rather than pretending short smoke runs optimize every parameter.

## Diagnostic probes

The diagnostic probes' help is available without submitting generation:

```bash
uv run python tools/drawthings/drawthings_probe.py --help
uv run python tools/drawthings/grpc_capabilities_probe.py --help
```

Metadata probe option pairs are `-f/--filename`, `-s/--sha256`, `-z/--size`, `-a/--api-base` and `-m/--matrix` for `get_byom_probe.py`; the local fingerprint probe uses `-f/--filename` and `-s/--known-sha256`, alongside `-a/--host` and `-p/--port`. Existing long spellings remain supported.

The gRPC capability probe is read-only/non-generative: it calls only Echo, FilesExist, Pubkey, and Hours and can save a sanitized JSON report. GenerateImage and UploadFile are deliberately excluded.

Diagnostic tools can contact a real Draw Things server or direct DT+ Cloud Compute and submit potentially billable generation, including through Bridge mode. Read `--help` and select the intended operation deliberately. The direct-cloud probe reads `DRAWTHINGS_API_KEY` from the environment; it does not accept the key on the command line. Keep credentials and tokens out of logs and saved request metadata.

## Desktop development and packaging

Read [DESIGN.md](../../DESIGN.md) before adding or changing a desktop tab. Canonical Designer forms are under `src/dtflow/ui/forms/`; shared QSS is under `src/dtflow/resources/styles/`. Business rules and backend protocols belong behind shared services, not in UI or generated Designer Python.

Use the [Windows desktop build guide](building.md) for the canonical GUI build and owner acceptance steps. [Windows packaging findings](../research/windows-packaging.md) retain historical console trials and target-machine evidence. Source execution does not establish packaged compatibility. The initial distribution target is the Qt application, not a separate packaged CLI.

## Offline Images presentation

The Images tab and standalone launcher load the same canonical gallery form and
fixture controller. Use the shared QSS/token pipeline; do not create a separate
palette or copy presentation behavior into a developer-only implementation.

```bash
uv run python tools/ui/preview_gallery_detail.py --view grid
uv run python tools/ui/preview_gallery_detail.py --view list
uv run python tools/ui/preview_gallery_detail.py --state details
uv run python tools/ui/preview_gallery_detail.py --state raw
```

These commands show bundled illustrative samples without output discovery or
provider execution. The [preview plan](../plans/2026-10-05-phase-8a-ui-preview.md)
records implemented controls and validation limits. Actual saved-output services
remain governed by the [viewer plan](../plans/2026-10-05-phase-8a-viewer.md).

## Pixel-statistics detector PoC

The standalone [image detector tool](../../tools/images/README.md) reports provisional noise/blank flags and colour/spatial statistics from local images. It is read-only, uncalibrated and separate from export and job status; owner-labelled evaluation remains pending.

## Working directories

Application state and durable outputs use `.dtflow/`; retained live evidence uses `test-runs/`; diagnostics use `logs/`; disposable scratch uses `tmp/`. These repository-development paths are ignored by Git. See [runtime layout](../architecture/runtime-layout.md) before changing ownership or cleanup behavior.

### PR #104 owner UIA follow-up (2026-10-19)

Owner evidence shows baseline and workers-3 navigating Images, switching Grid/List/Detail, scrolling and changing pages before both trial runs stopped at prompt-filter lookup. Text search has been explicitly removed from the browsing benchmark. The owner's recorder shows `Full screen image view||CheckBox` and `ThumbnailSizeSlider||Slider`; the harness uses these UIA names to exercise fullscreen and slider drag/restore. Each missed click, scroll-intent verification or slider movement is logged as a warning and the next step is attempted. Status `warning` is excluded from clean performance comparisons; initial GUI/query failure still aborts that trial. The feature requires a new Windows smoke; no successful post-change run is claimed.

### Extended owner-requested gallery benchmark actions (2026-10-19)

The PR #104 smoke now uses 192 wheel notches for each up/down sweep in Grid and List, configurable via `--scroll-notches 12..240`; moves the thumbnail slider toward 55% and 210% and attempts to restore its starting value; and toggles fullscreen **from Grid**, not from Detail. Detail is still exercised as a separate view change. Retain the same wheel count, DPI, viewport and gallery image set when comparing tuning profiles. Missing UIA actions remain recoverable warnings, not automatic proof of a Qt freeze. A fresh native Windows smoke is still necessary to accept this revised sequence.

### Native Grid `-ml`/`-mc` matrix (2026-10-19)

PR #104 adds a separate `--grid-matrix` mode to the Windows pywinauto benchmark. It launches the real Qt GUI for each requested `-ml:-mc` pair and uses DTFlow's existing limit resolver, so `20:50` effectively buffers 40 cards while `300:1000` buffers 900. Test only Grid: select Images, **click page 1** (even if checked), scroll down then back up for a number of steps computed from resident batch count, and **click page 1** again. No Sort dropdown changes or other view switches. Every case has a fresh process/log directory and saves requested/effective limits and action evidence. For example:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix --runs 1 --cycles 1 --ack-user-state
```

Large limits are not meaningfully tested when the saved-output catalogue is too small; first-query shortfall is a recorded warning. Scroll dispatch is not proof of physical image movement. No new product settings, dependencies or billable services are involved. Windows acceptance is pending.

### Faster position-driven Grid matrix (2026-10-19)

The newer `--grid-matrix` smoke supersedes its fixed wheel-step count: **click page 1 → rapidly wheel down until image #300 is within the visible range → wheel back until the first visible image is #1 → click page 1 again**. It checks the UIA `Visible image range` text (as identified by the owner's Windows recorder), falling back to a benchmark-only log of `GalleryPager.present` values when Windows UIA masks the displayed numbers. The limit pair never determines scrolling distance. Short 15-ms pulse gaps do not wait for adjacent thumbnail batches; timeouts (default 12 s per direction) and unobserved positions are warnings and exclude a case from clean ranking. The normal multi-view smoke is unchanged. CLI: `--grid-matrix`, `--grid-target-card 300`, `--grid-seek-timeout 12`, with `-ml/-mc` pairs given as `ML:MC`. No Sort manipulation, generation or live-provider calls. Windows acceptance is pending.

Owner-native Windows evidence `10190234.zip` showed the old 55-ms pulse driver taking roughly 11–12 seconds **per direction** at 10/50 and 50/150, with one of two cases missing both endpoints. The revised 15-ms/12-notch coarse pulse driver and exact visible-image acceptance require a new native smoke; do not attribute earlier results to the new pacing.

### Matrix start and automatic ranking (2026-10-19)

The Grid-only Windows pywinauto matrix no longer awaits the initial `query_done` or loops on an initially disabled page-1 chip. It attempts that chip once, then sends wheel input in a **separate continuous wheel thread**, including while the initial and adjacent batches are querying/preparing and while the main thread probes visible-image positions. Slow UI Automation cannot stop the wheel sender. The first saved-image viewport may not move until Qt commits it; the benchmark records this rather than pretending images were scrolled. Only the final page-1 click retains the selected-chip verification. The normal Grid/List/Detail profile smoke is unchanged. After the matrix, the console prints a provisional/verified best `-ml/-mc` based on complete journeys and Grid GUI-stall events during scrolling; `benchmark_report.txt` and `best_combination.json` are saved incrementally. Source/Windows acceptance pending.

### Windows offscreen renderer startup

Run `uv run python tools/ui/render_states.py --out tmp/ui-renders` from the checkout. The renderer passes its screen configuration as a path relative to the launch directory: Qt uses colons to separate platform arguments, so an absolute `C:\...` path in `offscreen:configfile=...` is parsed as `configfile=C` and exits with `Could not find platform config file C`. Launch from the repository drive when using the automatic configuration. Explicit platform overrides remain unchanged.

For Windows offscreen rendering, the script defaults `QT_QPA_FONTDIR` to `%WINDIR%\Fonts`, including when offscreen has configuration arguments. An explicitly supplied font directory is preserved. These settings affect offline screenshot startup; they are not native Windows/DPI acceptance.

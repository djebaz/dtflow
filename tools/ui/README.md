## PR #104 current Windows Grid benchmark contract (supersedes interim notes below)

This is the authoritative quick reference for the latest PR #104 Grid matrix. Earlier dated notes below document experiments and may describe abandoned startup checks or stop conditions.

The benchmark waits for the numbered page-1 chip to become enabled after the spinner, polling about every **200 ms**. It clicks the chip **once**, then checks that the **first visible image is #1**; it no longer waits for a diagnostic `page_jump` event. Once the journey begins, wheel messages continue independently of later batch queries/thumbnail preparation. The first visible image index—not the total, buffered count or end of an initially oversized range—determines when the down leg is near the requested card (±20 at #200, ±25 at #300). The return must reach first-visible #1. Unverified trials are excluded from ranking.

**Output:** inspect per-trial `trial.json` and the application `-x` logs, plus the end-of-run console ranking, `benchmark_report.txt` and `best_combination.json`. Grid stall threshold is **≥100 ms** only during measured scrolling; startup readiness is tracked separately. A single clean run per configuration is *provisional*; prefer at least three.

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix --grid-target-card 300 --runs 3 --cycles 1 --ack-user-state
```

**Native evidence / handoff:** owner observed clean runs in `10190342.zip` for `100/300` (page-1 readiness 3.23 s, round trip 7.45 s, one ≥100 ms Grid stall, worst 111 ms) and `200/600` (readiness 10.70 s, round trip 6.93 s, one ≥100 ms Grid stall, worst 385 ms). Faster scrolling for the large batch does not establish a better overall UX. The owner reports the initial spinner/page-1 readiness remains unnecessarily long. **Do not optimize it in PR #104**; use a separate follow-up PR to investigate first usable page/Qt preparation and mounting under large batch sizes, while preserving browsing correctness. No production `-ml/-mc` defaults change here.

---

# DTFlow UI screenshot scripts

## Grid-only dynamic -ml/-mc scroll matrix (PR #104)

Run the same **saved-image journey** for every `-ml/-mc` configuration, without opening Sort or changing the user's ordering: Images → Grid → **click page 1** → fast wheel scroll until **image #300 is inside the visible range** → fast wheel scroll back until **image #1** is first → **click page 1 again**. There are no sleeps waiting for whole batches to load, and no List/Detail/fullscreen/thumbnail-slider actions in this mode.

**High-batch false-positive regression (10190324.zip):** the observed first pager update was `1–200 of 833` with 200 buffered, while the viewport had not scrolled. A previous stop rule accepted card #200 merely because `last=200`, completing the down leg after **one wheel pulse** and falsely ranking the trip as ~0.49 s. The down-leg stop now uses **only the first visible index**, requiring it to be within a bounded neighborhood of the requested target (±20 for #200, ±25 for #300). An oversized initial visible range cannot satisfy the destination. `target_tolerance` is recorded for audit; stuck positions time out rather than winning a ranking.

**Position parsing invariant:** only the exact visible-range label (`first–last of total`) is accepted. Neither the trailing `of total` number nor the independent `N buffered` status is the scroll position. For example, `1–20 of 200` alongside `200 buffered` does **not** mean the user has reached card #200.

The native Windows recorder identifies the actual position control as `Gallery batch navigation → Visible image range||Text`. The driver reads its numeric text when UIA exposes it. Some Qt/Windows combinations return only the static accessible name; a **benchmark-child-only** diagnostic of the exact `GalleryPager.present` values provides a fallback. Each action records its initial/final visible image index, observed progress, input count and duration. A failed or undersized journey is a warning, **not** a successful benchmark. The return to image #1 must be confirmed *before* the final page-1 click.

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix --runs 1 --cycles 1 --ack-user-state
```

Defaults: `--grid-target-card 300`, `--grid-seek-timeout 12` seconds **per direction**, 15 ms between short wheel pulses without extra between-batch waits. `--scroll-notches` (default 192) controls the maximum pulse size in Grid matrix mode through `min(12,max(2,notches//16))`; pulses reduce automatically near the destination to limit overshoot. You can choose a shorter test or a smaller matrix:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix 20:50 100:300 300:1000 --grid-target-card 300 --grid-seek-timeout 12 --runs 1 --cycles 1 --ack-user-state
```

The default cases are `20:50 20:100 50:150 100:300 200:600 300:1000`. Each launches a **separate DTFlow source process** using the real validated `-ml/-mc` flags. Since DTFlow rounds `-mc` down to whole batches, the effective mounted-card caps are 40, 100, 150, 300, 600, 900. Reports retain the requested/effective limits, position trace, end position, result and original sanitized `-x` metrics. The existing catalogue must contain **at least 300 matching images** to reach the default target; otherwise the trial records a warning and cannot be used for comparison. Source image files are never generated, edited or deleted; the normal viewer may still save browsing preferences/cache.

This is intentionally a **fast, position-driven scrolling stress test**, not a wait-until-every-thumbnail-is-decoded test. Grid-card displacement and end points must be observed, rather than inferred from how many wheel events were sent. Treat mount duration and GUI heartbeat separately from input-to-paint latency. The changed test awaits a native Windows acceptance run.

### Equal starting position despite a busy first batch

The real Windows `10190307.zip` trace used `--grid-target-card 200`, but the `-ml 80 -mc 200` trial had already reached **visible images 281–295 before the measured down leg**; the automated user sent 555 wheel notches and hit the 12-second timeout while reversing the overshoot. Its maximum first-visible image was 301, even though the target was 200. The early page-1 click is dispatched without verifying the resulting viewport, so it cannot establish an equal starting position by itself.

The Grid matrix now **homes upward with live wheel events until `Visible image range` confirms first image #1** before beginning the timed down-to-target-and-back journey. Homing uses the independent wheel sender, so it also sends input while the initial batch is querying/preparing and while UIA/log position reads are delayed. It is separately recorded as `grid-home-start` and excluded from the measured round-trip and browsing-only stall ranking. If image #1 cannot be confirmed within the configured `--grid-seek-timeout`, the run is warned and not ranked; it does not silently start at image #281 or pass by clicking page 1 afterward. This avoids mistaking `-mc` (a resident-card buffer cap) for a limit on how far a user may browse.

### Continuous scrolling without a query barrier; direct console report

The Grid-only matrix **does not call `_await_query` before the first page-1 attempt or first wheel input**. It tries the actual page-1 chip immediately after entering Grid. The chip is disabled until Qt commits an initial batch; if unavailable, the runner records `click-page-one-start: not-ready` and **starts wheel input anyway**, rather than blocking for a batch. The final page-1 click is still performed. An initial viewport with no mounted cards physically cannot scroll yet: wheel events are still sent and their first visible movement is independently observed after Qt makes cards available.

The mouse-wheel sender runs in a dedicated short-lived Python thread, independent of the main thread that reads UI Automation / pager logs. It continues to send wheel events every 15 ms even when database reads, staged card construction, thumbnail decoding or cross-process UIA calls take longer. Each direction has a bounded timeout; the sender is stopped and joined on every exit. A missing destination is **never** reported as success. Each action includes timestamps, input/polling durations, pulses and observed image positions. This is a benchmark-only change, not a production scrolling behavior change. It must be accepted on real Windows before drawing performance conclusions.

After the final case, the console prints a **Grid matrix performance table** and `PROVISIONAL BEST: -ml ... -mc ...` or `BEST COMBINATION`. The comparison excludes incomplete/missing-diagnostic trials and **ignores startup GUI gaps**; it first minimizes individually logged Grid GUI heartbeat gaps >=250 ms while wheel input was active, then the worst such gap, then the median verified round-trip duration. It writes `benchmark_report.txt` and `best_combination.json` alongside the CSVs, and keeps them updated after each case. With fewer than three clean trials per case, it explicitly labels a leader *provisional*.

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --grid-matrix --grid-target-card 300 --runs 1 --cycles 1 --ack-user-state
```

## Native Windows gallery performance benchmark (PR #104)

The optional, Windows-only `pywinauto` driver launches a **real source DTFlow GUI** and exercises Images navigation, Grid/List/Detail, verified page-chip changes, 192-notch up/down wheel sweeps (configurable through `--scroll-notches`), **fullscreen while Grid is active**, and thumbnail-size sweeps toward **55% and 210%** with restoration of the initial value. Text search is intentionally out of scope. The experimental profiles vary the eight groups of performance constants recorded in the [benchmark protocol](../../devdocs/plans/2026-10-18-windows-gallery-benchmarks.md); pagination stays **20 records / 100 mounted cards** for every run. The runner reads `-x` diagnostic logs rather than replacing Qt telemetry; PR #105 migrated both benchmark tools to the shared JSONL reader. Historical text/gallery CSV is unsupported; repeated native acceptance is tracked in [#107](https://github.com/djebaz/dtflow/issues/107).

From an interactive Windows desktop with a prepared `uv` environment:

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

The interactive smoke defaults to **192 wheel notches per direction** (16 small bursts rather than eight); use `--scroll-notches 240` for an even longer, controlled sweep (12–240 allowed). The slider sweeps small → large → original, preserving the user's starting size if UIA restoration succeeds. The fullscreen toggle now occurs in Grid rather than Detail. Both changes increase the workload; compare only runs with the same sequence and viewport dimensions.

`--out` is an output root (default `tmp`), not the final run directory. The script prints and creates `<root>/gallery_bench_smoke/MMddHHmm/` using local time, for example `tmp/gallery_bench_smoke/10120040/` on October 12 at 00:40. Existing roots are allowed; an existing timestamped run is refused without overwriting evidence. Wait until the next minute or choose another root after a same-minute collision. Report files include `results.csv`, `summary.csv` and, inside each profile/trial directory, `trial.json` plus the real application logs. Missing UI clicks, wheels and slider drags now produce per-step `warnings` in `trial.json` and stderr, and the driver continues through the remaining actions. A run with warnings is marked `warning`, not `ok`, and excluded from clean performance comparisons. Core GUI startup or missing initial gallery data still fails the trial. A failed trial prints its `step` and sanitized exception message. Check those before treating automation failure as gallery slowness.

**Native acceptance is still pending:** the owner's later baseline and workers-3 smoke both navigated Images, switched views, scrolled and changed page chips but timed out at the unrelated prompt-search field. The field has been dropped from the smoke. The owner's recorder confirms the actual UIA controls `Full screen image view||CheckBox` and `ThumbnailSizeSlider||Slider`; new interactions and warning behavior have not yet been replayed on Windows. Avoid ranking profiles until a complete warning-free owner smoke succeeds. The tool reads the live saved-output catalogue, and the app can update viewer preferences or disposable thumbnail cache; the `--ack-user-state` flag is required. It performs no inference or generation.


The **UI renders** Actions workflow uploads a ZIP to Google Drive by default, including automatic PR renders.
For manual runs, choose `upload_mode: images` to upload every PNG individually with up to four concurrent
file uploads instead of an archive. ZIP mode uploads no separate preview PNG. The run summary links the
archive or individual image previews; the upload manifest retains successful files and any failures.
Explicit Drive rate-limit responses use bounded exponential backoff; other errors fail clearly.

Offscreen Qt renders of the real DTFlow forms under the real `dtflow_dark.qss`. Run from the repository root (or set `DTFLOW_ROOT`) with the locked environment:

PR #146 adds `queues-50-inspector-minimum-width`, `queues-51-inspector-long-backend` and `generate-50-inspector-after-queues` to `render_states.py`. These fixtures hold the right rail at its configured minimum width and switch between Queues and Generate to check hidden-page sizing. They run with the corresponding `--category queues` or `--category generate` selection (or `all`), using intercepted workers and synthetic presentation data. Fixture presence is not execution evidence; no new captures were produced in the fixture-extension pass, and native Windows/DPI acceptance remains separate. On Windows offscreen runs, the renderer defaults `QT_QPA_FONTDIR` to `%WINDIR%\Fonts`; see the [development guide](../../devdocs/guides/development.md).

```powershell
uv sync
$env:QT_QPA_PLATFORM = "offscreen"   # scripts also set it by default

# Full-window screens: Connection, Generate, Queues, Images (fake backend, no network)
uv run python tools/ui/shots.py tmp/ui-renders/screens

# State matrices (hover/pressed/focus/disabled/invalid/checked...) per element family
uv run python tools/ui/states.py tmp/ui-renders/states
uv run python tools/ui/states.py tmp/ui-renders/states buttons inputs   # only some families

# Gallery Grid / List / Detail with fixtures (repo tool)
uv run python tools/ui/preview_gallery_detail.py --capture out/gallery-grid.png --view grid --size 1280x900
uv run python tools/ui/preview_gallery_detail.py --capture out/gallery-list.png --view list --size 1280x900
uv run python tools/ui/preview_gallery_detail.py --capture out/gallery-detail.png --view detail --size 1280x900
uv run python tools/ui/preview_gallery_detail.py --capture out/gallery-detail-open.png --view detail --state details --size 1280x1000
```

`states.py` families: buttons, connection, gallery, inputs, checks, lists, tabs, labels, popups.

## Interactive Windows gallery performance automation (PR #104, draft)

This test is **not** an offscreen capture. `windows_gallery_benchmark.py` uses optional [pywinauto](https://github.com/pywinauto/pywinauto) to drive a separate, real DTFlow GUI process on an unlocked Windows desktop. It exercises Images Grid/List/Detail, pager chips, scrolling and positive-prompt filter editing and reads the existing `-x` gallery diagnostic logs. It does not generate images.

The benchmark experiments vary the screenshot-listed **6 thumbnail workers, 3 screens of lookahead, 4 ms preparation/mount budgets, 600 px thumbnails, 256 pending cache writes, 20,000/16,000/500 cache-eviction values, 5 s/30 s refresh and 300/250/40 ms debounce settings**. Source-only `-ml 20 -mc 100` is fixed for every profile; it is **not** a tuned variable.

From PowerShell in the PR #104 checkout (after `uv sync --frozen`):

```powershell
uv run --frozen --with pywinauto==0.6.9 python -m tools.ui.windows_gallery_benchmark --out tmp --profiles baseline workers-3 --runs 1 --cycles 1 --ack-user-state
```

Each invocation reserves a fresh timestamped folder under the output root as described above. `--ack-user-state` acknowledges that source DTFlow browses your **existing** saved-output state and may change preferences or disposable thumbnails; this tooling does not provide an isolated test catalogue. Avoid running it against valuable cache data without a backup and do not run another DTFlow instance concurrently. Pywinauto accessibility support, Windows latency, DPI and packaged execution have **not** been validated by Linux tests or static renders. Review per-run `trial.json`, source diagnostic evidence, `results.csv` and `summary.csv`; an absent event-loop gap is not proof of a zero-millisecond freeze. The current benchmark readers consume JSONL through the shared Qt-free reader and reject incomplete evidence for clean ranking; historical text/gallery CSV is not supported or converted. Automatic gallery CSV is no longer produced; report CSV remains an explicit tooling artifact.

See the [implementation plan and full matrix command](../../devdocs/plans/2026-10-18-windows-gallery-benchmarks.md) and the [development performance guide](../../devdocs/guides/development.md#native-windows-images-performance-matrix-experimental-pr-104). The 100–200-image everyday benchmark cannot meaningfully tune cache eviction at 20,000 entries without a separate, safe stress dataset.

## Workflow-state filenames

### PR #102 gallery squash preparation

Current gallery defaults are 20 records per batch/page chip and at most 100 buffered cards. The existing 100/300 navigation fixtures are explicitly pinned stress/continuity scenarios, not application defaults. `render_states.py` resolves the new default fixtures from `dtflow.gallery_limits`, passes the configured batch size to the pager and shows its query limit, buffered counts and page-2 tooltip.

| State | Evidence provided when actually rendered |
| --- | --- |
| `images-103-grid-metadata-before-thumbnails` | Complete card image/text space while thumbnail workers are held |
| `images-104-grid-metadata-after-thumbnails` | The same card geometry after synthetic thumbnail delivery |
| `images-105-pager-default-20-first-batch` | Default 20-record first batch and 20-record pager chips |
| `images-106-pager-default-100-buffered` | Default maximum of 100 resident/buffered cards |

The metadata pair checks identical card/child geometry; the default states live under `image-navigation/`. All four use offline projections and held workers; they do not prove cache encoding, file removal, native scrolling/DPI or packaged acceptance. Lock/lint/test CI passed at `2d66450`, but its UI-render job was skipped, so do not treat these source-listed states as executed evidence. A fresh successful render workflow/artifact is still needed. The script reports its actual executed total; older state counts below are historical checkpoints, not a current inventory.

Render only when requested, using a fresh output directory so old PNGs cannot masquerade as current captures. In Windows offscreen mode set `QT_QPA_FONTDIR=C:\Windows\Fonts` explicitly; this remains layout evidence rather than native desktop acceptance.

#### Change-based selection in Actions

On pull requests, adding `render` requests mapped representatives; adding `render-all` immediately requests every state independently of `render`. Subsequent updates run while either label is present. Unlabelled PRs do not render automatically. `render_changes.py` compares the head with its merge base. Exact component rules in `render_coverage.json` select named state lists; unmatched component scopes use the page ownership rules in `render_dependencies.json` to choose small page packs. Unknown pages or missing history use the approved eight-state shared pack, never a silent exhaustive fallback. Sampling and known fixture gaps emit prominent **coverage incomplete** warnings in console output and the Action summary. Invalid planner metadata fails the job instead of silently rendering everything.

The first implementation maps shared selector, weighted LoRA editor, export hosts, usage, feedback and activity files. Broad main-window/style changes use the shared pack until function/UI-object/QSS scope mapping is implemented. Page packs currently start from existing fast states and still need owner review; these samples are not exhaustive coverage. States are deduplicated in catalogue order, and the ordinary renderer retains their fixture prerequisites. Packs are named state lists, not another speed level: necessary mapped states may be outside `-f`. Docs-only/non-render changes retain the existing `-f` startup smoke. Explicit category dispatch keeps the fast default; manual all-category dispatch without `changed_only` remains exhaustive. `python tools/ui/render_changes.py --base origin/main --head HEAD` only reports selection and does not render. Update both mapping files when source ownership or representative coverage changes.

#### Run levels and parallel workers

`render_states.py` takes a run level: `-f` (default, about 17 key states, around 25s on a 2-core Windows PC), `-m` (common variants, about 245 states) and `-s` (every state, about 456; this is what the Actions workflow runs). Explicit selectors (`--category`, `--group`, `--prefix`, `--state`) reach states of any level. `-s` now means `--slow`; `--state` has no short form. `--list` prints the selection without starting Qt.

The launcher imports no Qt and starts up to `-j N` worker processes (default one per core, 2 to 4; `-j 1` renders in one process). Each worker owns window groups: `workspace`, `gallery`, `main` (generation flow, queue, shell, inpaint, logs), `scripts`, `images-windows` and `splash`. Qt allows one application per process, so a worker costs about 5 to 7s of startup. The groups are packed into the fewest-slowest split from the measured `WORKER_COST` table in `render_catalog.py`; retune it when a group changes size.

The console shows one line per state with overall done/total, elapsed time, an ETA and the category count; `-v` adds diagnostics. The full worker output goes to `render-details-<key>.log` next to the manifests. The launcher merges the worker manifests into `render-manifest-<key>.json` (per-state `worker`, per-worker `workers` block, `wait_timeouts` and `wait_timed_out` for fixture waits that timed out and rendered as they stood). It fails on a non-zero worker, blank image, missing selected state or a state rendered twice; Ctrl+C kills the workers. The Actions "Summarize renders" step reads the merged manifest.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_QPA_FONTDIR = 'C:\Windows\Fonts'
uv run --frozen python tools/ui/render_states.py --out tmp/ui-renders/pr102-final
```

The [remaining-state implementation plan](../../devdocs/plans/2026-10-17-remaining-render-states.md) records the original 115-state baseline, missing workflow states, reserved filenames and safe offline fixture seams. Connection 01–39, Generate 20–40, Queues 11–23 and Images 53–64 are source-present (200 listed states in total). The latest thirty cover asset receipt/error/reconciliation presentation, active/problem/settled queue tables and planners, the shared Images export column, and Detail metadata/focus/missing/edited/provenance/LoRA/native-zoom states plus empty filtered results. These fixtures reuse one isolated shell, synthetic credentials/read views/results and mocked picker selections; service coroutines are closed before execution. Queue controls never dispatch or commit consent. Gallery pixels/metadata are in-memory presentation results, not export/removal authority, and preference-save scheduling is suppressed for that synthetic projection. The owner reports Actions passed through `c24f7c5`; the latest thirty additions await execution. Checked entries mean source-present fixtures, not independently verified screenshots or native Windows/DPI acceptance; unchecked entries remain planned.

Startup follow-up #168/#169 adds `connection-41-startup-check-and-loading`, `connection-42-startup-unconfigured` and `connection-43-startup-summary-unavailable` through real startup/presentation handlers with intercepted coroutine admission. These definitions submit no provider or credential work and have not been rendered. Lazy Logs creation reuses the existing Logs fixtures; first-use navigation is covered separately by the Qt shell tests.

`connection-44-recheck-after-context-change` covers a fresh pending check after configuration changes, including rejection of the old check's late failure without enabling the current Check button. Coroutine admission is intercepted; this definition has not been rendered.

Renders are for human review and carry no layout assertions (since PR #225); do not add `assert` or similar checks to `render_states.py`. Only save failures, duplicate state names and blank images fail a run, and a timed-out wait warns and renders the current state.

`render_states.py` saves listed offline workflow states as `<tab>-<NN>-<state>.png`, for example `images-01-grid.png`, `generate-01-empty.png` and `queues-02-created-paused.png`. The groups are `connection`, `generate`, `images`, `inpaint`, `queues` and `shell`; numbering is independent within each group. Keep existing names stable and append new numbers within the relevant group, so filenames sort by group without global numbering collisions. Render order may differ from filename order.

The separate `shots.py` / `design_shots.py` screen captures retain their matched `10-connection` / `11-generate` / `12-queues` / `13-images` names; `states.py` / `design_states.py` retain their component-sheet names. These are separate capture namespaces, not references to the workflow-state IDs. Use separate output directories and do not treat leftover screenshots from an earlier naming scheme as current evidence.

Notes
- Both scripts import `tests/support` (network guard, fake vault, fake cloud) from the repo, so they must run inside a checkout. They contact no provider.
- Hover, pressed and focus are forced with real Qt mouse and keyboard events on a fresh widget per cell; the sheet cells are drawn at 2x.
- On Windows the real Segoe UI is used; the screenshots sent earlier were rendered on Linux with a fallback font, so metrics differ.
- Not covered: menu-item hover/selected, tab hover, horizontal scrollbar, section-card toggle, gallery card/list-row hover, image-preview toolbar states.

## Design-system equivalents (headless browser)

`design_shots.py` captures the exported HTML kits; `design_states.py` renders the export's real compiled components in state matrices. Neither edits Qt or the export. These are capture tools, not a parity score or a claim of exhaustive coverage. Kit fixture/default differences are preserved, not secretly patched.

Setup (Playwright is in the locked dev dependency group):

```powershell
uv sync
uv run playwright install chromium
```

For the updated external export:

```powershell
$ds = 'C:/Users/dje/Downloads/DTFlow Design System (5)'
# --fetch explicitly downloads the export's pinned React/ReactDOM/Babel scripts,
# verifies SHA-384 integrity, and caches them in ignored tmp/design-system-vendor.
uv run python tools/ui/design_shots.py tmp/ds-renders/screens --design-system $ds --fetch
uv run python tools/ui/states.py tmp/qt-renders/states
uv run python tools/ui/design_states.py tmp/ds-renders/states --design-system $ds --reference tmp/qt-renders/states/contract.json
# Optional subset, matching states.py family names:
uv run python tools/ui/design_states.py tmp/ds-renders/buttons buttons --design-system $ds
```

Omit `--design-system` to capture the tracked `devdocs/design-system` reference. Omit `--fetch` for subsequent runs: browser requests outside the local capture server are blocked; pinned vendor URLs are fulfilled from verified local bytes. No provider requests, credentials, generation or uploads are involved. CDN setup and browser installation do use the network. A compatible existing Chromium may be selected explicitly with `-b / --browser <exe>`; its version and override path are recorded, and this is not acceptance of every Chromium version.

Outputs:
- Screens use `10-connection`, `11-generate`, `12-queues`, `13-images`, and the gallery filenames used by the Qt captures; sizes are 1600x1000, 1280x900, and 1280x1000 for expanded Detail. No title bar is fabricated.
- State families emit sheets `30` through `39`, at DPR 1 with cells enlarged 2x, like the Qt sheets. Specimen-host dimensions are not overrides of component appearance. The Qt script emits contract.json; the web script consumes it (default tmp/qt-renders/states/contract.json). Both use the same fixed frames, row/state order, 8 px specimen inset and sheet compositor. Unsupported states retain labelled slots. No component is stretched to match another; frame overflow fails clearly. The original ZIP sheets predate this contract and must be regenerated for alignment.
- `manifest.json` records the export bundle hash, browser version, captures, computed styles/bounds and observed interaction states for matrix cells, unsupported states, blocked requests and errors. Do not use stale PNGs from a prior run as current evidence; the manifest lists only the current captures.
- A capture/setup/JavaScript/resource error returns nonzero. Unsupported states are recorded separately, not rendered with invented styling. Native scrollbar hover/press, missing component disabled APIs, indeterminate progress and elevated numeric styling are examples. A run without failures is not full state parity.

The existing review/scenarios.json is not assumed complete or authoritative: its screenshot paths can be missing, and its fixtures differ from shots.py. Matching content and states, token-role correctness, pixel-diff alignment and full workflow scenarios remain separate follow-ups.

## Comparison gallery

Generate a portable offline gallery with shared zoom and linked scrolling. For matched states, use the fresh Qt captures rather than the original ZIP. Screen fixtures remain different; aligned state frames do not imply visual parity.

```powershell
uv run python tools/ui/compare_gallery.py --qt tmp/qt-renders --design tmp/ds-renders --out tmp/render-gallery/index.html
```

In the gallery, choose Specimen for one matching component/state. This control is enabled only for identical Qt/web state contracts; it clips the original PNGs with CSS at the shared scale, without rewriting images.

### Icon coverage and image readiness

The gallery state sheet uses canonical Qt-form buttons and the same icon initializer as the application. Buttons remain mounted in the canonical page while their own pixels are captured, preserving ancestor-scoped typography and layout constraints. It covers Grid/List/Detail, Additional details/Raw metadata JSON, Refresh/Fullscreen, both round-arrow directions and thumbnail states. Web recipes use the export's Icon component with its kit icon sizes and local asset paths; native size and disabled-state differences remain visible. Screens and states wait for all mounted images to load and decode after interactions, fail broken assets, and record decoded-image counts. Expanded Detail's metadata icons are therefore captured only once ready. Round buttons use a nonzero-height positioned wrapper, and component bounds must fit inside the screenshot host.

**Startup correction (October 19):** the first page-1 click may be dispatched while the pager is not ready. The Grid-only benchmark therefore requires a measured `Visible image range` starting at image **#1** before its downward leg, retrying the page-1 control for at most 3 seconds. An unconfirmed position (such as #281) skips the journey and records a warning; it can never win the console ranking. This is a bounded *starting-position* prerequisite, not a wait for all image thumbnails or for subsequent batch queries. Once image #1 is verified, wheel input continues independently of batch loading.

**Delayed page-1 click fix:** Qt can queue a numbered-chip click while a Grid mount or adjacent batch is in progress. An already-visible first card (#1) and a successful UIA `click_input()` are not proof the queued jump has run; it may execute later and pull the scroll back. Before timed scrolling, the runner now snapshots `-x` `gallery_perf phase=page_jump page=1` events, clicks the enabled chip once, and waits for a **new page_jump event** plus two successive first-visible-#1 checks. It fails the trial if the application does not process that click before the startup deadline. The independent wheel sender is unchanged once startup is verified.

**Startup polling correction (2026-10-19):** the benchmark polls for the enabled numbered page-1 control roughly every 200 ms after the initial loading spinner; it clicks the control once as soon as available. It then checks first-visible image #1 and starts the timed wheel journey. The prior requirement for a new diagnostic `page_jump` log event was removed because it could block startup despite a usable UI. A retry is bounded by the existing startup `--timeout`. This is not a wait for every thumbnail or subsequent batch.

## Queue template preview definitions

Queue template definitions `queues-90` through `queues-102` cover loaded/modified recipes, Save name, searchable library, Update/Delete consent, Rename, dirty replacement, backend rebinding, reconstruction review, missing dependencies, empty/error states and a narrow library. They reuse the canonical composer/library forms and shared feedback with synthetic entries and intercepted workers: no template writes, queues, provider calls or generation. These definitions have not been rendered as part of PR #167. Windows offscreen font setup remains documented in the [development guide](../../devdocs/guides/development.md); captures alone are not native/DPI acceptance.

## Logs delivery and follow-ups

The canonical render script defines the native Logs empty/populated/detail/error, paging, retained-selection and whole-millisecond timing states. Numeric `*_ms` chips use display-only rounding; raw records stay exact. The Windows startup fix passes the offscreen screen configuration without a drive colon and applies the Windows font-directory default even with platform arguments. Owner reports that the command works; CI rendering was skipped at runtime head `b2fdf1d`, so do not claim a complete render run from that feedback. [Delivery audit](../../devdocs/plans/2026-10-19-structured-log-viewer.md); remaining implementation [#106](https://github.com/djebaz/dtflow/issues/106), acceptance [#107](https://github.com/djebaz/dtflow/issues/107).

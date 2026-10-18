# Ordinary gallery responsiveness: measured experiment

2026-10-18 · PR #92 · base main `7cf49c4` (merged PR #89).

## Scope and implementation

Prioritise an ordinary 100-image page replacement and appending the next 100,
not a 1,000-card stress workload. Missing Qt card, table-cell and filmstrip
objects are prepared privately in approximately 4 ms GUI-thread slices. The
committed page stays usable until preparation completes. Retained identities
keep their widgets, selection and thumbnail state. Generation checks cancel
stale preparation; rejected results dispose of their private objects. Old card
subtrees are retired four per event-loop turn to avoid a mass DeferredDelete
pause. Ordinary table append/replacement uses bulk row counts. Hidden-table
selection updates avoid unnecessary visual-rectangle calculations.

All Qt widgets remain on the GUI thread. Paging (100), committed residency
(300), query limits (200), six decoder slots and live/snapshot source checks
remain in the existing controller. The new preparer is a presentation phase,
not durable output authority. Offline synchronous rendering remains available.

The full suite exposed a delayed viewer-save callback accessing a deleted
date filter after shutdown. Shutdown now stops owned presentation timers;
save scheduling and delivery also check the runner lifecycle before reading
controls. A regression exercises this boundary.

## Local measurements

These measurements describe the original preparation experiment, before the
geometry/codec follow-ups below; they are not a final-head frame-time guarantee.

Linux virtual X11, Qt/PySide 6.12, Python 3.13.16, 1600×1000, scale 1.0.
Ten repetitions per transition, synthetic records and predecoded 256×192
pixmaps; no DB, file IO, providers or inference. Values are milliseconds.

The following comparison uses this implementation's synchronous fixture path
versus its prepared path, not an unchanged-main comparison. An earlier native
main baseline separately measured about 108 ms synchronous append and 110 ms
page replacement, with median GUI gaps of 144–147 ms.

| View / transition | Synchronous commit median | Prepared commit median | GUI gap median / maximum | Prepared ready median |
|---|---:|---:|---:|---:|
| Grid, 100→200 | 112.2 | 9.5 | 35.4 / 44.0 | 213.7 |
| Grid, 200→next 100 | 121.8 | 20.1 | 27.5 / 77.6 | 228.6 |
| Grid, 100→other 100 | 111.0 | 12.8 | 26.1 / 27.1 | 221.3 |
| List, 100→200 | 140.7 | 12.5 | 24.1 / 29.2 | 250.3 |
| List, 200→next 100 | 159.6 | 21.0 | 34.0 / 53.4 | 259.9 |
| List, 100→other 100 | 136.1 | 14.7 | 27.1 / 32.0 | 251.2 |

A queued callback at preparation start arrived within 4 ms in these runs,
instead of waiting for the whole synchronous commit. This measures event-loop
availability, not physical input latency. The 4 ms heartbeat is sampled using
QTest, and includes preparation, commit and 80 ms of following layout/paint/
deletion work. Host load and the test event pump affect these measurements.

A subsequent sequential Grid/List repeat, without other local tests running,
still showed variable timings: prepared commits were 10.7–27.2 ms median,
ready times 255–312 ms median, and the worst List replacement GUI gap reached
118 ms. This reinforces that the experiment has not removed every stall;
the earlier table must not be treated as a latency ceiling.

Tradeoff: preparation extends total readiness to roughly 214–312 ms median,
while leaving the old page usable. The local objectives of every final commit
below 16 ms and ordinary GUI gaps below 33 ms are **not yet met**. Do not call
this a complete elimination of stalls or promote the PR on these numbers alone.

Reproduce both views with:

```sh
uv run --frozen python tools/ui/benchmark_gallery_transitions.py --view Grid --repeats 10 --out tmp/gallery-grid
uv run --frozen python tools/ui/benchmark_gallery_transitions.py --view List --repeats 10 --out tmp/gallery-list
```

Set `QT_QPA_PLATFORM=xcb` and a valid `DISPLAY` for native Linux; the tool defaults
to offscreen. Its `results.json` contains per-transition samples and summaries.
This microbenchmark excludes source reads and decode contention; it cannot
replace a real application smoke test.

## Automated guarantees versus manual acceptance

| Evidence | What it covers | What it does not establish |
|---|---|---|
| Merged #89 CI: 612 passed, 3 skipped; Ruff | Existing tested data, navigation and lifecycle contracts | Skipped cases; native input, display-driver or smoothness acceptance |
| Merged #89: 230 offscreen UI states | Fixture presentation and assertions | Interactive behavior on a physical desktop |
| #92 regressions | Old 100-card page remains usable during append; retained widget and checked ID survive; latest refresh wins; preparation and preference-save shutdown safety | Universal frame-time limits or physical input latency |
| #92: 232 locally rendered states | Existing assertions plus preparation/200-card presentation | Native Windows/DPI, long scrolling or a stall-free Detail transition |
| #92 local full suite: 616 passed, 3 skipped; Ruff passed | Locked-environment regression and lint checks on the candidate | Remote CI result or native acceptance |
| Native Linux measurements above | Reduced blocking in ordinary presentation transitions | IO contention, Windows packaging, trackpad behavior or zero outliers |

The broader native sequence still intermittently stalls after entering Detail;
a later isolated sequence passed. Root cause remains unproven (issue #88).
Do not describe the paging fix as closing it. A List-downward smoke assertion
also assumed a resident page jump must reset the mounted-window offset; that
assumption is invalid when page 2 is already buffered and is not evidence of
a backwards-fetch regression.

## Short native validation protocol

1. Record exact head, OS, Qt version, display scale, input device, cache state
   and Auto-refresh setting. Use 200–300 offline saved images with distinct IDs.
2. In Grid then List, switch 100-image pages back and forth ten times, then
   append 100→200 by scrolling down. Scroll upward across image 101→100.
   **Pass:** input stays responsive, correct order/direction, no blank page,
   no duplicate IDs, and the visible anchor is preserved.
3. Change thumbnail/row size at the boundary and resize the window. Check an
   image, switch page/view and return; open Detail, step 100→101→100, then return.
   **Pass:** checked identity persists; Detail matches the selected image and
   restores the browsing location; no stall, selection jump or clipped control.
4. Repeat page switches and 100→200 with Auto-refresh enabled for three polls;
   continue ordinary scrolling for five minutes. **Pass:** no flash, reset,
   accumulating delay or unexpected backward query. Capture GUI-gap timings
   alongside query/decode/prepare/commit phases and retain a clip for any stall.
5. Repeat on the packaged Windows build at 100%, 150% and 200% DPI with mouse
   and trackpad. Keep any unrun row pending. An offline capture or green CI does
   not substitute for these native acceptance steps.

At the initial checkpoint PR #92 was draft pending latency-outlier work, the
intermittent Detail stall and native owner acceptance. No provider calls or real
output mutations were needed. Its initial draft-PR workflows were skipped;
the initial 616/3 and 232-state results above were local, not CI.

### Detail-chevron visual follow-up

The circular Previous/Next buttons now use centered SVG paths rather than
font glyphs. Two named fixtures cover both enabled buttons and Previous disabled
at the first image; they check painted glyph bounds against the button center
and exercise both navigation actions. Focused renders and Ruff pass.

A full-render repeat during this follow-up stopped at the existing 300-card
eviction assertion: image 250 was lost from the viewport (321–336 visible).
An isolated copy of the unchanged pre-chevron head `25c0e60` reproduced the
same failure. Those focused captures were not a passing full 234-state run.

### Actions render and interaction follow-up

Inspected [Actions run 39123329604](https://github.com/djebaz/dtflow/actions/runs/39123329604)
and downloaded its `ui-renders-pr92-8c9dd67` artifact. It failed after 107
captures on the early List-prepend anchor assertion. The Grid eviction,
List 100/101 and Detail chevron captures were also visually inspected; file-size
badges were visibly clipped against the right edge.

- **Grid geometry:** a small asynchronous-read regression reproduced the
  anchor shift. Prepared cards were still explicitly hidden when layout/anchor
  geometry was calculated; Qt's later show changed row sizes. Explicitly show
  cards as they enter the layout. The retained anchor/widget survives eviction
  with the same viewport offset. Deferred restores now have a QObject context
  and reject shutdown or superseded query generations.
- **List fixture timing:** waiting for data/preparation completion did not
  necessarily wait for the queued anchor restore. Fixtures now include that
  final phase before capturing or moving to the next scenario. A named early
  List-prefetch capture retains image 113 at the top.
- **Buffered image I/O:** a native stack probe observed GUI PNG writing waiting
  on Qt's image mutex while a worker was in buffered image reading. A regression
  then proved that the Python-created QBuffer could call a Python virtual read
  from inside the codec. QDataStream now only owns a native C++ raw-byte buffer;
  no data-stream deserialization occurs. This removes that Python callback/GIL
  re-entry mechanism, retains owner lifetime through decode and preserves the
  original-file lease, 600 px thumbnail scaling and original-size Detail path.
  Do not treat it as universal native/Windows stall acceptance for issue #88.
- **Badges:** right alignment uses the measured text width. Narrow previews
  show one elided dimensions badge instead of overlapping/clipping two labels;
  full metadata remains in the presentation model. Paint regressions cover both
  ordinary and narrow widths.
- **Deterministic fixtures/checks:** synthetic removal results disable real
  five-second polling while their callbacks are driven explicitly. Auto-refresh
  and snapshot behavior keep their earlier dedicated cases. The countdown test
  checks its synchronous initial value before yielding to Qt; the cloud GUI
  test receives a controlled missing-credentials service result, leaving real
  credential/TLS behavior to lower-layer tests.

The final full local suite passed **620 tests, 3 skipped**. The full local
renderer passed **238 states in 146.8 seconds**, including new
`images-99` through `images-102`. The new anchor, badge, buffered-decode and
shutdown regressions cover the corrected paths. Ruff passes. Final CI results
must be checked against the pushed head; local captures remain layout evidence,
not physical input or Windows/DPI acceptance. A short post-geometry microprobe
still showed ordinary GUI-gap outliers, so the original <16/<33 ms goals remain
open. No provider calls, inference, original mutations or billable operations
were required.

### Resumed Grid append and diagnostic follow-up

The pending-session Grid experiment was reconstructed on the PR branch instead
of treating unsaved checkout changes as committed. For an ordinary 100→200
append with the same column count and retained prefix, the grid layout now
visits only the 100 new cells. Full reflow remains the conservative path when
columns or the mounted identity order change, including page replacement,
backward prepend and 300-card eviction. A presentation regression checks
retained and appended cell positions after the asynchronous layout settles.

Development-only `-x` instrumentation records Qt timer gaps of at least 50 ms
(sampled every 20 ms), environment DPI, Auto-refresh skip reasons, bounded
query stages, worker-to-Qt callback delivery wait, preparation totals, and
Grid cell counts. Logs contain aggregate counts, durations and lifecycle
flags; they intentionally omit output identities, prompts and paths.
See `devdocs/guides/development.md` for event names and interpretation.

The previous session's reported Grid reflow reduction (approximately
160→26 ms on its local test case) has **not** been independently remeasured
in this resumed environment. CI, offline renderer and native Windows input
acceptance are distinct from that report. No provider generation, inference,
original-output mutation or billable action was involved in this follow-up.

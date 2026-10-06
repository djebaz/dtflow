# Design-system capture tooling


`design_shots.py` captures the exported HTML kits; `design_states.py` renders the
export's real compiled components in state matrices. Neither edits Qt or the
export. These are capture tools, not a parity score or a claim of exhaustive
coverage. Kit fixture/default differences are preserved, not secretly patched.

Setup (Playwright is in the locked dev dependency group):

```powershell
uv sync
uv run playwright install chromium
```

For the updated external export:

```powershell
$ds = 'C:/Users/USER/Downloads/DTFlow Design System (5)'
# --fetch explicitly downloads the export's pinned React/ReactDOM/Babel scripts,
# verifies SHA-384 integrity, and caches them in ignored tmp/design-system-vendor.
uv run python tools/ui/design_shots.py tmp/ds-renders/screens --design-system $ds --fetch
uv run python tools/ui/states.py tmp/qt-renders/states
uv run python tools/ui/design_states.py tmp/ds-renders/states --design-system $ds --reference tmp/qt-renders/states/contract.json
# Optional subset, matching states.py family names:
uv run python tools/ui/design_states.py tmp/ds-renders/buttons buttons --design-system $ds
```

Omit `--design-system` to capture the tracked `devdocs/design-system` reference.
Omit `--fetch` for subsequent runs: browser requests outside the local capture
server are blocked; pinned vendor URLs are fulfilled from verified local bytes.
No provider requests, credentials, generation or uploads are involved. CDN setup
and browser installation do use the network. A compatible existing Chromium may
be selected explicitly with `-b / --browser <exe>`; its version and override path
are recorded, and this is not acceptance of every Chromium version.

Outputs:
- Screens use `10-connection`, `11-generate`, `12-queues`, `13-images`, and the
  gallery filenames used by the Qt captures; sizes are 1600x1000, 1280x900, and
  1280x1000 for expanded Detail. No title bar is fabricated.
- State families emit sheets `30` through `39`, at DPR 1 with cells enlarged 2x,
  like the Qt sheets. Specimen-host dimensions are not overrides of component
  appearance. Regenerated Qt states emit contract.json, consumed by the web renderer: both use identical fixed frames, 8 px specimen insets, row/state ordering and a common compositor. Unsupported slots are labelled without shifting columns. Component sizes are preserved; overflow fails, rather than silently cropping/stretching. The original ZIP does not use this contract.
- `manifest.json` records the export bundle hash, browser version, captures,
  computed styles/bounds and observed interaction states for matrix cells,
  unsupported states, blocked requests and errors. Do not use stale PNGs from a
  prior run as current evidence; the manifest lists only the current captures.
- A capture/setup/JavaScript/resource error returns nonzero. Unsupported states
  are recorded separately, not rendered with invented styling. Native scrollbar
  hover/press, missing component disabled APIs, indeterminate progress and elevated
  numeric styling are examples. A run without failures is not full state parity.

The existing review/scenarios.json is not assumed complete or authoritative:
its screenshot paths can be missing, and its fixtures differ from shots.py.
Matching content and states, token-role correctness, pixel-diff alignment and
full workflow scenarios remain separate follow-ups.


## Earlier development capture (2026-10-06, before matched state frames)

Against the external `DTFlow Design System (5)` export: eight screen captures,
ten state sheets / 158 captured cells, 18 explicit unsupported states, and zero
capture/JavaScript/resource errors. Browser: existing Chromium 149.0.7827.55 via
an explicit executable override; the Playwright-matched browser installation hit
C: disk-space limits and was stopped. This is not matched-browser, high-DPI or
pixel-parity acceptance. Three focused offline vendor-cache contract tests,
Ruff, compilation and both CLI help commands passed. No full application suite,
packaging, model inference, real upload or billable action was run.

## Matched state frames (2026-10-06)

Fresh Windows offscreen Qt captures produced ten sheets at DPR 1 and logical DPI 96. The web capture consumed their contract and produced the same ten sheet dimensions, with 146 captured cells, 26 explicitly unsupported slots and zero capture/JavaScript/resource errors. All ten dimension pairs were checked. Unsupported slots include isolated popups without equivalent recipes; they are not silently replaced with screenshots of different components.

The regenerated tmp/render-gallery/index.html uses these fresh states on both sides. Its screens retain the supplied ZIP captures, with a provenance warning. Choose Specimen to inspect a single matching component/state without resampling the source PNG; Whole sheet remains available. Six focused offline tests, repository Ruff checks/format, compilation, CLI help and headless gallery navigation/zoom/alignment/specimen checks passed. No full application suite or packaged/high-DPI acceptance was run. No inference, upload or billable action ran.

## Icon capture correction and coverage (2026-10-06)

Expanded Detail's six web metadata icons were omitted by a capture race after opening Additional details. Capture now waits for rendered root content, the final disclosure state, and image load/decode; broken images fail clearly. The export is unchanged. All six metadata icons were checked decoded and visually inspected in the regenerated screenshot. A local headless check also verified valid SVG decode and broken-asset rejection.

Gallery states now use canonical Qt-form buttons and the application's shared icon initializer: Grid/List/Detail, both disclosure buttons, Refresh/Fullscreen, both round directions and thumbnail states. Web recipes use real exported Icon components and native kit icon sizes; 47 icon-button states each had a decoded icon. Both round directions are captured completely at their native 42 px web diameter, including disabled. Their previous clipping was a zero-height absolute-positioning host error, not a design-system defect.

The complete regenerated run produced ten matched Qt/web sheets, 184 web cells, 25 explicitly unsupported slots, eight web screens, and zero capture/JavaScript/resource errors. The expanded gallery-button sheet is 4418 by 3272 px on both sides, with 64 selectable specimens. Seven focused tests, repository Ruff/format checks, compilation, CLI help and headless gallery/asset checks passed. Original Qt ZIP screens still include standalone gallery-page previews rather than full-shell Grid/List/Detail captures; this separate scope mismatch is not fixed here. No full application or packaging acceptance, inference, upload or billable action ran.

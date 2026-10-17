# DTFlow UI screenshot scripts

Offscreen Qt renders of the real DTFlow forms under the real `dtflow_dark.qss`. Run from the repository root (or set `DTFLOW_ROOT`) with the locked environment:

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

## Workflow-state filenames

The [remaining-state implementation plan](../../devdocs/plans/2026-10-17-remaining-render-states.md) records the current 115-state baseline, missing workflow states, reserved filenames and safe offline fixture seams. Its unchecked entries are planned coverage, not existing screenshots.

`render_states.py` saves listed offline workflow states as `<tab>-<NN>-<state>.png`, for example `images-01-grid.png`, `generate-01-empty.png` and `queues-02-created-paused.png`. The groups are `generate`, `images`, `inpaint`, `queues` and `shell`; numbering is independent within each group. Keep existing names stable and append new numbers within the relevant group, so filenames sort by group without global numbering collisions. Render order may differ from filename order.

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

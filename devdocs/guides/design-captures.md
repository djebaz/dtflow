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
$ds = 'C:/Users/dje/Downloads/DTFlow Design System (5)'
# --fetch explicitly downloads the export's pinned React/ReactDOM/Babel scripts,
# verifies SHA-384 integrity, and caches them in ignored tmp/design-system-vendor.
uv run python tools/ui/design_shots.py tmp/ds-renders/screens --design-system $ds --fetch
uv run python tools/ui/design_states.py tmp/ds-renders/states --design-system $ds
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
  appearance. Sheet dimensions/row coverage need not equal the Qt matrices.
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


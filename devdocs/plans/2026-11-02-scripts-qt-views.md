# Scripts Qt view contract (mockup ZIP authority)

This UI-only slice implements presentation fixtures for Generate > Scripts using the supplied **DTFlow Design System (8).zip**, especially `ui_kits/scripts/scripts-design.md`, `ScriptsScreen.jsx`, `ScriptForm.jsx`, `ScriptEditor.jsx`, and `scripts.css`.

The ZIP is authoritative for interaction and visual states. Older issue #118 descriptions are historical context where they differ. The implementation must still follow repository `AGENTS.md`, `DESIGN.md`, tracked Qt Designer forms, shared QSS, and runtime tokens.

## Scope of this PR

- Three Generate sub-modes: Text to image, Inpaint and Scripts (not a new top-level navigation tab).
- Script library at left; Inspect / Configure / Code / Run / Results in the primary work area; compatibility, inherited settings, source and history in the right rail.
- Reuse shared section cards, semantic status colors, inline confirmations and the activity overlay; scripts never own a second generator or cancellation service.
- Editor-only VS2019 Dark palette is allowed as a scoped exception. Common control tokens remain authoritative.
- Offline, explicitly illustrative Qt states in `tools/ui/render_states.py`, including empty, supported, unsupported, partial, form, editor, running, waiting, queued, stopped, failed, interrupted, unresolved, results, source handoff and narrow/drawer layouts.
- No backend dispatch, script evaluation, cloud calls or downloads in UI fixtures. Connect controllers and services only in later implementation slices.

## Mockup-resolved decisions

- The script's code and versions are immutable once saved; draft editing creates a new version.
- Form discovery is static and conservative; dynamic forms appear when the run actually asks.
- Source selection offers no source, Images, local file and Inpaint handoff, with immutable snapshot at start.
- Shared admission queues scripts behind the backend owner; Cancel while waiting is distinct from Stop after submission.
- Run history belongs to Scripts; child attempts link to Images/Queues.
- The start-bar cap is **8** in the mockup, while the current headless CLI default remains **4**. Do not silently change backend CLI behavior; expose the cap explicitly when wiring the UI.
- Photo/file-picker compatibility must be checked against newest epic decisions before enabling those hooks; static UI may illustrate a disabled state.

## Verification boundary

Offline renders are layout evidence only, not Windows DPI acceptance or working scripting. No automated tests are added or executed in the first implementation pass by owner direction.

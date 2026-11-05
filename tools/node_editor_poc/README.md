# Node-editor POC (#223, audit 10.8 step 6)

Isolated proof of concept. Nothing here is imported by `src/dtflow`. There is no executor, provider, model, or generation code, and no tests.

- `nodeeditor_app.py`: the candidate, nodeeditor-qtpy 0.8.2 on pyside6-essentials 6.12.0. It covers five reference nodes, IMAGE/MASK branching, drag/connect with typed-port and cycle rejection, pan/zoom, undo/redo (including inspector edits), a fixture thumbnail, the selection-linked inspector, DTFlow tokens and QSS, and ComfyUI-format open/save.
- `baseline_app.py`: an Essentials-only reference with the same shell, fixture, and packaging. It's a viewer only, with no connecting and no undo.
- `poc_contract.py`: a headless catalogue plus a strict all-or-nothing ComfyUI v1.0 import/export. `widgets_values` are written as arrays (D1).
- `reference_workflow_v1.json`: a POC stand-in for the owner's reference file, using placeholder model ids.
- `build.py`: PyInstaller one-file and one-folder builds with identical settings, plus sizes, timings, and a manifest.

## Run from source (pwsh 7.5+, repository root)

```pwsh
uv run --isolated --no-project --python 3.13 --with pyside6-essentials==6.12.0 --with shiboken6==6.12.0 --with nodeeditor-qtpy==0.8.2 --with qtpy==2.4.3 --with orjson==3.13.0 python tools/node_editor_poc/nodeeditor_app.py
uv run --isolated --no-project --python 3.13 --with pyside6-essentials==6.12.0 --with shiboken6==6.12.0 python tools/node_editor_poc/baseline_app.py
```

## Build and measure the Windows EXEs (baseline first, so the candidate manifest records the deltas)

```pwsh
uv run --isolated --no-project --python 3.13 --with pyside6-essentials==6.12.0 --with shiboken6==6.12.0 --with pyinstaller==6.22.3 python tools/node_editor_poc/build.py --candidate baseline --measure 5
uv run --isolated --no-project --python 3.13 --with pyside6-essentials==6.12.0 --with shiboken6==6.12.0 --with nodeeditor-qtpy==0.8.2 --with qtpy==2.4.3 --with orjson==3.13.0 --with pyinstaller==6.22.3 python tools/node_editor_poc/build.py --candidate nodeeditor --measure 5
Get-ChildItem dist/node-editor-poc/onefile
Get-Content dist/node-editor-poc/nodeeditor-manifest.json
```

The outputs are in `dist/node-editor-poc/onefile/*.exe` (the size to ship) and `onedir/` (the extracted size). The manifest records sizes, the packages in the build environment, packages added compared with the baseline, and spawn-to-first-frame timings. That timestamp is the first event-loop callback after `show()`, not proof of visible painting or a controlled cold start. Omit `--measure` to build only, without launching either EXE.

## Windows owner smoke (2026-11-05)

Built locally with managed Python 3.13.13, PyInstaller 6.22.3 and the pinned packages above. The owner launched the candidate one-file EXE, approved the initial visual appearance, then reported that plain-wheel zoom did not work and middle-button-only panning was awkward. Inspection found that the library requires Ctrl + wheel and keeps a separate zoom counter that `fitInView` does not update.

The POC now uses a view subclass (no dependency patch) for plain-wheel zoom based on the actual transform and left-drag panning on empty canvas. Shift + drag retains rectangle selection; node/port gestures remain delegated to the library. The owner approved the rebuilt navigation EXE. No unit tests were added or run, and the navigation rebuild did not launch the EXE automatically.

The navigation one-file EXE is 40,723,733 bytes versus 37,689,664 bytes for the baseline (+3,034,069 bytes, decimal MB: 40.72 versus 37.69). These are local artifacts, not release assets. The original candidate build's automated timing launch exited with status 1, so no successful Windows candidate timing series is claimed; it was not rerun after the owner requested build-only. A navigation onedir comparison, controlled cold-start measurement, clean-machine launch without Python installed, DPI/font matrix, and full rejection/undo/save checklist remain unverified on Windows. Owner visual/navigation approval is not acceptance of those separate checks.

## POC friction follow-up (2026-11-05; pending owner smoke)

- Port names now render beside the socket, using DTFlow font/colour tokens and type tooltips, instead of upstream's text clipped inside the tiny connector. Labels follow the socket's visibility at low zoom.
- Invalid drag completion cancels the provisional edge immediately and keeps the actionable status message, without upstream's `NOT VALID EDGE` print. It does not replace an existing valid input link on rejection.
- Selection/deselection callbacks still update the inspector, but their history stamps are omitted. Real edits remain undoable; a selection change breaks parameter-edit coalescing. Empty-background navigation also notifies the inspector when selection clears.
- Imported positive link IDs and `state.lastLinkId` are preserved. New links get monotonically allocated IDs, retained in undo snapshots; deletion/undo does not rewind the allocator and export no longer renumbers surviving links. The file adapter rejects duplicate or nonpositive IDs.
- Grid debug-print avoidance and type-colour refresh remain in place; no installed dependency is patched.
- Explicit `--measure` runs now retain JSON reports and console logs beside each EXE under `smoke-reports/<run>/`, including failures/timeouts. Smoke-mode startup/timer exceptions write an error report and request a failing exit rather than losing the traceback in a windowed EXE. This improves diagnosis only: the previous exit-1 cause remains unknown until another owner-authorized smoke run captures it. No EXE launch or unit tests were performed for this follow-up.

The size and owner approval above refer to the earlier navigation build, not to these additional changes. Repeat the manual checklist before accepting the follow-up; build success and static checks are not interaction acceptance.

## Design-system alignment plan

`DESIGN.md`, `tokens.json` and `dtflow_dark.qss` remain authoritative. Applying the stylesheet is not sufficient: the canvas paints its own items, while some shell widgets currently use generic or mismatched roles. Scope is visual/interaction alignment of this isolated POC, not a new production workspace or executor.

| Area | Concrete target | Touchpoints |
| --- | --- | --- |
| Canvas grid | **Implemented, awaiting visual review:** existing `color.canvas` background; minor `color.itemSurface` lines at 25% opacity, major `color.panelEdge` lines at 30%, both 1-unit pens. Keep the library's existing zoom-dependent grid suppression. No global palette change. | `_apply_canvas_theme` |
| Node hierarchy | Map titles to `type.body` + `type.weightEmphasis`, port/evidence labels to `type.small`, summaries to body/secondary. Use `color.sectionHeader`, `color.surface`, `size.panelRadius`, and a quiet `color.border`; keep focus distinct from ordinary selection. Audit the actual rendered title item, not only a font field. | `DTFGraphicsNode`, `DTFNodeContent`, port labels |
| Graph states | Specify neutral/hover/selected/keyboard-focus node and edge states. Reserve amber/red for warnings/failures, not MASK taxonomy; retain square MASK versus round IMAGE sockets, explicit names/type tooltips and neutral/blue type cues. Keep status words, not colour alone, for refused links. | Node/socket/edge palette and view |
| Inspector and controls | Match existing section-card/header roles rather than using a 17pt workspace title for every selected node. Use 12px panel padding, 8px gaps, `size.controlContent` 22px minimum content height, shared select/editor/focus/disabled states and a right rail that scrolls without horizontal overflow. Placeholder checkpoint/LoRA controls remain clearly illustrative. | `Inspector`, LoRA rows, shared QSS selectors |
| Toolbar and feedback | Group File / Edit / View / Add actions, reuse existing SVG icons, give icon controls tooltips/accessibility names, and apply shared action roles. Replace modal POC warnings and inline status styles with a window-owned feedback seam using canonical feedback roles, without importing execution/services or inventing progress. | `_build_actions`, `show_status`, open/save error presentation |
| Responsive acceptance | Review default/narrow layouts, long node/checkpoint names, dense ports, 100/150/200% Windows scaling, high/low zoom and all interaction states. Keep thumbnails aspect-fit and the canvas dominant. Check graph/save behavior after presentation changes. | Manual owner review, source/packaged builds |

Order: (1) approve the quieter grid; (2) align node type/surfaces/states; (3) align inspector/toolbar/feedback; (4) rebuild only and let the owner run the visual/interaction checklist. Keep this work in POC adapters and existing token/QSS roles; promote a missing shared role only when needed for production, with the DESIGN token snapshot updated together. Static Designer migration belongs to the production-integration boundary, not a rewrite of this disposable prototype. No unit tests, automatic EXE launches, production palette changes or full application integration are authorized by this plan. Remaining rows are planned, not implemented or accepted.

## Manual smoke checklist

1. Launch the source app, then the one-file EXE on a PC without Python on PATH. Record the EXE size, extracted size and first-launch time.
2. Drag empty canvas with the left mouse button to pan; drag nodes to move them and ports to connect them. Shift + left-drag selects a rectangle. Zoom with the wheel (no Ctrl required), including after pressing F to fit the view. Middle-button panning remains available but is not required.
3. Check readable port names/type tooltips. Drag a MASK output onto an IMAGE input, including an already-connected input. The status bar should say "Connection refused", the provisional edge should disappear, and any prior input link should remain. A link that would close a cycle should also be refused.
4. Select a node and check that the inspector follows. Edit steps or prompt, select other nodes, then use Ctrl+Z / Ctrl+Y on the canvas. The value should revert and come back without stepping through selection-only changes. Click empty canvas and check that the inspector clears.
5. Add a node, delete a link, then undo and redo.
6. Save as, reopen, and compare the JSON. The only differences should be your edits. Open a valid fixture with nonconsecutive link IDs, add/delete/undo links, save/reopen and verify that surviving IDs stay unchanged and new IDs exceed the saved high-water mark.
7. Theme: canvas, node, socket, edge and inspector colours should all come from DTFlow tokens.

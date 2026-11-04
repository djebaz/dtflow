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

The outputs are in `dist/node-editor-poc/onefile/*.exe` (the size to ship) and `onedir/` (the extracted size). The manifest records sizes, the packages in the build environment, packages added compared with the baseline, and spawn-to-first-frame timings. The first run is the coldest.

## Manual smoke checklist

1. Launch the source app, then the one-file EXE on a PC without Python on PATH. Record the EXE size, extracted size and first-launch time.
2. Pan with the middle mouse button and zoom with the wheel. Press F to fit the view.
3. Drag a MASK output onto an IMAGE input. The status bar should say "Connection refused". A link that would close a cycle should also be refused.
4. Select a node and check that the inspector follows. Edit steps or prompt, then use Ctrl+Z / Ctrl+Y on the canvas. The value should revert and come back.
5. Add a node, delete a link, then undo and redo.
6. Save as, reopen, and compare the JSON. The only differences should be your edits.
7. Theme: canvas, node, socket, edge and inspector colours should all come from DTFlow tokens.

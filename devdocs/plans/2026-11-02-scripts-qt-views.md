# Scripts Qt integration — reference-first correction (PR #201)

**Status: UI implementation REJECTED / not review-ready.** The initial standalone Designer scaffold and placeholder render fixtures are not visually faithful to the owner-approved mockups. Do not merge or count these renders as accepted. This document records the corrected implementation gate, not proof of completed work.

## Source of truth (priority order)

1. Owner-provided `Downloads.zip`: 25 full-size screenshots of the `ui_kits/scripts/index.html` mockup (24 distinct named states; screenshot positions 3 and 14 repeat `scripts_imported_supported` and `scripts_interrupted`, respectively).
2. Owner-provided `DTFlow Design System (8).zip`: `ui_kits/scripts/index.html`, `ScriptsScreen.jsx`, `ScriptForm.jsx`, `ScriptEditor.jsx`, `data.js`, `scripts.css` and shared shell/components. `data.js` defines the precise state name-to-data mapping.
3. Updated and tracked `devdocs/design-system/scripts-design.md`: *Owner decisions (resolved, round 1–5)* and final *Shared overlay appearance (correction)* supersede earlier draft proposals in that document.
4. Canonical runtime constraints from `AGENTS.md`, `DESIGN.md`, Qt Designer forms, `tokens.json`, and shared QSS. Issue #118 is historical context, not a substitute for the supplied visuals.

## Exact 24-state parity matrix

All captures must be of the actual Qt screen, using the existing shell, at the corresponding layout size. The ZIP's browser mockup-state picker is not part of the application screenshot. Screen number refers to sorted ZIP file order, with the unnumbered PNG first.

| Screenshot | Qt state name | Required visual content |
| --- | --- | --- |
| 0 | `scripts_empty` | Full app shell, empty library, large black canvas/DTFlow logo, empty inspector |
| 1, 3 | `scripts_imported_supported` | Library card rows, full Inspect summary and facts, hook statuses, settings/source/history, disabled Start |
| 2 | `scripts_imported_unsupported` | Error card, unsupported hook details, disabled Start |
| 4 | `scripts_inspect_partial` | Yellow preflight card, acknowledgement, right inspector |
| 5 | `scripts_form_default` | Two-column full form with all upstream types, sections, controls and Start bar |
| 6 | `scripts_form_long_values` | Long labels, emoji, paths, wrap/ellipsis, scroll and tooltips |
| 7 | `scripts_queued` | Queued run with no committed result, queued activity strip and Cancel |
| 8 | `scripts_cancelled_before_start` | Cancelled without submission, durable status |
| 9 | `scripts_running` | Committed image placeholder, run text, console and shared activity strip |
| 10 | `scripts_waiting_input` | Parked live form, Continue/Cancel input, console, shared Stop |
| 11 | `scripts_stopped` | Stopped locally; no inference of remote cancellation |
| 12 | `scripts_failed` | Persistent red error overlay, durable details |
| 13–14 | `scripts_interrupted` | Temporary amber overlay, durable details |
| 15 | `scripts_form_after_unknown` | Previous run unresolved warning, new distinct Start |
| 16 | `scripts_results` | Ordered ScriptRun child-result rows and Open in Images |
| 17 | `scripts_from_inpaint` | Immutable source/mask handoff chip and Detach |
| 18 | `scripts_editor_new` | Empty versionless editor, toolbar and new draft controls |
| 19 | `scripts_editor_dirty` | Monospace syntax colors/line numbers, toolbar, unsaved banner, new-version action |
| 20 | `scripts_editor_syntax_error` | Highlighted error line, line/column diagnostic and Go to line |
| 21 | `scripts_editor_run_active` | Run-active warning, immutable saved version, editable separate draft |
| 22 | `scripts_editor_narrow` | Narrow code editor with readable toolbar and no side inspector |
| 23 | `scripts_narrow` | 800×680 layout: genuine shell, one-column form, inspector hidden |
| 24 | `scripts_narrow_details` | Right-hand details **drawer overlay**, Close control and form behind |

## Non-negotiable structure

- Render the actual DTFlow app shell (brand/power status, Generate/Queues/Images navigation, shared status bar and activity strip). Do **not** build or screenshot an unrelated standalone panel.
- Scripts is the third joined `Text to image | Inpaint | Scripts` sub-mode within Generate, not a new top-level tab. Switching mode does not stop an active script.
- Left script library has two-line rows with filename + SHA/tier/support, and New script / Import / Reload controls. Centre has header with compatibility ladder (Inspect/Configure/Code only), matched card layouts, Start bar and overlays. Right inspector is the existing 272px rail and becomes the right drawer at narrow width.
- The form must map **all 14 upstream component types** with their data and native control semantics, not three placeholder fields. Static inspection only. Dynamic forms are requested during a real run. No arbitrary script HTML.
- The editor must have line numbers, syntax highlighting, Undo/Redo, Find, font size, diagnostics, Discard and Save as new version. Keep the editor palette scoped; reused UI elements take shared tokens.
- The run view needs a committed result, parked-input form, console, distinct queued/cancelled/running/waiting/stopped/failed/interrupted/unknown states, immutable run/version labels and linked child results. Stop/Cancel belongs only to the shared activity bar and notifications use the actual shared overlay widget.
- UI-only preview fixtures must not execute scripts, submit work, contact any backend, upload to cloud, or claim actual run outcomes.

## Existing PR #201 shortcomings — must be repaired before review

- **Implemented since initial rejection:** `scripts_workspace.ui` now supplies three Designer-owned rails mounted inside the real `MainWindow` shell; Generate includes the third Scripts sub-mode. `scripts_page.py`, `scripts_form.py` and `scripts_editor.py` provide native library, inspection, configured form, code editor, run/result presentation and 14-component control inventory. The offline renderer now captures these widgets and uses the shared activity strip; narrow details uses a top-level Qt overlay drawer.
- **Still incomplete:** live source/version storage, actual parser-driven compatibility and forms, functional Start/Stop/admission and durable history bindings, interactive handoff, and native interactive/accessibility/DPI acceptance. The reference examples appear only in offline fixtures.
- **2026-11-02 correction pass:** [review corrections](2026-11-02-scripts-review-fixes.md) records the Designer migration, shared side-rail surfaces/borders/icons, dirty-draft protection, queue/editor/drawer fixes and the owner-authorized offline Qt render. All 24 final PNGs in `tmp/ui-renders-scripts-codex` were visually inspected; this is layout evidence, not owner approval or interactive Windows acceptance.

## Review/verification sequence (not automatic unit tests)

1. Integrate the real Generate third submode and shared shell rails, while leaving the scripting backend disconnected.
2. Implement one normal desktop state and **capture it from actual Qt**, compare to the matching screenshot. Fix the major layout/tokens before expanding.
3. Implement form, editor, run/results and inspector variants, then capture all 24 states at corresponding dimensions.
4. Review screenshot pairs and log measurable differences/clipping, labels, controls, colors and accessibility. Mockup screenshot parity is a visual review; web rasterization need not be pixel-identical to native Qt.
5. Only after owner visual approval wire services and active interactions. No new automated unit tests by owner instruction.

The existing workflow `--category scripts` hook is infrastructure only. Its presence is **not** evidence that renders match this matrix. No live generation or billable operation is authorized for UI validation.

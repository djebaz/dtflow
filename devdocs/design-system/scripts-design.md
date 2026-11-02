# Generate › Scripts — design proposal (issue #118, part of #112)

Mockup: `ui_kits/scripts/index.html` (state picker at the top; `?state=<name>` deep-links). Design only: nothing here executes script code.

## Proposed DESIGN.md section

The initial proposal below is historical where it conflicts with the resolved owner decisions and corrections later on this page. Those resolved sections are the implementation contract.

**Scripts mode.** Scripts is the third joined sub-mode of Generate (Text to image | Inpaint | Scripts), a row of `mode` buttons at the top of the centre column, 6 px outer corners, 1 px overlap. No top-level tab. Switching mode never cancels, pauses or resumes a running script; the activity bar keeps showing it on every tab.

**Layout.** Left rail: shell chrome, then *Script library* (SectionCard: list, Import…, Reload from disk, storage caption). Centre: sub-mode row, script header, view, start bar. Right inspector (272 px, same toggle as the other tabs): Compatibility, Starting settings, Source, History. The inspector is hidden at 800 × 680 with nothing relocated (as Queues does below 1100 px); the hook table stays reachable from the header status.

**Script header.** File name (ellipsis, full name in tooltip), short SHA in mono (full SHA-256 in tooltip) and one line of coloured text labels: `Syntax valid`, `API calls observed`, then exactly one verdict: `Preflight incomplete` (warning), `Ready for explicit run` (success) or `Not runnable · unsupported hooks` (error). Colour is text only and always paired with words.

**Views.** `inspect` (summary card + Inspection facts + Configure…), `form` (declarative component list), `run` (latest committed result, state line, parked-input form, console), `results` (one ScriptRun and its ordered child attempts).

**Form rendering.** The UI maps the 14 upstream component types to DTFlow controls and never shows script HTML. Two columns (label 120–190 px, control fills); at narrow widths one column with the label above. Labels clamp at two lines with the full text in a tooltip; values truncate (directories truncate at the start, so the folder name stays visible); script emoji and punctuation are content and shown as given.

| Upstream type | DTFlow control |
| --- | --- |
| `section` | 11 pt semibold heading with section-border underline |
| `plainText`, `markdown` | small secondary text; markdown limited to bold and code |
| `textField` | TextField |
| `comboBox`, `menu` | ComboBox |
| `segmented` | joined `mode` buttons |
| `switch` | native switch with On / Off word |
| `slider` | slider + mono value: percent `75%`, fractional `5.5`, scale `×2` |
| `doubleSlider` | two-thumb slider + mono `8–28` |
| `size` | two SpinBoxes × (multiples of 64) |
| `imageField` | path box + native Choose… / Clear |
| `directory` | path box + native Choose… |
| `image` | 56 px preview tile + caption (read-only) |

**Start bar.** One panel under the view: Generation cap (default 8), a cloud and source-image disclosure with the local Compute Units estimate (never billing), backend/queue line, then **Start script** (primary, 120 px min). Start is a single explicit click, disabled and relabelled "Starting…" until the run begins (double-click guard). Preflight incomplete requires an inline acknowledgement checkbox. Unsupported scripts show the reason in place of the disclosure. While running or waiting, the bar becomes Stop script (danger, local-stop caption).

**Run states** (text label colours follow the Items table: running none, waiting warning, stopped warning, failed and interrupted error, outcome unknown neutral gray). The shared activity bar shows running, waiting ("Waiting for your input") and the routine ending "Stopped locally". Failed, interrupted and outcome-unknown stay in the bottom overlay and in the centre state line. Outcome unknown has no retry affordance: only "Check Queues" and "Review and start again", because the paid attempt may exist.

**requestFromUser.** A mid-run call parks the run and inserts a SectionCard in the centre titled with the script's own title, holding the rendered controls and the script's confirm label; Cancel answers with no value. The run state line reads "Waiting for your input · nothing is generating". Repeated calls reuse the same card.

**Canvas scripts.** In the Inpaint toolbar a *Run script on this image* button switches to Scripts with an immutable copy of image and mask (a chip at the top of the form with Detach). JS geometry changes stay virtual until a generation.

## State names for `tools/ui/render_states.py`

| State | What it shows |
| --- | --- |
| `scripts_empty` | no library, empty centre |
| `scripts_imported_supported` | Wildcard prompt sweep, inspect view, Ready for explicit run |
| `scripts_imported_unsupported` | Outpaint ring, error card, Start disabled |
| `scripts_inspect_partial` | Hires fix loop, Preflight incomplete, acknowledgement |
| `scripts_form_default` | all 14 types, default values |
| `scripts_form_long_values` | long labels with emoji, long paths and options |
| `scripts_running` | committed result, console, elapsed counting, activity bar |
| `scripts_waiting_input` | parked run with inline form |
| `scripts_stopped` | stopped locally, activity bar result |
| `scripts_failed` | bottom overlay + state line |
| `scripts_interrupted` | bottom overlay + state line |
| `scripts_outcome_unknown` | proposed extra: unknown outcome, no retry |
| `scripts_results` | ScriptRun with child attempts, history |
| `scripts_from_inpaint` | proposed extra: handoff chip |
| `scripts_narrow` | 800 × 680, one-column form, no inspector |

The two proposed extras are not in the issue's list; drop them if not wanted.

## Owner questions: recommendations
2. **Canvas entry:** both (Inpaint handoff and a source picker in Scripts); the mockup shows the handoff only.
3. **Same backend queue:** shared admission, serialized dispatch. The start bar says so. Refusing Start would hide a paid run's availability.
4. **History:** Scripts history only, with child attempts linked into Images and Queues ("Created by script X, step N"). No queue entries.
5. **Persistent user-file area:** no; per-run sandbox only.
6. **loadLayerFromPhotos / Files:** unsupported at first; the hook table marks them `unsupported` and the script cannot start.

## Not designed here
Import errors (file too large, not UTF-8), the library full state (50 versions), a cloud-credit-exhausted block, and the Qt sizes for the Stop and Start buttons beyond the kit's 32 px height.

## Revision after UX review (corrections applied)
- **Narrow details:** at 800 × 680 the inspector is a right-hand drawer opened by the Scripts toolbar's labelled Inspector toggle (toggle state = drawer visible), with a Close button; the form stays behind it.
- **Compatibility vs run:** the header shows compatibility (Syntax valid, API calls observed, verdict) only in Inspect / Configure / Code. Run and results views show the run identity and status instead. "Ready for explicit run" is now "No blocking issues found" (not a promise of success).
- **Unresolved outcomes:** "Review and start again" after *outcome unknown* or *interrupted* carries a warning card into the form (previous run #5, may be billed, a new run is separate work), keeps the run in History as unresolved, and relabels the button "Start new run".
- **Stop:** only the shared activity bar owns Stop; the Scripts footer carries a caption. Cancel input answers the script with no value and the run continues.
- **Waiting wording:** "Waiting for your input · the script is paused. A generation already submitted may still complete."
- **Feedback:** failure uses the persistent red overlay; interrupted and outcome unknown use the six-second amber warning row, with × and Escape. Durable status stays in the state line and History after transient feedback expires.
- **Identity:** runs are "Run #N" with a separate "version <sha>"; steps read "Created by script X, run #N, step M".
- **Accessibility:** every control has a programmatic label (Width, Height, minimum, maximum, switch name, Choose <field>, Open run N step M in Images); long labels wrap; hook names wrap; paths expand on hover or focus.
- **Estimates:** the disclosure names the settings the estimate describes, says later calls can differ, and shows backend as unchecked while the connection is unknown.
- **Creator / editor:** library gets New script…; the centre has an Inspect | Configure | Code switch. The editor is a real code area (syntax highlighting, line numbers, undo/redo, Find, font size, line/column diagnostics with Go to line), "Draft · unsaved changes", Discard (inline confirm) and Save as new version (immutable SHA, re-inspected, runs stay pinned). Check syntax is parse-only. New states: `scripts_editor_new`, `scripts_editor_dirty`, `scripts_editor_syntax_error`, `scripts_editor_run_active`, `scripts_editor_narrow`, plus `scripts_form_after_unknown` and `scripts_narrow_details`.
- **Form discovery:** static inspection only; no script evaluation or discovery dry-run. Dynamic `requestFromUser` forms appear only when an explicitly started run asks.
- **Owner decisions:** resolved in the following section; the earlier questions are historical.
- **Not done:** real fixture images (placeholders remain).

## Owner decisions (resolved, round 1–5)
1. **Canvas entry:** both. Inpaint handoff plus a **Source image** row at the top of Configure (No source / Images gallery… / Local file… / Inpaint canvas). Optional unless the script header says required (then Start is disabled with the reason in red text). The source is an immutable copy taken at Start; changing it needs a new run.
2. **Backend busy:** shared admission, serialized dispatch. Start stays enabled; the run shows **Queued behind N items** (nothing submitted, nothing charged). The activity bar shows `Queued behind 3 items`, a separate `Waiting 0:42` counter, and **Cancel** instead of Stop script. Elapsed starts at admission.
3. **Cancel while queued:** free; the run stays in History as *Cancelled before start* and the activity bar shows a stopped-style result.
4. **History:** Scripts history only, children linked into Images and Queues.
5. **Files:** per-run sandbox only.
6. **Photos / Files pickers:** unsupported first; the hook is marked unsupported and Start is disabled.
7. **Form discovery:** static inspection only, nothing is executed. Configure shows the source, starting settings, cap and (when statically known) the script's controls; dynamic forms appear when the run asks (requestFromUser).
New states: `scripts_queued`, `scripts_cancelled_before_start`.

## Editor palette (editor only)
VS2019 Dark from the project's javascript.js theme: background #1E1E1E, text #E1E1E1, keywords #E67DDC, types and API globals #65C9FE, strings #F09B87, numbers #32EB87, comments #57A64A, operators #C0C0C0, selection #264F78, Consolas. Surrounding controls, diagnostics (error line tint, red text) and status feedback keep DTFlow tokens.

## Shared overlay appearance (correction)
Scripts notices reuse the shared overlay exactly (dtflow_dark.qss 1655–1810): state-tinted gradient (warning `warningSurface` → surface, error `dangerSurface`, success `connectionSurface`), 1 px state border, 48 px minimum, padding 7 / 8, 10 px gap, 28 × 28 symbol tile (`radiusChip`, strong weight), neutral `title` colour at emphasis weight, secondary small body, 22 px × dismiss, 2 px lifetime line. Interrupted and unknown outcome use the amber warning treatment and expire after 6 s (lifetime line, hover/focus pause, 450 ms fade); only a real failure is red and persistent. Outcomes stay inspectable in the run view and History. Version text is not part of the notice. Tokens `color-warning-surface` (#423525) and `color-warning-glyph-surface` (#61441d) were added to the design system.

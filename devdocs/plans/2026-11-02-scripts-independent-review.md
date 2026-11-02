# PR #201: independent UI review alignment

## Approved scope
- Complete offline interactions only: fixture Start/queue/cancel/input/terminal/review transitions, fixture-only syntax diagnostics and in-memory version save. No parser, execution, durable library, backend admission, generation or provider calls.
- Add the Inpaint entry and a detached source/mask snapshot into Scripts for offline inspection only.
- Native Photos/Files pickers are supported in the target design; do not misrepresent the runtime hooks as implemented. Keep a genuine unsupported-hook example for the blocked state.
- Retain native shared overlays, 1100px breakpoint, shared rail widths, readable editor size and icon-only Undo/Redo. Fix local inconsistencies rather than copy HTML pixels.

## Checklist
- [x] Add terminal actions and appropriate footers; preserve unresolved run context on review and show live/prior run history and library cues.
- [x] Demonstrate guarded Start, queue/cancel/input flows and fixture-only editor Check/Save states with explicit offline disclosure and no fabricated parser evidence.
- [x] Add the Inpaint button, immutable offline image/mask handoff and render coverage for the entry and captured source.
- [x] Correct waiting form, activity details, semantic status, disclosure, source-required styling and compatibility target wording.
- [x] Polish form alignment/joined controls, slider geometry, result headings/canvas, library/history presentation and source metadata readability.
- [x] Polish editor footer, dirty/version identity, diagnostics and minimum toolbar geometry while retaining native font size and shared consent.
- [x] Extend offline renders, run the owner-authorized Scripts render and inspect changed states; run Ruff/static checks only, no tests.
- [x] Reconcile design/status/release docs and findings; commit/push to the same draft PR and verify the remote head.

## Evidence boundaries
- Independent review baseline: `e2f5086`; current implementation baseline: `d9d07f5`. Inspector toggle placement was already corrected in `41c3ddd`.
- Runtime integration, real result sizing, owner visual approval, native DPI/keyboard acceptance and packaged/provider validation remain separate. Disabled real service integration is intentional, not an instruction to implement it here.
- No automated tests added, changed or run. No billable work, uploads, script evaluation, model downloads or packaging.

## Finding dispositions
- Run/results: terminal navigation, review without submission, unresolved history, live library/history cues, semantic statuses, waiting form/help, console bounds, scaled illustration and activity detail are implemented as offline fixtures. Gallery links remain disabled without real output identities; Check Queues and previous-run inspection use shared informational notices, not backend navigation.
- Start/editor: readiness covers support, required source, preflight acknowledgement and live-run exclusion. Explicit fixture progression demonstrates queued cancellation, input cancellation/continuation and completion. Preview versions stay in memory, use SHA-256 and never invoke a parser; arbitrary edited text remains unverified with Save disabled. Dirty import/draft replacement stays protected.
- Layout: joined form choices, aligned headings, expanding size/range controls, primary Configure, source-required coloring, card/metadata/history readability, result titles, editor footer/diagnostics and toolbar geometry corrected. The Inpaint entry snapshots immutable source content and detached raw mask bytes without changing the original editor.
- Intentional native differences: shared overlays and uncertainty colors, 1100px breakpoint, shared rail widths, 12pt editor font, icon-only Undo/Redo and native slider styling remain. Directory scrolling keeps the final folder visible. Fixture identity prefixes are not expanded into invented full hashes; new in-memory versions have actual computed hashes. Size controls remain UI bounds, not backend compatibility promises.
- Target Photos/Files pickers are supported in the design and labelled as not runtime-wired in compatibility details. `console.repl` supplies the unsupported example. No dry-run wording or fixed per-call billing estimate restored.
- Static checks: changed-file Ruff lint/format, Python compilation and six Designer XML/unique-name/stretch/UTF-8-LF inspections passed. No tests added, modified or run. No provider, generation, upload, model download or packaging.
- Render iterations: the 31-state initial pass completed in 36.8s; the expanded 35-state pass completed in 125.6s and the 36-state correction pass in 39.7s. Existing 30-second diagnostic stack dumps occurred, but these runs exited successfully. All 35-state captures were reviewed in contact sheets, with full-size form/run/editor checks. Final render results and remote commit are recorded in the PR/handoff. Screenshots are layout evidence only, not native interaction/DPI or owner approval.
- Final render: 37 states in 36.3s, exit 0, in `tmp/ui-renders-scripts-codex`; the existing 30-second diagnostic emitted one stack dump. Final partial-ready, previous-run inspection and capped completion captures were inspected full-size; the preceding pass covered all other state families. No render failures. Owner visual/native acceptance remains pending.

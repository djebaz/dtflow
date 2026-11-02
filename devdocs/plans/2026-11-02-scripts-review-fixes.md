# Plan: PR #201 Scripts UI review corrections

## Scope
- In: address every finding in `tmp/script_ui_review.md` on `feature/scripts-qt-views`, retaining native shared overlays and the VS2019 Dark editor palette.
- Out: runtime execution/admission, provider calls, durable library/history integration, packaging, tests and smoke tests. The UI remains explicitly preview-only.

## Action items
- [x] Move static library, workspace, inspector/drawer and editor layouts into canonical Designer forms; retain handwritten dynamic script fields and result/hook rows.
- [x] Keep library actions inside the available rail and put overflow in the list/form, not clipped outer panels. Correct wrapped-label and control height negotiation.
- [x] Match both side rails' shared section-card structure: QGroupBox surface/border, separate sectionHeader, existing SVG icon, panelHeading typography and sectionContents; verify the library's list/background and footer as well as the right rail/drawer.
- [x] Separate queued/cancelled fixture consoles and timestamps; show Cancel and a waiting clock through the shared activity bar.
- [x] Bind inspection facts, source metadata and compatibility rows to the selected fixture script.
- [x] Distinguish saved inspection from draft diagnostics; remove Start from Code views; implement literal Find/Replace/count/Next and Ctrl+F.
- [x] Protect dirty drafts on New/Import/library replacement and use shared inline discard consent.
- [x] Synchronize the Scripts toolbar Inspector toggle with the desktop rail and narrow drawer; wrap hook names, correct history newlines, and add separate source/mask offline thumbnails and coverage summary.
- [x] Extend existing offline render states to expose corrected controls, Find/Replace, and long-value geometry without executing scripts; run the owner-authorized `uv run python tools/ui/render_states.py --category scripts --out tmp/ui-renders-scripts-codex` and inspect all 24 PNGs after corrections.
- [x] Synchronize design/status/release documentation and record permitted static checks, commit and push to the same draft PR.

## Decisions and design changes
- 2026-11-02: reviewed source baseline is `035da337`; supplied `ui-renders-scripts11` PNGs are pre-fix evidence, not acceptance of these corrections.
- 2026-11-02: native shared overlays take precedence over any obsolete mockup styling. Existing script/runtime safety boundaries remain unchanged.
- 2026-11-02: source/mask thumbnails are deterministic offline illustrations, explicitly labelled as fixtures; no user image or inference is required.
- 2026-11-02: the owner explicitly authorized offline Qt rendering after the fixes; this supersedes the initial no-new-screenshots boundary, not the no-tests/no-provider boundary.

## Open questions
- None blocking. Fresh offline renders have been inspected; owner visual approval and native interactive/DPI acceptance remain separate.

## Validation
- [x] Repository readiness, Ruff formatting/lint, Python compilation and Designer XML/structural inspection only.
- [x] Final diff reviewed and corrections committed/pushed to draft PR #201: `e2f5086` (shared cards/icons) and `41c3ddd` (Inspector toggle). Remote PR head matched `41c3ddd2c4ed40106f5895f959df41c5a3fdf0fc` and the working tree was clean before this documentation-only reconciliation.
- Tests, smoke tests, interactive app launches, model/provider calls and billable actions are not run. Offline screenshot rendering is authorized; it establishes layout evidence only, not interactive/DPI acceptance.

## Results
- Main correction-pass offline render: 24 states in 17.6 seconds, exit 0, under `tmp/ui-renders-scripts-codex`. Reviewed every state using six contact sheets, with full-size inspection of the queued and unresolved-review layouts; earlier iterations also received full-size inspection of the form, source, inspection and side-rail corrections. The toggle follow-up subsequently refreshed all 24 PNGs in 18.6 seconds, as recorded below. Generated PNGs and contact sheets remain ignored.
- Both side rails now use canonical QGroupBox section-card surfaces/borders, section headers, existing SVG icons and transparent contents. The library list uses the shared background token; New/Import/Reload and storage caption remain visible at desktop and narrow widths. Editor toolbar groups reuse native segmented styling.
- Final readiness: Python 3.13.13 / SQLite 3.53.1. Ruff lint, Ruff format check (seven files), Python compilation (seven files), five Designer XML/name/encoding checks and `git diff --check` passed. Side-rail structural inspection confirms one library card and four inspector cards with icons. No test files changed and no tests ran.
- An intermediate render exceeded the existing 30-second diagnostic threshold but completed successfully; the final run completed in 17.6 seconds without that diagnostic. One initial structural assertion incorrectly treated center QFrame sections as side-rail cards; the corrected scope checks the actual QGroupBox side rails.
- Existing concurrently created/pushed commit `a633f9b` was preserved. Remaining detailed surface/icon and metadata corrections are a separate follow-up; no history rewrite or merge.
- Runtime Start, parser-driven inspection, saved library/history, service admission and real source handoff remain disconnected. Offline image/mask illustrations and result placeholders are not real output-sizing acceptance. Static/visual checks do not establish keyboard interaction, dirty-consent behavior, native DPI, provider behavior or owner approval.

## Follow-up: inspector toggle placement
- Owner feedback: the grid-like Unicode glyph beside the product logo was ambiguous and suggested global scope. Move the control to the right edge of the Scripts sub-mode toolbar, beside the region it controls; use a labelled Inspector button and a right-sidebar outline SVG. Preserve synchronized checked state, desktop rail and narrow drawer behavior.
- [x] Update canonical Designer ownership and remove the old shell-header control/references.
- [x] Add a right-sidebar SVG, Inspector label and shared-token selected styling at the right edge of the Scripts toolbar.
- [x] Extend existing desktop/narrow render diagnostics and rerender all 24 offline states.
- [x] Inspect desktop, narrow closed and narrow open control placement and state.
- [x] Commit/push the focused follow-up as `41c3ddd`; verify the remote PR head and retain draft status.
- Completed: 24 offline states rerendered in 18.6 seconds (exit 0). Full-size desktop, narrow closed and narrow open captures inspected; the labelled SVG control stays visible above the drawer. Render diagnostics confirm modeRow ownership, non-null icon and checked states true/false/true. Ruff lint/format, readiness, Designer/SVG XML and diff whitespace checks passed. No tests or provider calls.

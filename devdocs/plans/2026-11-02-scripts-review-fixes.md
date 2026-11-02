# Plan: PR #201 Scripts UI review corrections

## Scope
- In: address every finding in `tmp/script_ui_review.md` on `feature/scripts-qt-views`, retaining native shared overlays and the VS2019 Dark editor palette.
- Out: runtime execution/admission, provider calls, durable library/history integration, packaging, tests and smoke tests. The UI remains explicitly preview-only.

## Action items
- [ ] Move static library, workspace, inspector/drawer and editor layouts into canonical Designer forms; retain handwritten dynamic script fields and result/hook rows.
- [ ] Keep library actions inside the available rail and put overflow in the list/form, not clipped outer panels. Correct wrapped-label and control height negotiation.
- [ ] Separate queued/cancelled fixture consoles and timestamps; show Cancel and a waiting clock through the shared activity bar.
- [ ] Bind inspection facts, source metadata and compatibility rows to the selected fixture script.
- [ ] Distinguish saved inspection from draft diagnostics; remove Start from Code views; implement literal Find/Replace/count/Next and Ctrl+F.
- [ ] Protect dirty drafts on New/Import/library replacement and use shared inline discard consent.
- [ ] Synchronize the existing shell inspector toggle with the narrow drawer; wrap hook names, correct history newlines, and add separate source/mask offline thumbnails and coverage summary.
- [ ] Extend existing offline render states to expose corrected controls, Find/Replace, and long-value geometry without executing scripts; run the owner-authorized `uv run python tools/ui/render_states.py --category scripts --out tmp/ui-renders-scripts-codex` and inspect all 24 PNGs after corrections.
- [ ] Synchronize design/status/release documentation and record permitted static checks, commit and push to the same draft PR.

## Decisions and design changes
- 2026-11-02: reviewed source baseline is `035da337`; supplied `ui-renders-scripts11` PNGs are pre-fix evidence, not acceptance of these corrections.
- 2026-11-02: native shared overlays take precedence over any obsolete mockup styling. Existing script/runtime safety boundaries remain unchanged.
- 2026-11-02: source/mask thumbnails are deterministic offline illustrations, explicitly labelled as fixtures; no user image or inference is required.
- 2026-11-02: the owner explicitly authorized offline Qt rendering after the fixes; this supersedes the initial no-new-screenshots boundary, not the no-tests/no-provider boundary.

## Open questions
- None blocking. Exact native geometry remains subject to the owner's next render and visual acceptance.

## Validation
- [ ] Repository readiness, Ruff formatting/lint, Python compilation and Designer XML/structural inspection only.
- [ ] Review final diff and clean working tree after commit/push; keep PR #201 draft.
- Tests, smoke tests, interactive app launches, model/provider calls and billable actions are not run. Offline screenshot rendering is authorized; it establishes layout evidence only, not interactive/DPI acceptance.

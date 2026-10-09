# Plan: Inline desktop actions

## Scope
- In: Inventory and replace DTFlow-owned confirmation, error, result and progress windows with main-window presentation; preserve safety/service boundaries.
- In: Prefer an action button that becomes Confirm, with cancellation in the separate details surface and contextual explanation.
- Out: Native file/folder pickers, release/version changes, provider behavior, new tests, agent tests or smoke tests.

## Action items
- [x] Inspect clean main, remote and open PR state; create feature/inline-actions.
- [x] Inventory popup call sites and review canonical UI/design and safety documentation.
- [x] Resolve owner UX choices before implementation.
- [x] Implement shared inline presentation and action confirmation with stale-scope invalidation.
- [x] Replace affected controller paths without weakening destructive/billable safeguards.
- [x] Synchronize design, desktop guide, repository contract, status and unreleased notes.
- [x] Perform minimal non-test static checks and review staged scope.
- [x] Commit/push implementation and update PR #58 to main; do not merge or release.

## Popup inventory
- main_window.py: preset replacement/import/export errors, cache busy/cleanup confirmation/failure, regeneration confirmation/busy, custom-LoRA clear/import failures, saved-key removal, close during recycling, About.
- queue_page.py: queue Cancel/Clear, uncertain-reservation acknowledgement, close during active dispatch.
- image_recycle_panel.py: busy/empty/error notices, frozen-scope Trash confirmation, progress/cancel window, outcome summary.
- lora_panel.py: searchable Add chooser, numeric weight entry, explicit cloud-upload confirmation.
- search_select.py: checkpoint/LoRA search selection and custom-identifier input.
- generation_page.py: saved metadata/error display, unclassified checkpoint list, preset-name input.
- gallery_page.py: asynchronous checked-selection progress window, replaced by inline Cancel selection and the shared activity overlay.
- Native QFileDialog open/save/folder paths remain unchanged.

## Decisions and Design Changes
- 2026-10-09 Owner requests no DTFlow popup-style dialogs; native file/folder pickers stay.
- 2026-10-09 Owner prefers the triggering button to mutate to confirmation so the pointer need not travel.
- 2026-10-09 No release, version bump or packaging for this enhancement PR.
- 2026-10-09 Keep frozen Trash plans, execution revalidation/leases/durable intents, cache coordination, queue uncertainty warnings and explicit upload/credit consent unchanged.

- 2026-10-09 Owner approved no timer and replacement of all application-owned dialogs, including selectors/inputs.
- 2026-10-09 Owner requested one-step LoRA identifier/weight entry, 0.0–3.0 with 0.1 steps; existing imported/saved requests are not silently clamped.
- 2026-10-09 Owner refined cancellation placement: only amber Confirm replaces the original button, preserving width; Cancel is elsewhere in the inline details panel, not adjacent in the action row. This supersedes the earlier nearby-Cancel proposal.
- 2026-10-09 Owner chose a bottom progress overlay above content, hidden when idle, rather than reserved idle space. Export, Trash, gallery selection and generation/queue progress share labelled rows; generation preview/Run controls remain. No notice dismissal cancels work.
- 2026-10-09 Ordinary menus remain menus; native window-close consent uses an inline Confirm close exception because the OS close button cannot mutate.
- 2026-10-09 Draft PR #58 opened early at plan checkpoint 84a9a92; implementation follow-up stays on feature/inline-actions.

- 2026-10-09 Owner final color refinement: Delete/Clear confirmations are red; other confirmations remain amber. Cancel remains separate.

## Open questions
- None.

## Validation
- [ ] Final tests and smoke tests deferred to user.
- Tests were not added or run. Smoke tests were not run. Final runtime/visual and packaged acceptance remains with the owner.
- Syntax/AST parsing, canonical XML parsing, module imports and theme token resolution passed without launching the application. Ruff check passed for src/dtflow/ui; format checks passed for all ten changed Python modules; git diff whitespace check passed. A source inventory found no remaining QMessageBox/QInputDialog/QProgressDialog/QDialog use in src/dtflow/ui.
- Packaging skipped: this is an enhancement PR, not an artifact/release task. Canonical forms use the existing packaged data collection; packaged acceptance is not claimed.
- No training/inference, cloud upload, actual Trash/cache mutation or potentially billable action ran.

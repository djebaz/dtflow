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


## Approved CI follow-up (2026-10-09)

Owner approved adapting existing tests and correcting shutdown, without popup restoration,
weakened consent or progress redesign. CI [run 37982792491](https://github.com/djebaz/dtflow/actions/runs/37982792491)
at source 314d927 reported 8 failed, 417 passed, 3 skipped and 1 error. Failures relied on
removed QMessageBox/chooser/confirmation hooks; queue teardown also polled a shut-down runner.

- [x] Adapt existing metadata, LoRA, upload, queue and saved-key contracts to the inline controls.
- [x] Extend existing lifecycle/close contracts for refused programmatic close, stopped polling
  and ignored queued results; no new test functions/files.
- [x] Expose runner closed/shutdown_started lifecycle; stop desktop timers and reject late UI
  delivery without changing worker cancellation/durable outcome settlement.
- [x] Programmatic close requests consent before runner shutdown; close consent is one-use only.
- [x] Synchronize lifecycle contract, desktop guide, status and unreleased notes.
- [x] Review/static checks and commit/push follow-up to PR #58; fresh CI tracked in the PR.

Tests were adapted with owner approval, not added or run locally. Smoke tests were not run.
Fresh CI, runtime/visual and packaged acceptance remain separate. No inference, training,
actual cloud upload, destructive live operations, billable action, packaging or release.
Disk exhaustion interrupted documentation writes; owner freed space before handoff resumed.

Follow-up static checks: Ruff lint/format passed for all eight changed Python files; AST and
module imports passed; existing test-function inventory unchanged; git diff whitespace passed.
No application launch or test execution. Fresh CI status is not presumed successful.

### CI teardown follow-up

- CI at `cc1d371`: 426 passed, 3 skipped, 40 teardown errors. Functional inline
  interactions passed, but the plain Python shutdown receiver outlived the Qt form.
- Move timer shutdown into a window-parented QObject with a decorated Qt slot.
  Window destruction removes its signal connection; do not suppress invalid-widget
  exceptions or restore dialogs. No new tests or local test/smoke execution.
- Static checks and automatic CI verification of the follow-up are pending.

- CI at `b6c15b9`: 426 passed, 3 skipped, one teardown error. The shutdown
  receiver errors are gone; the progress filter still outlived its frame.
- Parent the progress controller to its own frame, not the main window, and
  scope its deferred positioning callback to that QObject. This is lifecycle
  repair only, not an overlay redesign. Static lint/format/syntax/import checks
  passed; automatic CI verification pending. No new/local tests or smoke.

## Approved unified feedback overlay follow-up

The owner approved the updated `dtflow_inline_panels.html` mockup: one thin
bottom overlay for confirmations, progress, success, error, partial and stopped
results. Essential information is directly visible and wraps; no Details control,
no reserved layout space or main-content resizing. Confirm alone stays on the
original action (red for Delete/Clear); Cancel is in the overlay. Export starts
without additional consent. Successful operation results dismiss after six seconds,
paused during hover/keyboard focus; errors/partial/stopped outcomes persist.

- [x] Mount inline feedback/editors and concurrent progress in one Qt-owned overlay.
- [x] Match compact row hierarchy, thin progress/count/Stop and state palette.
- [x] Route Trash/export/cache/selection results to labelled overlay rows, retaining
  all original leases, eligibility, frozen consent and cancellation semantics.
- [ ] Synchronize docs and run static checks; no new tests or local tests/smoke.
- [ ] Push PR #58 and observe automatic CI to completion; owner native visual and
  packaged acceptance remain separate. No inference or billable operations.

Prototype markup/images/demo-only state controls are not production content.
Long metadata and selection editors must remain usable without opening popups.
Concurrent tasks retain separate rows in the same overlay rather than overwriting
one another or dismissing pending consent.

Approved HTML reference SHA-256:
`b05dffac0fd3b0d984f9ea47f9355f4f1ae3aa5bffe2e7e3f0a04fc11ec6461e`.
Only native Qt presentation/controllers are changed; no prototype images, demo
controls, simulated counts, provider behavior or service safety rules are copied.
The previous lifecycle CI at `6cf931c` passed (426 passed, 3 gated live skips).
The unified-overlay change requires its own CI and owner mounted-window visual
acceptance; no visual parity is claimed from source inspection.

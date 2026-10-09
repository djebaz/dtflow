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

## Add LoRA editor layout refinement (2026-10-09)
- Owner selected the existing Create queue primary-button treatment for Add LoRA.
- Put editor Cancel and Add below the identifier/weight fields, preserving the shared
  header consent buttons for non-editor flows. Other editors use neutral actions unless
  explicitly assigned a primary role. No new palette or tokens.

## Inline editor dimming refinement (2026-10-09)
- Owner approved a 50%-alpha charcoal overlay behind Add LoRA while editing.
- Use the same scrim for other main-window inline input editors; keep their bottom
  action layout and token-based button roles unchanged.
- The scrim intercepts workspace mouse events and keeps editor keyboard focus
  inside the editor; it resizes with the central widget. The editor/feedback
  surface remains fully opaque above it.
- Hide the scrim on Apply/Add, Cancel, Escape and replacement of the editor.
  Do not dim non-modal confirmations, progress or operation outcomes.

## Confirmation palette follow-up (2026-10-09)
- Cloud asset preparation is a non-destructive amber confirmation, not an error:
  remove the red `color.dangerSurface` tint from its feedback surface.
- Use existing neutral `color.button`/`color.surface` gradient and amber
  `color.statusWarningBorder`/icon; keep the amber Confirm button unchanged.
- Re-polish the inline symbol on state transitions so the icon cannot remain
  blue when the panel becomes amber or red. Preserve red Delete/Clear semantics,
  cancellation, upload warnings and service behavior.

## Affirmative consent palette refinement (2026-10-09)
- Owner superseded the previous amber confirmation palette: use the same existing
  mint/teal tokens as Auto-refresh ON (`color.connectionSurface`,
  `color.connectionAccent`, `color.connectionGlyph`) for non-destructive
  Confirm buttons and their inline feedback state.
- Keep Delete/Clear Confirm red, other primary actions blue, Cancel neutral,
  partial/stopped warnings amber, and real errors red.
- Preserve two-click consent, upload/credit text, frozen scopes and controller
  safety. This changes presentation only; no new tokens or model/provider behavior.

## Teardown lifecycle correction (2026-10-09)
- CI at `fe6f05e` reports 426 passed, 3 skipped and 2 teardown errors:
  delayed resize/layout events accessed deleted inline feedback, and a
  generation progress event accessed a deleted progress row.
- Retire the shared activity strip when its Qt main window is destroyed,
  detach central-widget/form event filters and stop its notice timer before
  child-widget teardown. Late position/progress calls do not access stale Qt
  objects. Remove the main-window plain-Python activity listeners on window
  destruction and stop the finishing timer.
- Preserve all non-teardown operation and shutdown semantics. No tests added or
  run locally; await new CI and owner runtime/packaged visual acceptance.

## Prepare simplification and Qt teardown correction (2026-10-09)
- Owner removed the redundant second confirmation for cloud LoRA Prepare:
  explicit file selection starts the existing preparation workflow, while
  cancelling the picker remains a no-op. The asset note/tooltip disclose
  possible DT+ upload and later-generation credits. Recheck busy/backend
  identity after the picker; preserve receipt validation and no-retry rules.
- CI at `a4cf45e` reported 35 failures, 391 passed, 3 skipped; repeated Qt
  errors arose because `window.destroyed` fired after the overlay's central
  host was deleted. Retire on the activity strip's own `form.destroyed`
  instead, without invoking methods on dying Qt objects; Qt owns filter and
  child timer cleanup. Unsubscribe main-window activity listeners at the
  same earlier boundary.
- One integration queue test separately reached its five-second timeout.
  Avoid speculative changes to durable scheduling until reproduced.
- Adapt the existing LoRA presentation test to the one-step workflow.
  No new or local tests were run; await CI and owner runtime acceptance.

## Feedback child-lifetime follow-up (2026-10-09)
- CI at `e4e3420` still reports 426 passed, 3 skipped and two Qt
  teardown errors: the inline feedback child is deleted while the activity
  strip can still receive layout events. Retiring the strip when its own
  frame is destroyed does not protect a younger child.
- Clear the feedback reference on the child's `destroyed` signal and check
  Shiboken's Qt wrapper validity before using widgets during layout and
  progress updates. Avoid catching/suppressing `RuntimeError`.
- Keep progress-detail updates inside the strip rather than dereferencing
  the progress row directly from MainWindow; preserve progress and Stop
  semantics. Extend the existing Qt shell tests with a child-deletion
  regression; new CI and native runtime acceptance remain pending.

## PR review closeout follow-up (2026-10-09)
- LoRA Prepare intentionally starts after native file selection with no second
  Confirm. Correct the stale PR summary to describe explicit file selection,
  prior upload/credit disclosure and preserved receipt/no-retry validation.
- Keep owner-approved LoRA Add range 0.0–3.0, leaving saved requests untouched.
  When a catalogue default is outside range, visibly explain the 1.0 initial
  value instead of silently substituting it.
- An unrelated InlineNotice during pending consent appears as its own
  dismissible activity row, without changing the armed button. Explicit
  navigation/scope invalidation still cancels safely.
- Make Activity.unlisten idempotent and unsubscribe Queues/Generation listeners
  on their respective Qt page destruction. Extend an existing Qt consent test
  to cover the unrelated-notice case. No actual generation/upload performed.

## Overlay parity and safety closeout (2026-10-09)
- Compare the accepted HTML mockup's progress, success, partial, stopped,
  error and confirmation states with the actual Designer forms and QSS.
  Implement the 2 px full-width mint lifetime line and 450 ms fade for six-second
  success rows, retaining the numeric countdown and pausing the entire
  overlay on hover/focus. Keep blue task progress separate and honest.
- Eliminate the duplicate blue outer border; reuse red for errors and
  destructive confirmations, amber surfaces/glyphs/borders for partial,
  stopped and nonfatal warning, and teal for success/non-destructive consent.
  Complete semantic feedback states and distinguish stopped/operation glyphs.
- Preserve unsaved inline editor input when a background notice arrives.
  Enable Escape on standalone informational notices; warnings remain
  persistent. Do not make notices cancel running work.
- Remove hardcoded button-object-name danger detection from Python; pass
  `danger=True` from destructive call sites. QSS retains higher-specificity
  Qt style overrides where named buttons require them.
- Extend existing Qt shell assertions for editor coexistence, lifetime state,
  stopped glyph, and Escape dismissal. Do not run real generation or uploads.
- After code and CI acceptance, update the separate HTML prototype to
  represent all validated Qt states, workflows and later decisions.

## Export cancellation transient-result refinement (2026-10-09)
- A clean, user-cancelled image Export retains amber `stopped` semantics but
  explicitly opts into the existing six-second draining countdown/bar,
  hover/focus pause and 450 ms fade. The timer/line use the amber stopped
  accent rather than showing a success color.
- Stopped queue/Trash outcomes remain persistent. Export cancellations
  with failure or warning details are still partial and persistent.
  Do not alter export interruption, retained-file, or retry behavior.
- Extend existing Qt overlay assertions for opt-in stopped expiry, and
  preserve the default persistent stopped/partial contracts.
- CI and owner native Windows visual acceptance remain separately required.

## Unified amber lifetime and destructive icon (2026-10-10)
- Owner requested a consistent six-second countdown/draining bar and fade
  for every amber warning, partial or stopped overlay. Apply this uniformly
  in the shared activity strip; route standalone warning notices through it.
  Errors and informational inspection stay persistent. Countdown and line
  use amber, while success continues to use teal.
- The countdown is presentation only: it does not settle or dismiss durable
  uncertainty, erase warnings, or imply remote cancellation. Generation stop
  evidence stays recorded in the Generation view and attempt ledger.
- Draw the Recycle Bin destructive-confirmation symbol as a red monochrome
  trash can, not the OS green recycling emoji. Preserve the red danger
  surface, stable Confirm button, Cancel and explicit consent semantics.
- Extend existing Qt shell tests for state lifetimes, warning notices and
  trash icon. New CI and owner native visual smoke are required before
  accepting the HTML mockup synchronization.

## Single-generation ring step preservation (2026-10-10)
- A non-sampling Draw Things signpost or preview event has no step count; the compact Generate overlay refactor had been replacing the shared RunState count with None, causing the global Run ring to fall back to its animated indeterminate indicator.
- Preserve the latest observed, validated per-generation sampler step/total across stage-only and preview events, including in the overlay count. Mark it as the *last reported* step in stage tooltips, never a newly inferred percentage; reset on each new generation and on settlement, keeping queue semantics unchanged.
- Add a focused existing Qt generation regression covering sampling → stage → preview → later sampling and next-run reset. Owner native visual/provider acceptance remains separate.

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

## Generate center simplification (2026-10-10)
- Move the existing Generate image button into the prompt header at the right of Expand prompt, preserving the original single controller and primary QSS styling.
- Reduce the default prompt height from 76 to 52 logical pixels (about two lines), keeping Expand prompt at 180 and its existing toggle behavior.
- Remove the redundant Stop waiting button and synthetic percentage bar from the Generate center form/controller. The shared activity overlay remains the single stopping/progress surface; its stop calls the unchanged local cancellation controller.
- Preserve the discreet persistent status line, saved attempt evidence, preview and result actions. Show only backend-reported stages and step counts in the overlay, without inventing completion percentages; uncertainty remains durable.
- Adapt existing Qt generation tests to the canonical overlay controls and new prompt geometry; GitHub CI and native Windows visual acceptance remain separate. No live generation or billable action.

## Prompt-header icon polish (2026-10-10)
- Refine the relocated Generate action to match the accepted mockup: 16 px sparkles SVG in the existing blue primary button, and a 38 px icon-only Expand prompt button with separate expanded/collapsed SVGs and accessible stateful labels.
- Use the Images SVG/QIcon convention and the existing shared QSS palette. Preserve the Generate action, 52/180 px prompt sizing, and native single-generation/overlay operation semantics.
- Extend the existing Generate Qt layout assertion to cover icon presence, toggle state and accessibility. CI and native Windows visual acceptance remain separate.

## Mockup synchronization and owner acceptance (2026-10-10)

- The owner reports completing an interactive smoke test of the entire current
  PR #58 UI and reports **all good**; this is owner-reported native acceptance,
  not an agent-run Windows/packaged test.
- Import the approved original HTML (historical SHA-256 retained above) to
  [devdocs/mockups/dtflow_inline_panels.html](../mockups/dtflow_inline_panels.html)
  and synchronize it with the approved implementation. Keep the accepted
  colors/layout and embedded illustrative images, but update the modern Qt
  interactions: red Delete/Clear consent, persistent error/info, timed
  success/amber notifications, LoRA editor, Generate compact prompt and
  status-only Run ring, and Images Export's independent panel toggle.
- The prototype **does not simulate durable provider behavior**. Example
  photo pixels, backend counts, sampling progression, file paths, and status
  messages remain explicitly demonstrative. The actual Qt code is the source
  of truth for remote cancellation, retries, original-file leases,
  preservation and generation evidence.
- CI checks for the resulting documentation commit are separate from the
  previously verified Python/Qt CI at `ad0f09c`; no inference, export,
  recycling, training or potentially billable operation is run by the agent.

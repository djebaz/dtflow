# Plan: Inline desktop actions

## Scope
- In: Inventory and replace DTFlow-owned confirmation, error, result and progress windows with main-window presentation; preserve safety/service boundaries.
- In: Prefer an action button that becomes Confirm, with nearby cancellation and contextual explanation.
- Out: Native file/folder pickers, release/version changes, provider behavior, new tests, agent tests or smoke tests.

## Action items
- [x] Inspect clean main, remote and open PR state; create feature/inline-actions.
- [x] Inventory popup call sites and review canonical UI/design and safety documentation.
- [ ] Resolve owner UX choices before implementation.
- [ ] Implement shared inline presentation and action confirmation with stale-scope invalidation.
- [ ] Replace affected controller paths without weakening destructive/billable safeguards.
- [ ] Synchronize design, desktop guide, repository contract, status and unreleased notes.
- [ ] Perform minimal non-test static checks and review staged scope.
- [ ] Commit/push implementation and update PR to main; do not merge or release.

## Popup inventory
- main_window.py: preset replacement/import/export errors, cache busy/cleanup confirmation/failure, regeneration confirmation/busy, custom-LoRA clear/import failures, saved-key removal, close during recycling, About.
- queue_page.py: queue Cancel/Clear, uncertain-reservation acknowledgement, close during active dispatch.
- image_recycle_panel.py: busy/empty/error notices, frozen-scope Trash confirmation, progress/cancel window, outcome summary.
- lora_panel.py: searchable Add chooser, numeric weight entry, explicit cloud-upload confirmation.
- search_select.py: checkpoint/LoRA search selection and custom-identifier input.
- generation_page.py: saved metadata/error display, unclassified checkpoint list, preset-name input.
- Native QFileDialog open/save/folder paths remain unchanged.

## Decisions and Design Changes
- 2026-10-09 Owner requests no DTFlow popup-style dialogs; native file/folder pickers stay.
- 2026-10-09 Owner prefers the triggering button to mutate to confirmation so the pointer need not travel.
- 2026-10-09 No release, version bump or packaging for this enhancement PR.
- 2026-10-09 Keep frozen Trash plans, execution revalidation/leases/durable intents, cache coordination, queue uncertainty warnings and explicit upload/credit consent unchanged.

## Open questions
- Confirm + nearby Cancel without timer, or reset after 10 seconds?
- Replace searchable selectors/weight/name input with inline editors in this PR, or defer selector redesign?

## Validation
- [ ] Final tests and smoke tests deferred to user.
- Tests were not added or run. Smoke tests were not run. Final runtime/visual and packaged acceptance remains with the owner.
- Implementation has not started while blocking UX choices remain unresolved.

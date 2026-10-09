# Plan: Images inline export column

## Scope
- In: Toggle a scrollable right-hand export column beside Images content; reuse the exact Connection export card, controller and operation.
- Out: Export encoding/service changes, new dependencies, tests, packaging and provider calls.

## Action items
- [x] Add canonical gallery column and Connection card host, preserving existing widget references.
- [x] Move the single card between hosts on navigation/toggle; preserve running operation and preferences, with a close action that never cancels.
- [x] Synchronize current UX contracts and release notes.
- [x] Run static Ruff lint/format, syntax/import, XML structure and Qt Designer compilation checks; no GUI instantiated. Commit/push this coherent checkpoint to the existing PR #57 branch.

## Decisions and Design Changes
- 2026-10-09: Owner clarified beside the listing, not above it. Reparent one shared card rather than creating two competing exporters. Connection always regains the card when leaving Images; Images remembers whether its column is open. Opening selects checked scope only when idle; no immediate export or tab redirect.

## Open questions
- None.

## Validation
- Tests and smoke tests are not run; final layout/export/cancel/navigation smoke remains with the owner. Earlier export smoke was owner-reported for the previous navigation behavior.

Static checks passed in the locked environment. No tests, GUI smoke, packaging, actual export/Trash operation, training/inference or potentially billable action ran for this follow-up.

## Checked-only Images follow-up
- 2026-10-09: Owner reports smoke passed for 5772f54, then requests removal of the Images scope dropdown. Hide the scope label/dropdown only in Images; keep every scope in Connection. New exports started from the Images panel always capture checked IDs, even after a Connection-started operation settles there; never change an already-running snapshot. Focus the destination rather than the hidden selector. This small follow-up remains owner-smoke-pending; static checks only.

## Always-available Export panel toggle (2026-10-10)
- Make the Images Export toolbar button a checkable panel toggle, enabled independently of checked-image count. Neutral Off and selected-blue On use the existing gallery tokens.
- Synchronize the checked state after the toolbar click, the panel's Close button and navigation between Images and Connection; preserve open-panel intent when returning to Images.
- Keep the shared export card/controller and checked-only Images submission validation. Opening the panel alone must not start an export, and changing the panel visibility must not cancel an active operation.
- Extend an existing Qt shell test for empty-selection opening, checked states, Close and navigation. GitHub CI and native visual acceptance remain separate.

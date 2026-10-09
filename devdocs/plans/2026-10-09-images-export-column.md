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

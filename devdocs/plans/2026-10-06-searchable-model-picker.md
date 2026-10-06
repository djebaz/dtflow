# Plan: searchable Generate model picker

## Scope
- In: dedicated search popup for large inventories, single-line wide results, exact IDs, keyboard selection, eight recent choices per provider, manual ID entry and stable selection on inventory refresh.
- Out: favorites, metadata/family filters, thumbnails, backend changes, generation and dependencies.

## Action items
- [x] Inspect canonical form, inventory and session seams; create feature branch.
- [ ] Implement presentation-only picker and additive per-provider recent memory.
- [ ] Synchronize desktop/design/status/release documentation.
- [ ] Static formatting, lint, compilation/import and resource parsing; commit/push new PR.

## Decisions and Design Changes
- 2026-10-06: approved searchable picker; search is separate from the committed exact ID, Escape cancels. Wider default popup (760 logical pixels, screen-clamped), no wrapping, full-ID tooltips.
- 2026-10-06: reuse last_session.json optional provider-form fields for bounded recents; no durable schema migration. Inventory absence is not evidence of unavailability.

## Open questions
None.

## Validation
- [ ] Final tests and smoke tests deferred to user under fast-implement. No tests added/run; no smoke, packaging, inference or billable actions planned.

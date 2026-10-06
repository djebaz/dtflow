# Plan: searchable Generate model picker

## Scope
- In: dedicated search popup for large inventories, single-line wide results, exact IDs, keyboard selection, eight recent choices per provider, manual ID entry and stable selection on inventory refresh.
- Out: favorites, metadata/family filters, thumbnails, backend changes, generation and dependencies.

## Action items
- [x] Inspect canonical form, inventory and session seams; create feature branch.
- [x] Implement presentation-only picker and additive per-provider recent memory.
- [x] Synchronize desktop/design/status/release documentation.
- [x] Static formatting, lint, compilation/import and resource parsing; commit/push new PR.

## Decisions and Design Changes
- 2026-10-06: approved searchable picker; search is separate from the committed exact ID, Escape cancels. Wider default popup (760 logical pixels, screen-clamped), no wrapping, full-ID tooltips.
- 2026-10-06: reuse last_session.json optional provider-form fields for bounded recents; no durable schema migration. Inventory absence is not evidence of unavailability.

- 2026-10-06: preserve exact IDs with blocked combo replacement signals; provider/endpoint changes clear evidence and reject stale live/cache completions. Supplied display names stay on the same single line as the ID. No new dependencies or token values.

## Open questions
None.

## Validation
- [ ] Final tests and smoke tests deferred to user under fast-implement. No tests added/run; no smoke, packaging, inference or billable actions planned.

## Delivery and static evidence
- New PR #38 targets main from feature/searchable-model-picker; not merged.
- Passed: targeted Ruff lint/format, Python compilation/imports, canonical UI XML parsing, shared theme/token resolution and git diff --check. No QApplication or interactive UI was launched.
- Tests and smoke tests were not run or added; final 500+ item, keyboard, narrow-screen and restart acceptance remains with the owner.
- Packaging skipped: source UI feature PR, no release requested. No training/inference, network/provider checks, uploads or billable actions performed.
- AGENTS.md contract remains unchanged; desktop guide, README, DESIGN, runtime layout, current status and unreleased notes synchronized. No version bump.

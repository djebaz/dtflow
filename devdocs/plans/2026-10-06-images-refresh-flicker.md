# Plan: Images refresh without blinking

## Scope
- In: keep Grid, List and Detail intact when a refresh returns the same visible outputs.
- Out: incremental rendering of changed pages, packaging, provider operations, version bump.

## Action items
- [x] Trace refresh and rendering; start a separate fix branch from current main.
- [x] Skip rendering/redecoding identical pages; still update counts/navigation and facets.
- [x] Sync viewer documentation and unreleased notes.
- [x] Review changes, commit and push implementation; open/update PR #37 to main. Not merged.

## Decisions and Design Changes
- 2026-10-06 Compare the complete immutable visible item records and rendered offset, not just identities or total count. Changed metadata/file availability must still render; a changed total alone need not clear images.
- 2026-10-06 Preserve in-flight image decodes, exact scroll, focus and Detail zoom/pan on unchanged refreshes. Initial empty results still render the empty state. Explicit page-navigation actions retain existing behavior.
- 2026-10-06 Use fast-implement: no new tests, test runs or smoke tests. Packaging is not applicable to this source-only bug-fix PR.

## Open questions
- None.

## Validation
- [ ] Final tests and smoke tests deferred to user.
- Tests and smoke tests were not run; final validation remains with the user.

- Checks run: Ruff format/check on the changed controller, Python compilation/import, and git diff --check. No tests or smoke tests.
- Documentation review: AGENTS.md and README.md need no contract/feature-list changes; detailed behavior is recorded in the desktop guide, viewer plan and unreleased notes.
- New PR: #37, targeting main; no merge, tag or release.

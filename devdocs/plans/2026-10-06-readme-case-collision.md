# Plan: Windows-safe design-system README

## Scope
- In: remove the case-only tracked duplicate, keep canonical README.md links and preserve the newer segmented Run documentation.
- Out: UI/runtime changes, branch cleanup, resets, releases.

## Action items
- [x] Confirm main matches origin/main and identify the two conflicting tracked paths.
- [x] Preserve the newer lowercase file content in canonical uppercase README.md; remove only the duplicate index entry.
- [x] Check all tracked paths for case-insensitive collisions and existing documentation references.
- [ ] Commit/push and open a small PR to main; leave merging to the owner.

## Decisions and Design Changes
- 2026-10-06: Windows maps both tracked names to one physical file, so restoring one necessarily dirties the other. This was not an ordinary local edit.
- README.md is canonical because all current links use it. Its retained content exactly matches the previous lowercase Git blob e87a739bb8e30ff421f50ef8a33aa4fbec7448b8.
- This was the only case-insensitive tracked filename collision detected.

## Open questions
None.

## Validation
- Documentation/index checks and git diff --check only; no tests/smoke or packaging needed for this documentation-only repair.
- No runtime changes, inference, training, provider operations or billable actions.

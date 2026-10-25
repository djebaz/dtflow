# Plan: isolate overlay startup cost (#169, PR #173)

## Scope
- In: instrument ActivityStrip initialization and expose startup-report phase populations/schema differences.
- Out: lazy ActivityStrip, speculative optimization, UI changes, generation, packaging and merge.

## Action items
- [x] Inspect the active clean PR #173 branch and current constructor/report implementation.
- [x] Add marks around Designer loading, scrim construction, status-bar acquisition, painted ActivityBar construction and final wiring/timers.
- [x] Preserve existing median fields while adding per-phase sample counts, schema count and a mixed-schema warning.
- [x] Synchronize status/release documentation.
- [ ] Collect matched native Windows logs and select an optimization only after identifying the expensive operation.
- [ ] Final tests and smoke tests deferred to user.

## Decisions and Design Changes
- 2026-10-25: the checkout already advanced beyond merged #172 to PR #173; keep this refinement on its existing clean branch.
- 2026-10-25: retain eager shared status presentation. The reported 96% share identifies a candidate, not a proven internal cause or speedup.
- 2026-10-25: phase schemas compare ordered phase names. Missing phases are not zero-duration samples; retain the existing per-phase medians with explicit populations.

## Open questions
- None blocking implementation; matched native measurements remain necessary.

## Validation
- Tests were not added or run; smoke tests were not run. Final validation remains with the user.
- The owner permits future GUI launch/close measurements and startup_report.py execution. No such launch/report run was performed in this budget-limited refinement.
- No renders, packaging, provider calls, uploads or inference were performed.

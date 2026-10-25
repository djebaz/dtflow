# Plan: isolate overlay startup cost (#169, PR #173)

## Scope
- In: instrument ActivityStrip initialization and expose startup-report phase populations/schema differences.
- Out: lazy ActivityStrip, speculative optimization, UI changes, generation, packaging and merge.

## Action items
- [x] Inspect the active clean PR #173 branch and current constructor/report implementation.
- [x] Add marks around Designer loading, scrim construction, status-bar acquisition, painted ActivityBar construction and final wiring/timers.
- [x] Preserve existing median fields while adding per-phase sample counts, schema count and a mixed-schema warning.
- [x] Synchronize status/release documentation.
- [x] Inspect three owner captures with identical phase schemas; correct the earlier broad attribution to ActivityStrip.
- [x] Split preceding Logs placeholder, stack-hide and root-layout insertion work, and isolate QObject construction from host acquisition.
- [x] Split the development-only QLineEdit probe into construction, polishing and explicit native destruction timings, without changing normal non-development startup.
- [ ] Collect matched native Windows logs and select an optimization only after identifying the expensive operation.
- [ ] Final tests and smoke tests deferred to user.

## Decisions and Design Changes
- 2026-10-25: the checkout already advanced beyond merged #172 to PR #173; keep this refinement on its existing clean branch.
- 2026-10-25: retain eager shared status presentation. The reported 96% share identifies a candidate, not a proven internal cause or speedup.
- 2026-10-25: phase schemas compare ordered phase names. Missing phases are not zero-duration samples; retain the existing per-phase medians with explicit populations.
- 2026-10-25: owner captures `2ec1e03dcfb6`, `2947e04de618` and `90ea09debf67` share one phase schema (n=3 per phase). Recorded startup totals are 11.33 s, 11.10 s and 15.74 s (median 11.33 s); all report successful connection checks and no warnings/errors in the inspected snapshots. No launches or tests were needed to inspect this evidence.
- 2026-10-25: ActivityStrip work measured after its internal entry mark is approximately 17 ms. The preceding 0.87–1.07 s interval also includes Logs placeholders, hiding the old stack and inserting the shell into the root layout, plus initial QObject/host acquisition. This supersedes the earlier 96%-overlay attribution: do not make ActivityStrip lazy or identify root-layout insertion as the culprit until the new boundaries isolate it.

## Open questions
- None blocking implementation; matched native measurements remain necessary.

## QLineEdit probe refinement
- The old `first QLineEdit polished` phase combined construction, polishing and temporary disposal. It is replaced by `QLineEdit probe: constructed`, `QLineEdit probe: polished` and `QLineEdit probe: destroyed`; do not compare the old phase directly to the new polishing-only phase.
- The probe remains hidden, parentless and development-only. Explicit Shiboken deletion measures native destruction before releasing its Python wrapper; no real input control is deleted or moved to a worker.
- This adds measurement boundaries only. It does not disable the warm-up, establish a speedup or perform the proposed with/without-warm-up comparison. New matched owner logs are required; mixing old/new logs must trigger the report's schema warning.

## Validation
- Tests were not added or run; smoke tests were not run. Final validation remains with the user.
- The owner permits future GUI launch/close measurements and startup_report.py execution. No such launch/report run was performed in this budget-limited refinement.
- No renders, packaging, provider calls, uploads or inference were performed.

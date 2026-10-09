# Plan: pixel-statistics detector PoC

## Scope
- In: standalone local pixel-only diagnostic under tools/images; JSON metrics and advisory noise/blank flags.
- Out: aesthetic scoring, anatomy/prompt checks, automatic deletion, job-status changes, GUI/export integration, inference.

## Action items
- [x] Implement bounded read-only analysis with Pillow and standard library only.
- [x] Document CLI, provisional thresholds and false-positive limitations.
- [x] Check syntax/imports, formatting and help. Delivery stays on PR #55 as an isolated tool-only follow-up.

## Decisions and Design Changes
- 2026-10-09: Owner requested a detector PoC after comparing smoke3 outputs. Continue the existing export branch, but keep the tool independent of production services.
- Metrics use a bounded RGB thumbnail, colour clipping/saturation, neighbour differences, luminance entropy and coarse-scale residual. Thresholds are uncalibrated heuristics, not confidence or quality ratings.
- Source files are never modified. Existing report files are never overwritten. No new dependencies.

## Version 2 follow-up
- [x] Add independent spatial-noise rule: neighbour difference >=0.08, residual RMS >=0.18 and residual/stddev >=0.65, all required; retain existing rules and blank priority.
- [x] Update algorithm identity and documentation; scoped Ruff, AST/import and help checks passed. Delivery remains in PR #55. No new image run performed for this code follow-up.
- Owner-labelled evidence: 262 good images had no flags; original failed set detected 11/12. Smoke3 review identified two misses. Cached-metric exploration adds exactly those two with zero flags among the 262 good images. This is sample fitting, not independent validation.

## Open questions
- None blocking. Calibration against owner-labelled examples is future work.

## Validation
- [x] Static AST/import, scoped Ruff formatting/lint and CLI help passed; initial implementation had no image analysis executed; subsequent owner-requested runs analyzed failed/tests/smoke3.
- [ ] Final tests and smoke tests deferred to user. No automated tests added or run. Owner explicitly requested subsequent detector image runs; independent validation of version 2 remains pending.
- Packaging is not applicable to this standalone developer PoC. No inference or billable actions.

## Shared-service follow-up
Owner requested production export sorting after labelled sample review. Pixel rules now live in services/image_quality.py, reused by the CLI and optional Connection export routing; this supersedes the original developer-only boundary only for copy routing. See [routing plan](2026-10-09-export-noise-routing.md). No job-status changes or independent accuracy claim.

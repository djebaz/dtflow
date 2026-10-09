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

## Open questions
- None blocking. Calibration against owner-labelled examples is future work.

## Validation
- [x] Static AST/import, scoped Ruff formatting/lint and CLI help passed; no image analysis executed.
- [ ] Final tests and smoke tests deferred to user. No tests added or run; no detector run on images by the agent.
- Packaging is not applicable to this standalone developer PoC. No inference or billable actions.

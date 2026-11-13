# Plan: Consolidate UI previews (#421)

## Scope

- In: one Preview Action at `preview-ui.yml`, change-selected/category/all/workflow-file modes, exact-head targeting, shared publication and consistent offline tooling names.
- Out: application runtime behavior, test/fixture changes, local screenshots, provider calls, build/release uploads and historical evidence rewrites.

## Action items

- [x] Consolidate request planning and workflows; skip all automatic draft requests, including labels.
- [x] Migrate screenshot tooling filenames and active references without changing fixture behavior.
- [x] Share GitHub artifact, latest-per-PR thumbnail publication and provenance/coverage reporting.
- [x] Update active documentation and unreleased notes; inspect static checks and diff.
- [x] Commit/push a coherent initial implementation and open draft PR #422 at `ebbc0a62`.
- [x] Finish source/reference review; validate Qt-free imports and catalogue/pack metadata.
- [x] Finalize source-review corrections for commit/push and the Ready-for-review handoff; actual Actions execution remains separate evidence, tracked on PR #422.

## Decisions and design changes

- 2026-11-13: only `preview:PART` and `preview:ALL`; no category labels or archived-label restoration. ALL wins. Manual category input stays available.
- 2026-11-13: automatic requests skip drafts; manual dispatch is explicit and may target a draft. Recheck live draft/head before expensive capture and publication.
- 2026-11-13: retain `ci-render-previews` as a compatibility publication branch to preserve existing PR images; new tool/output names use Preview. Reuse existing comment markers when updating old PRs.
- 2026-11-13: consolidate on GitHub PNG/diagnostic artifacts (two-day requested retention, subject to Artifact Sweeper) and inline thumbnails; retire render-only Drive upload, leaving release/build Drive tooling untouched.
- 2026-11-13: inspection found build/release imports `upload_ui_renders_to_drive.py`; retain this shared legacy helper unchanged rather than break release uploads. Do not rename unrelated Qt/image rendering APIs or dated evidence.
- 2026-11-13: use at most two capture workers, retain separate state/workflow-file outcomes, and cap the comment grid at 48 thumbnails with links/artifact access to every produced image. Preserve fast runtime LoRA coverage from the old targeted workflow, derived from the catalogue rather than YAML counts.

## Open questions

None blocking. Implementation details and limitations will be recorded here.

## Validation

- Tests, renders and smoke tests will not be run by the agent. Final execution and native Windows/DPI acceptance remain with the owner.
- Environment readiness passed: Python 3.13.13 / SQLite 3.53.1. Focused Ruff lint/format, Python AST and JSON parsing, and Actionlint 1.7.12 passed. Actionlint's optional ShellCheck/Pyflakes integrations were disabled because those executables are not installed; no shell execution or workflow runtime acceptance is implied.
- Qt-free orchestration imports and selection metadata validation passed (494 existing states, 41 packs). No fixture scenarios or catalogue entries were added or changed; fixture modules only have mechanical import/function/path renames.
- GitHub observed both Preview jobs skipped on draft PR #422 at `ebbc0a62` (run `43493375838`); this is draft-guard evidence, not a successful capture/publication run. No CI/Actions manually dispatched.
- Final source review covered exact-head targeting, automatic draft/closed-PR refusal, changed-vs-all precedence, selection prerequisites, timeout/result separation, shared-helper release compatibility, lease publication/fallback and active references. Actual capture, private-repository inline visibility, concurrent publication/cancellation and native acceptance remain unverified.

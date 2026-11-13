# Plan: Consolidate UI previews (#421)

## Scope

- In: one Preview Action at `preview-ui.yml`, change-selected/category/all/workflow-file modes, exact-head targeting, shared publication and consistent offline tooling names.
- Out: application runtime behavior, test/fixture changes, local screenshots, provider calls, build/release uploads and historical evidence rewrites.

## Action items

- [x] Consolidate request planning and workflows; skip all automatic draft requests, including labels.
- [x] Migrate screenshot tooling filenames and active references without changing fixture behavior.
- [x] Share GitHub artifact, latest-per-PR thumbnail publication and provenance/coverage reporting.
- [ ] Update active documentation and unreleased notes; inspect static checks and diff.
- [ ] Commit/push a coherent initial implementation and open a draft PR; finish source review before readiness.

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
- Source/reference review continuing; no CI/Actions dispatch authorized by this issue.

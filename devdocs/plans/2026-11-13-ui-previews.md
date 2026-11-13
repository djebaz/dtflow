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

- 2026-11-13 review follow-up: correct the active deleted-script/output-path references and retired Preview-to-Drive instructions; document the GNU-timeout capture orchestrator as Linux CI-only. Harden file-derived diagnostic table/inline labels with escaped HTML code and selection notes with escaped preformatted HTML, so backticks, pipes, newlines or HTML in paths cannot break out into report markup. No new tests, fixtures or captures; focused static validation only before the next automatic Preview run.

- 2026-11-13: only `preview:PART` and `preview:ALL`; no category labels or archived-label restoration. ALL wins. Manual category input stays available.
- 2026-11-13: automatic requests skip drafts; manual dispatch is explicit and may target a draft. Recheck live draft/head before expensive capture and publication.
- 2026-11-13: initially retained `ci-render-previews`; the owner subsequently requested **`preview-outputs`**. Rename the remote branch preserving its current image tree, migrate existing preview bot-comment links and use a shared branch constant for future publication/report URLs. Reuse existing comment markers when updating old PRs.
- 2026-11-13: consolidate on GitHub PNG/diagnostic artifacts (two-day requested retention, subject to Artifact Sweeper) and inline thumbnails; retire render-only Drive upload, leaving release/build Drive tooling untouched.
- 2026-11-13: inspection found build/release imports `upload_ui_renders_to_drive.py`; retain this shared legacy helper unchanged rather than break release uploads. Do not rename unrelated Qt/image rendering APIs or dated evidence.
- 2026-11-13: use at most two capture workers, retain separate state/workflow-file outcomes, and cap the comment grid at 48 thumbnails with links/artifact access to every produced image. Preserve fast runtime LoRA coverage from the old targeted workflow, derived from the catalogue rather than YAML counts.

## Open questions

Performance follow-up: compare the next automatic Preview run with `43499855118` at `701c36e4` (59-second job, 18-second dependency sync including 14.21-second installation, 12-second capture). Try Linux-only hardlinks with a workspace-local package cache, managed-Python caching and `--no-dev` to omit unused browser/test/lint dependencies. Keep all runtime dependencies and `uv.lock` unchanged. The separate `preview-runtime-v1` cache starts cold; warm-cache gains remain unverified until measured. No local tests/renders or manual Actions dispatches.

None blocking. Implementation details and limitations will be recorded here.

## Validation

- Tests, renders and smoke tests will not be run by the agent. Final execution and native Windows/DPI acceptance remain with the owner.
- Environment readiness passed: Python 3.13.13 / SQLite 3.53.1. Focused Ruff lint/format, Python AST and JSON parsing, and Actionlint 1.7.12 passed. Actionlint's optional ShellCheck/Pyflakes integrations were disabled because those executables are not installed; no shell execution or workflow runtime acceptance is implied.
- Qt-free orchestration imports and selection metadata validation passed (494 existing states, 41 packs). No fixture scenarios or catalogue entries were added or changed; fixture modules only have mechanical import/function/path renames.
- GitHub observed both Preview jobs skipped on draft PR #422 at `ebbc0a62` (run `43493375838`); this is draft-guard evidence, not a successful capture/publication run. No CI/Actions manually dispatched.
- Final source review covered exact-head targeting, automatic draft/closed-PR refusal, changed-vs-all precedence, selection prerequisites, timeout/result separation, shared-helper release compatibility, lease publication/fallback and active references. Actual capture, private-repository inline visibility, concurrent publication/cancellation and native acceptance remain unverified.
- Automatic run `43493906747` subsequently passed at `83b055d1`: all 29 selected states plus two workflow-file screenshots, artifact and preview-branch publication, and PR-comment update succeeded. Downloaded the artifact and inspected the LoRA metadata tooltip and workflow window. Two fixture waits timed out (`queues-60-item-table`, `images-105-pager-default-20-first-batch`); those captures are not acceptance. ALL/manual modes, concurrent publication/cancellation and native acceptance remain unchecked.
- Owner naming follow-up: renamed remote `ci-render-previews` to `preview-outputs`, preserving commit/tree `50433db7b4a125b75d42fc5425dc83d59be51b77` (PR #420 and #422 image directories). Migrated both existing preview bot comments, updated code/docs, and clarified thumbnail counts: show “all N” when all images fit, otherwise “48 of N”. Focused Ruff and diff checks passed; new-head capture/publication remains separate evidence.
- Owner presentation follow-up: group thumbnail grids by catalogue category, with workflow-file images separate, inside closed-by-default accordions with H3-sized clickable titles. Share the existing 48-thumbnail comment budget across categories and disclose per-category sampling. No capture fixtures or image outputs change.
- Owner screenshot review supersedes the H3 title choice: use compact inline-bold titles with bracketed image counts (for example, **LoRA** [11]), no heading margins and no “preview(s)” wording in accordion titles. Keep categories closed by default and retain the three-column grids.
- Owner report-density follow-up: replace the expanded path-warning list with one coverage line, combine mode/source/counts/links in one row, remove repeated category counts and all-images captions, and consolidate workflow/capture/worker/retention/selection detail into one closed Diagnostics section. Keep failure, missing-image and timed-out-wait warnings visible; preserve complete exact records in the artifact. No capture inputs or fixtures change.
- Owner minimal-selection follow-up supersedes the legacy full-LoRA preservation rule: exactly one existing state per catalogue category in the shared baseline (ten views). Tooling/workflow files, including old rename/deletion paths, select this baseline before component mappings; remove unconditional LoRA expansion and tooling-triggered workflow-file captures. Actual application changes retain component/page sampling; explicit category/all/workflow modes remain available. Only selection metadata changes, with no new/modified fixture scenarios or screenshots.
- Static selection verification: Qt-free `preview_changes.py --base origin/main --head HEAD` selected only `shared`, with 10 states and exactly one in each of the 10 catalogue categories for this PR's cumulative diff. Ruff lint/format and diff checks passed. General style changes no longer add duplicate workflow-file captures. No local captures/tests ran; exact-head Actions evidence remains separate.
- Owner billing/readiness follow-up: merge planning/capture/publication into one runner job and one checkout. Keep early expensive-step gating and draft guards; serialize per-PR jobs without cancelling active work, with stale-head checks and documented pending-run trade-offs. Gate-only runs still incur GitHub's private-repository minimum; useful captures no longer incur a second job minimum.
- Inspected artifact/logs from `43497881879` at `ad20a2bc`: 10 images saved but `images-01-grid` took about 139 seconds, with two 60-second and two 8-second waits in the legacy cloud asset example (`preview_states.py:4618`). That flow opens Prepare but never approves the current editor. Select the existing direct gallery-grid projection for the minimal pack instead; stop terminal gallery composition after its last selected capture, preserving patch unwinding and workspace-owned cleanup. No fixture data/scenarios or runtime APIs change. The legacy detailed asset journey remains a known limitation for explicit/ALL requests, not silently accepted.
- Updated selection/orchestration passes focused Ruff and Actionlint (optional ShellCheck/Pyflakes disabled) and diff checks. No local tests/renders or manual Actions dispatches; new-head execution evidence remains pending.

# UI previews

The **Preview** Action (`.github/workflows/preview-ui.yml`) is the single entry point for offline UI screenshots. It replaces UI renders, Explicit UI render request, LoRA Prepare UI renders and Workflow preview screenshot. It never executes a workflow graph, contacts a generation provider or downloads models. Images remain layout evidence, not native Windows/DPI, packaged or provider acceptance.

## Requests

| Request | Selection |
| --- | --- |
| Non-draft, same-repository PR opened, updated, reopened or marked ready | Change-selected representative states when the update affects preview inputs |
| Add `preview:PART` | Representatives from the cumulative PR diff; an unmapped/no-impact request uses the shared sample pack with a coverage warning |
| Add `preview:ALL` | Every registered UI state plus the bundled workflow-file window/canvas capture; wins over PART |
| Manual `mode: changed` | Same representatives; requires a same-repository `pr` number |
| Manual `mode: category` | Fast registered runtime states for the selected category; `workflows` also captures the bundled workflow JSON |
| Manual `mode: all` | Every registered state and bundled workflow-file capture |
| Manual `mode: workflow` | Only the window/canvas for the chosen repository workflow JSON |

No automatic preview work runs for draft PRs, including label additions. Mark ready to enter normal automatic eligibility. Manual dispatch is explicit and can target a draft. Fork/deleted-repository PR targets are refused. Automatic requests pin the event's exact PR head, never GitHub's synthetic merge commit; manual PR dispatch resolves the current head. Live draft/head checks before setup, capture and publication refuse superseded automatic work. A capture already in progress is not forcibly stopped when a PR becomes a draft, but it cannot replace the PR's images/comment afterwards.

There are only two preview labels: `preview:PART` and `preview:ALL`. No per-category labels or automatic label creation. Archived labels are not restored, and old `render`/`render-all` labels are not recognized. Retaining ALL makes subsequent relevant updates exhaustive; retaining either label does not make docs-only follow-up pushes expensive. Re-add a label or use manual dispatch for an explicit fresh request.

Manual inputs are `pr`, `mode`, `category` and `workflow`. Empty `pr` uses the dispatch ref and requires category/all/workflow mode. Category is ignored outside category mode. A custom `workflow` path is used only in workflow mode; automatic requests and other modes use the bundled reference. Paths must stay inside the checkout, with no traversal, and the existing strict importer must accept the file. Workflows-related source/reference/style changes add the reference window/canvas capture alongside representative states; generic Workflows state screenshots and file-based captures remain distinct.

## Selection, cost and concurrency

A small planning job checks eligibility and computes the selection before Qt/uv setup. No native PR path filter suppresses explicit labels. Draft and unrelated-label events skip both jobs; docs-only updates can allocate the short planning job, but no capture job. This is not zero-cost filtering, and two eligible jobs can incur separate runner-minute rounding. `tools/ci/action_changes.py` compares the previous head for synchronize events; screenshot selection uses the cumulative diff against the PR base's merge base. Missing comparison history runs conservatively, with representative selection warnings rather than silent exhaustive rendering.

`tools/ui/preview_catalog.json` owns state identities, paths, levels and fixture prerequisites. `preview_coverage.json` supplies exact component packs; `preview_dependencies.json` supplies page fallbacks. Unknown/no-impact explicit requests use the shared pack with a coverage-incomplete warning. Invalid metadata fails rather than silently requesting everything. Category requests use the same catalogue and runtime capture path as automatic selection, not the separate LoRA design-reference forms. States are deduplicated in catalogue order. Fixture preparation may execute unselected synthetic transitions without saving those images.

To preserve the former targeted LoRA workflow, changes to its canonical editor/picker/main-window/catalogue inputs also include the complete fast runtime LoRA category, combined with other selected representatives without duplicates. This uses the same category selection as manual category mode; no hard-coded screenshot list/count is maintained in YAML.

Only eligible capture jobs join per-PR concurrency; newer relevant requests cancel older captures for that PR, while irrelevant updates do not cancel them. State and workflow-file captures retain separate exit codes/logs, and the second capture is attempted even when the first returns failure. State capture has a 300-second deadline; workflow-file capture has 180 seconds, within a bounded job. Failure diagnostics and partial PNGs are uploaded where available. Two offline Qt worker processes run at most concurrently in Actions.

## Review output

- One downloadable `ui-preview-…` GitHub artifact contains every produced PNG, the exact request/selection, capture outcomes, logs and state manifests. Requested retention is two days, but the manual Artifact Sweeper can delete artifacts older than 30 minutes; it is not a guaranteed two-day download window.
- PR runs publish every produced PNG to the dedicated **`preview-outputs`** branch (renamed from `ci-render-previews`, preserving the existing images). Publication replaces only that PR directory and preserves other PRs in a lease-protected parentless commit. No PNGs are committed to main or the implementation branch. Manual branch-only requests use artifacts/summary without persistent branch image publication. Existing preview bot-comment links are migrated to the new branch name; raw old-name URLs are not a compatibility contract.
- One updatable Preview comment shows exact full commit, mode/category, actual and planned counts, missing states, independent capture results, selection warnings and artifact link. Thumbnails are grouped by catalogue category, with workflow-file captures in their own group. Each category is a closed-by-default accordion with a compact inline-bold clickable title and bracketed image count (for example, **LoRA** [11]), without heading margins or “preview(s)” wording in the title; a three-column grid sits inside. The overall 48-thumbnail budget is shared round-robin across categories rather than consumed by the first filenames; category captions disclose sampling and an overall shown/total caption appears only when capped. All images remain accessible through the linked branch directory and artifact, even for ALL. Legacy bot comment markers are recognized when migrating an existing PR; historical unrelated comments are left alone.
- Image publication is best effort: failure produces an Actions warning and artifact fallback, not a false capture failure. Failed captures remain failures even if partial images publish. Superseded/draft automatic runs do not update the current PR comment or images. Preview-branch images have no time-based cleanup guarantee and are replaced on that PR's next publication; the branch is not a source of immutable historical evidence.

The comment and run summary share a compact layout: one metadata/link row (mode, linked commit, image/state counts, Run/Download/Browse), a single-line representative-coverage notice when needed, visible timeout/failure/missing-image warnings, and category accordions. Do not repeat category counts or successful capture details above the gallery. Full commit, workflow path, capture exit codes/deadlines, worker timings, retention caveat and deduplicated mapping notes sit in one closed Diagnostics section; exact complete records remain in the artifact. Only show a separate shown/total caption when the thumbnail cap actually omits images. Detailed diagnostics are collapsed, not discarded or treated as passed.

The Preview Action no longer uses Drive secrets or offers upload-format inputs. Build/release Drive uploads and their shared legacy-named helper (`tools/ci/upload_ui_renders_to_drive.py`) remain unchanged and outside this migration.

## Local commands and migration

Only run captures when the owner explicitly requests them; normal development still forbids agent tests/renders and fixture additions. Use fresh ignored output directories so old PNGs cannot masquerade as current captures.

```powershell
# Qt-free selection only:
uv run --frozen python tools/ui/preview_changes.py --base origin/main --head HEAD
# Owner-authorized capture examples (not implementation validation commands):
uv run --frozen python tools/ui/preview_states.py --category lora --fast --out tmp/ui-previews/lora
uv run --frozen python tools/ui/preview_states.py --slow --out tmp/ui-previews/all
uv run --frozen python tools/ui/preview_workflow_file.py --workflow src/dtflow/resources/workflows/reference_workflow_v1.json --out tmp/ui-previews/workflow
```

The `tools/ui/render_*` screenshot modules/configs are now `preview_*`; `capture_workflow_preview.py` is `preview_workflow_file.py`. Worker reports use `preview-manifest-…` and `preview-details-…`; default state output is `tmp/ui-previews`. Stable screenshot IDs/filenames and fixture scenarios are unchanged. `preview_lora_design.py` retains the separate historical Designer-reference captures as a local-only command, not the runtime LoRA category. Old dated plans/release evidence retain their original names/paths as historical references; use this guide for current commands. Other screenshot tools, native benchmarks and application rendering APIs are not renamed.

See [development](development.md), [tool reference](../../tools/ui/README.md), and the [implementation ledger](../plans/2026-11-13-ui-previews.md). Source/static checks do not establish an executed Preview run.

# UI render selection, progress and process orchestration

**Status (2026-11-04):** steps 1-5 below are implemented on PR #226 and the Actions `-s` run passed (456 states, 61.9s, 2-core runner). Remaining: Actions confirmation of 4b/5b/5c and phase 6, and the acceptance gates. Follow-up to merged PR #225 (`30f84282c1cc641beed333021896f4b531c14391`).

## Goal and boundaries

Make local and Actions rendering use one selection/orchestration path, generate only relevant offline fixture screenshots, and report progress clearly. Keep rendering a human-review tool without layout/state assertions. Preserve the network guard, fake providers and isolated temporary state; no real credentials, uploads, model downloads or generation to validate rendering. Screenshots do not establish native Windows/DPI acceptance.

The owner reported 456 screenshots in 286 seconds after PR #225's fixture repairs. This is one source-run observation, not a benchmark. Its older failed UI Action reached Google Drive upload and failed with HTTP 403 storageQuotaExceeded; do not attribute that to rendering or delete Drive files as part of this work.

## Agreed design

### Selection before work

- Introduce an authoritative state/group registry with category, subcategory, stable state ID, fixture dependency group and display order. Keep existing filenames stable; their historical numeric suffixes are not run positions.
- Support listing states and selecting categories, subcategories, explicit states and prefixes; reject unknown explicit selectors clearly.
- Resolve selection before creating windows, fixture databases or expensive images. Do not render unwanted screenshots and delete them afterward.
- Preserve prerequisite setup/transitions within a selected fixture group while saving only selected states. Do not assume each screenshot can run independently.
- Distinguish intentional empty selection from an inability to determine selection safely.

### Progress and diagnostics

- Report each screenshot immediately after saving, including Scripts; do not wait until its whole batch returns.
- Parent output includes category/subcategory, completed/selected count, state ID and per-state elapsed duration. Keep total wall time distinct from summed worker durations.
- Retain deterministic manifest/display ordering even when completion order differs across workers; label concurrent messages so users can distinguish them.
- Default output is concise; verbose mode retains detailed timings and geometry diagnostics. Save detailed worker logs and a machine-readable manifest with selected/saved/missing states, reasons, timings, warnings and failures.
- Count/summarize narrowly identified offscreen platform warnings and explicitly labelled expected fixture failures. Preserve unexpected warnings, exceptions, save failures, duplicate names, blank screenshots and fixture-wait timeouts. Never globally disable warnings or change production logging policy.
- Maintain the non-terminating diagnostic stack watchdog separately from terminating worker and Actions timeouts. Document what each measures.

### Run levels

- Each catalog state gets a `level`: `f` (fast), `m` (medium) or `s` (slow, everything). Levels are nested: `-m` includes `f`, `-s` includes both.
- `-f` is the default: one or two key states per fixture group so every screen is eyeballed, targeting about 60 states and about one minute. `-m` adds common variants and drops the slow pager, trash and refresh-recovery waits (about 250 states). `-s` renders all 456 states.
- Levels combine with category/subcategory/state selectors (`--category queues -m`) and are applied during selection, before any window or fixture is built. A shard whose states are all filtered out is never started.
- **Behaviour change:** a bare run no longer renders everything. Actions, release checks and any "all states" use pass `-s` explicitly. Prerequisite setup inside a fixture group still runs even when only a fast state is selected.
- Djé reviews the first f/m split by eye; the proposal is drawn from per-state durations and category coverage in a full manifest.

### Measured baseline (four full-run manifests, per-state capture seconds)

- Everything except Scripts totals about 230s in every run, so window order is not the cost. Scripts cost 46s when run first and 95s when run last (594s run: 115s). Closed windows and timers surviving into the Scripts window is the working explanation; commit 932bd04 frees windows and is unmeasured.
- Per fixture group (latest run): gallery-composition 57s, image windows and splash 43s, active-queue/item-table/queue-template 32s, main window queue/shell/inpaint/logs 34s, connection 25s, generation-flow 23s, generate-configuration 17s, Scripts 46-95s.
- 373 of 399 non-Scripts states take 0.36s or less (144s in total); 15 states over 2s take 70s, mostly gallery pager/buffer, trash and refresh-recovery waits.
- Per-process startup cost is not yet recorded; the first implementation step adds a "ready after Ns" line per shard.

### Shared process orchestration

- Shard by process, not by thread or window: Qt widgets need one `QApplication` main thread, so parallelism is several `render_states.py` workers, each with its own windows, temp state directory, in-memory credentials, fake cloud and gate. This also removes the shared `cloud`/`scenario`/`gate` closure coupling between windows.
- A shard is a window group: (1) main window generation flow (`generate`, `images-01`, `images-28..30`, `queues-01`; serial); (2) main window queue, shell, inpaint, logs; (3) workspace connection (changes credentials, one ordered process); (4) workspace generate-configuration; (5) workspace queues read-views; (6) workspace gallery-composition, split in two if its states are independent; (7) splash and the one-shot image windows; (8) Scripts. Whether workspace fixtures (3-6) depend on credentials or queues left by an earlier one is unverified and must be read from the code before splitting.
- The shard map lives in the catalog (a `shard` field next to `fixture`) so selection, levels and orchestration read one source. Wall time becomes the slowest shard (gallery-composition, about 57s) instead of the sum, provided cores are free.
- A small launcher reads the catalog, plans shards after level/selector filtering, starts them longest first as subprocesses with `--jobs` workers (auto = cores minus one, capped at 4; 1 keeps today's single-process behaviour), gives each its own `--report-key`, output staging and temp state, then merges the manifests and prints the final summary in journey order. Console lines stay labelled `[category/sub] n/N id duration` as completion happens, with the shard name added when several run.
- Every run goes through the launcher, even with one worker (`-j 1` or a single-group selection), so the stall timeout covers it; workers never spawn workers.
- Offscreen remains the default platform, including on Windows, so concurrent workers do not fight for focus.
- Parent handles worker crashes/timeouts, cancellation, partial manifests and missing outputs; a failed worker is never reported as a completed category. Preflight disjoint final filenames. Replace Actions' separate xargs orchestration with the same entry point and reconcile its two-minute step deadline with the 300-second subprocess deadline.
- Parallel gain depends on the core count of the machine, which is unknown for the owner's Windows PC; do not promise a speedup. Within a shard, trim fixed `wait_for` settle delays on the 15 slow states and skip the generation flow when only `queues-01` is selected.

### Change-based Actions selection

- **Owner refinement / file-level implementation (2026-11-04):** select specific component/variant representatives instead of whole-category expansion, deduplicating identical shared presentation while retaining distinct mounting/layout/interaction samples. The [coverage inventory and approved policy](2026-11-04-ui-render-coverage-inventory.md) record current consumers, selected state IDs and gaps; source implementation is complete for the simplified file-level scope, with final-head Actions and owner visual acceptance pending. `render` requests mapped selection; adding `render-all` immediately requests exhaustive rendering independently of `render`. Unlabelled PRs do not render by default.
- Maintain a tracked explicit path-to-render-group dependency map, including shared dependencies and rationale; do not infer dependencies solely from filenames.
- Compare the PR head with its merge base against the target branch, not just the latest commit. Account for renamed/deleted paths and changed maps/registry/workflow code.
- Examples: Scripts controller/forms select Scripts; gallery detail selects Detail plus navigation; refresh/loading selects recovery/loading; usage-panel changes select Connection usage and shared activity counter states.
- Shared styles/tokens/main-window changes select identifiable affected component representatives first; broad or uncertain shared impact uses a small cross-shape pack. Unmapped impact uses affected-page representatives, or the shared pack when the page is unknown, with a prominent coverage-incomplete warning and explicit reason. Missing history or selection errors must expose uncertainty rather than claim complete coverage. This approved follow-up policy supersedes the earlier automatic full-render fallback; current implementation is still category-based.
- Docs-only changes may skip. Treat fixture assets, forms, resources, renderer helpers and dependency/configuration changes explicitly rather than classifying them as documentation.
- Print changed paths, selected groups and mapping reasons in the Action summary. Expose manual selection and a force-all override; preserve a full-render path independent of automatic selection.
- Separate render success/artifacts from optional external publication. Keep Drive quota failures visible; storage cleanup and upload-policy changes require separate scope.

## Implementation sequence

1. Done: registry, selection, immediate reporting, single-pass run, window phases (in-process sequencing superseded by step 3).
2. Done: `level` field and `-f/-m/-s` flags (default `-f`, 17 states; `-m` about 245; `-s` all 456).
3. Done: launcher (`render_launcher.py`) with `-j` worker processes, merged manifest, per-state `worker`, `workers` block, `wait_timeouts`, kill on Ctrl+C, failure on worker exit, blank, missing or duplicate states. Windows PC: `-f` 23s, `-s` 141s.
4. Done: worker groups workspace, gallery, main, scripts, images-windows, splash, packed by a measured `WORKER_COST` table.
   - 4b. Done (81c5663, awaiting an Actions run): the workspace group is split further (connection, generate-configuration, queues read-views). Only worth it with more than 2 cores; first read the code to confirm the fixtures do not depend on credentials or queues left by an earlier one. Re-measure `-m` and `-s` with `-j 3/4` on a 4-core machine.
5. Done: Actions runs the launcher once with `-s`; Summarize renders reads the merged manifest; the 100ms settle wait is gone.
   - 5b. Done for `images-102` only (wait 8s to 2s); other slow states untouched until their cause is measured: trim the slow fixed waits (`images-102` 8s timeout on Windows, `images-03`, `images-02`, `images-18`), only after the owner approves lowering the timeout.
   - 5c. Done: `--stall-timeout` (default 180s) and a 6 minute Actions step limit above the 300s deadline; the stall kill itself has not been triggered yet: worker timeout and one deadline for local runs and Actions (step `timeout-minutes: 2` vs the 300s subprocess deadline); keep `WORKER_COST` in step with catalog changes.
6. Implemented category selection in #226; #227 implements representative selection with 30 file-level rules and 37 named packs, awaiting final-head Actions acceptance. `render_changes.py` diffs against the merge base; `render_coverage.json` provides component path rules and named exact-state packs, with `render_dependencies.json` providing fallback page ownership. Unmapped impact chooses page/shared samples with coverage-incomplete warnings; missing history samples the shared pack, while invalid planner metadata fails visibly. Summaries list packs, distinct planned states and fixture setup separately. `render` requests mapped selection; `render-all` independently triggers exhaustive selection. Docs/non-render changes retain `-f` smoke, manual category dispatch defaults to fast, and manual all-category dispatch without changed-only stays exhaustive. Owner-approved simplification: focused files map to component packs, page files to small populated page packs, broad shared files to the eight-state shared pack. Automatic function/object/QSS-selector diff analysis is out of scope; refine specific file mappings only when repeated unnecessary renders justify it. Drive upload stays separate.
7. Done: docs (`tools/ui/README.md`, `AGENTS.md`, development guide). Extend them with any later subphase.

## Verification gates, not results

Use static syntax/configuration checks and bounded offline fixtures for selection, prerequisite retention, ordering, filename collisions, worker failure/cancellation, warning classification and diff fallback. Extend existing contract coverage where appropriate rather than adding one test per screenshot. Run renders only when requested. Full screenshot parity and target-Windows process behavior remain separate acceptance gates; do not claim speedup from process count alone.

## Pending implementation decisions

Open: the first `f`/`m` state split (owner review by eye); `shard` names and whether gallery-composition splits; worker event transport (stdout lines vs per-shard manifest files); default deadlines; exact changed-path mappings. The design introduces no new production workflow engine.

# UI render selection, progress and process orchestration

**Status:** Approved direction; implementation pending. Follow-up to merged PR #225 (`30f84282c1cc641beed333021896f4b531c14391`). This initial draft PR records scope, not delivered runtime behavior.

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

### Shared process orchestration

- Keep the existing renderer as an internal single-worker execution path; a parent orchestrates isolated Qt child processes and merges progress/results.
- Default to at most two workers locally, with a configurable limit; Actions can explicitly select three. Performance must be measured rather than promised.
- Group work by isolated category/fixture ownership, not arbitrary individual states. Avoid unnecessary processes for one small selection and prevent recursive worker spawning.
- Give each worker isolated temporary application state, logs and output staging. Preflight disjoint final filenames; avoid shared mutable fixture state and simultaneous writes to the same output.
- Parent handles worker crashes/timeouts, cancellation and draining, partial manifests, missing outputs and stable final summaries. A failed worker must not be reported as a completed category.
- Replace Actions' separate xargs orchestration with the shared entry point. Reconcile the current two-minute step deadline with its 300-second subprocess deadline; leave cleanup/reporting time beyond worker deadlines.

### Change-based Actions selection

- Maintain a tracked explicit path-to-render-group dependency map, including shared dependencies and rationale; do not infer dependencies solely from filenames.
- Compare the PR head with its merge base against the target branch, not just the latest commit. Account for renamed/deleted paths and changed maps/registry/workflow code.
- Examples: Scripts controller/forms select Scripts; gallery detail selects Detail plus navigation; refresh/loading selects recovery/loading; usage-panel changes select Connection usage and shared activity counter states.
- Shared styles/tokens/main-window changes conservatively expand affected coverage, up to all states. Unmapped relevant application/UI files, missing base history or selection errors fall back to full rendering with an explicit reason.
- Docs-only changes may skip. Treat fixture assets, forms, resources, renderer helpers and dependency/configuration changes explicitly rather than classifying them as documentation.
- Print changed paths, selected groups and mapping reasons in the Action summary. Expose manual selection and a force-all override; preserve a full-render path independent of automatic selection.
- Separate render success/artifacts from optional external publication. Keep Drive quota failures visible; storage cleanup and upload-policy changes require separate scope.

## Implementation sequence

1. Inventory all registered/implicit states and fixture dependencies; establish the registry and listing/selection without changing screenshot content.
2. Make selection gate fixture construction and saving, and make immediate progress/manifest reporting consistent across existing groups.
3. Add bounded parent/worker orchestration and lifecycle handling; verify parity with sequential rendering.
4. Add conservative diff mapping, explainable selection and overrides; migrate Actions to the shared orchestrator and align deadlines.
5. Update `tools/ui/README.md` and the development guide with actual commands and evidence; update release/status notes only when implementation changes their scope.

## Verification gates, not results

Use static syntax/configuration checks and bounded offline fixtures for selection, prerequisite retention, ordering, filename collisions, worker failure/cancellation, warning classification and diff fallback. Extend existing contract coverage where appropriate rather than adding one test per screenshot. Run renders only when requested. Full screenshot parity and target-Windows process behavior remain separate acceptance gates; do not claim speedup from process count alone.

## Pending implementation decisions

Finalize registry location/schema, group granularity, actual CLI spellings, worker event transport, default worker deadlines, manifest/log retention and exact changed-path mappings after inspecting current code. The design above does not introduce any executable API or new production workflow engine.

# Plan: Phase 8D cache-management completion

## Scope
- In: complete the existing disposable-cache management contract after PR #55; coordinate cleanup with active cache readers/writers, including image export; preserve durable state and outputs; refresh category documentation and record acceptance boundaries.
- Out: original-image deletion, multi-selection/8E organisation, automatic regeneration/retry, new providers, automatic retention policies, version bump, packaging and release.

## Action items
- [x] Squash-merge owner-approved PR #55 and branch from updated main.
- [x] Write and commit this plan before runtime edits; open a dedicated draft PR.
- [x] Audit all inventory/derived cache readers and writers, cleanup entry points and no-cache behavior; define one shared coordination policy rather than a UI-only export guard.
- [x] Implement bounded safe category cleanup and activity coordination, including concurrent starts and cleanup failures, without touching durable paths or exported copies.
- [x] Reconcile inventory/thumbnails/derived documentation: gallery thumbnails are currently in-memory; PR #55 writes derived/image_analysis.json.
- [x] Review existing authoritative preservation/category coverage and record remaining validation scenarios without duplicating tests across layers.
- [x] Run repo-defined static checks, synchronize status/architecture/desktop/unreleased documentation, and commit/push coherent checkpoints.
- [ ] Owner final tests and cache-management smoke acceptance.

## Decisions and Design Changes
- 2026-10-09: PR #55 squash-merged as bca51e60a3e363b3c279e473020ca4b698a79d18. This follow-up is separate from completed export delivery.
- Existing cache_usage/purge_cache services own only cache/inventory, cache/thumbnails and cache/derived. Outputs, attempts, queues, credentials, assets, presets, export preferences and user export folders remain outside purge ownership.
- Audit found inventory/raw-LoRA persistence and source-analysis persistence as disk cache users; gallery thumbnails are in-memory. Shared nonblocking reentrant thread/OS cache ownership now serializes actual file I/O and purge across participating processes. Export holds the computation/checkpoint interval; unavailable ownership falls back to uncached export. Network refresh remains outside disk ownership and can repopulate cache after cleanup.
- Missing cache categories remain valid; disposable data can be rebuilt. --no-cache disables persistent cache reads/writes, not durable convenience state.
- Code presence is not runtime acceptance. Older status text claiming inventory is the only disk cache is superseded by the source-only image computation cache.

## Open questions
- None blocking for 8D. 8E deletion/recovery and selection policies remain separate pre-implementation decisions.

## Validation
- Runtime implementation and docs are present; owner acceptance is pending. Cache-only control file remains outside purge categories; bounded preflight rejects redirected roots/categories/nested paths before deleting any selected tree. Partial failures report completed/failing categories and refresh sizes.
- Under fast-implement, no new tests or agent test/smoke execution. Final validation remains with the owner unless explicitly requested otherwise.
- Acceptance must cover category measurement/purge, durable-output/state preservation, active export/inventory/cache activity, concurrent starts, failures, cache recreation and no-cache behavior. Offline temporary fixtures only; no real generation is needed.
- No inference, training, billable actions, packaging or release.

## Validation results
- uv sync succeeded after the owner closed the running app; no dependency changes.
- Whole-repository Ruff check/format passed; uv lock check, syntax/import checks, CLI/gui help, Designer XML and Git whitespace checks passed.
- No new tests, unit/integration tests, export invocation, QApplication launch or agent smoke. Source inspection found no existing direct cache_usage/purge_cache contract tests; inventory cache contracts exist. Preservation/contention/path-safety scenario coverage remains owner validation under fast-implement, not claimed from static checks.
- 8E Images multi-selection/bulk deletion added as a planned-only document and linked from initial/sequencing plans; no destructive image code added.

## Owner smoke checklist
1. Launch normally. Connection shows Inventory/Thumbnails/Derived sizes and file counts; an empty disk thumbnail category is expected.
2. Export saved images to a fresh scratch folder to populate Derived. Cancel a Derived cleanup confirmation: no files/counts should change. Confirm Derived cleanup: it clears derived/image_analysis.json without changing original images, exported copies, history, presets or preferences.
3. Re-export into that same folder: existing images are skipped even with Derived empty. Export into another fresh folder: Derived is rebuilt and measured again.
4. During a long export, try Clear derived/all: cleanup is refused as busy. Cancel/finish export, then cleanup should work and completed copies should remain. A second participating app must not purge while the first owns cache operations; it may show cache busy.
5. Clear Inventory, then explicitly Refresh inventory: disk cache is rebuilt without generation. Existing in-memory choices may stay visible; cleanup is not a presentation reset. Clear all cache and verify durable state/images/export copies remain untouched.
6. Close the app, launch with uv run dtflow gui --no-cache, and export to a fresh folder: inventory/derived files must not be created or rewritten. Existing cache/control files may remain. Exit and relaunch normally to restore disk caching.
7. Isolated fixture validation (never manipulate real state): redirected root/category/nested junctions, invalid category names, traversal bound, concurrent-start races and permission/partial-deletion errors. Confirm no durable target is touched and errors allow retry; these are not agent-validated by the static checks above.

## Owner test feedback and startup contention fix
- Owner ran the offline suite at 9397d03: 425 passed, 3 skipped, one failure in test_cache_prefills_the_model_list_and_a_bypass_ignores_it (cached model not restored within three seconds).
- Source inspection identified transient ownership contention between startup cache measurement and simultaneous persistence reads: immediate busy-to-miss handling can discard the current valid observation. Persistence transactions now wait at most 0.5 seconds, including OS-lock contention; purge, measurement and export acquisition stay nonblocking. Owner rerun is required; no agent test execution or new tests.

# Plan: Phase 8D cache-management completion

## Scope
- In: complete the existing disposable-cache management contract after PR #55; coordinate cleanup with active cache readers/writers, including image export; preserve durable state and outputs; refresh category documentation and record acceptance boundaries.
- Out: original-image deletion, multi-selection/8E organisation, automatic regeneration/retry, new providers, automatic retention policies, version bump, packaging and release.

## Action items
- [x] Squash-merge owner-approved PR #55 and branch from updated main.
- [x] Write and commit this plan before runtime edits; open a dedicated draft PR.
- [ ] Audit all inventory/derived cache readers and writers, cleanup entry points and no-cache behavior; define one shared coordination policy rather than a UI-only export guard.
- [ ] Implement bounded safe category cleanup and activity coordination, including concurrent starts and cleanup failures, without touching durable paths or exported copies.
- [ ] Reconcile inventory/thumbnails/derived documentation: gallery thumbnails are currently in-memory; PR #55 writes derived/image_analysis.json.
- [ ] Review existing authoritative preservation/category coverage and record remaining validation scenarios without duplicating tests across layers.
- [ ] Run repo-defined static checks, synchronize status/architecture/desktop/unreleased documentation, and commit/push coherent checkpoints.
- [ ] Owner final tests and cache-management smoke acceptance.

## Decisions and Design Changes
- 2026-10-09: PR #55 squash-merged as bca51e60a3e363b3c279e473020ca4b698a79d18. This follow-up is separate from completed export delivery.
- Existing cache_usage/purge_cache services own only cache/inventory, cache/thumbnails and cache/derived. Outputs, attempts, queues, credentials, assets, presets, export preferences and user export folders remain outside purge ownership.
- Current Qt cleanup blocks generation/dispatch but does not coordinate image export or all asynchronous inventory/cache operations. Audit both service-level and presentation boundaries before selecting synchronization mechanics.
- Missing cache categories remain valid; disposable data can be rebuilt. --no-cache disables persistent cache reads/writes, not durable convenience state.
- Code presence is not runtime acceptance. Older status text claiming inventory is the only disk cache is superseded by the source-only image computation cache.

## Open questions
- No blocking product questions. Synchronization and filesystem-safety details will be recorded after the implementation audit.

## Validation
- This initial PR is plan-only; no runtime behavior changed.
- Under fast-implement, no new tests or agent test/smoke execution. Final validation remains with the owner unless explicitly requested otherwise.
- Acceptance must cover category measurement/purge, durable-output/state preservation, active export/inventory/cache activity, concurrent starts, failures, cache recreation and no-cache behavior. Offline temporary fixtures only; no real generation is needed.
- No inference, training, billable actions, packaging or release.

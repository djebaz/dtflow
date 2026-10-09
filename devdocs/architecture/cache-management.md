# Disposable-cache management

## Ownership

`services/cache_management.py` measures/purges only `<state>/cache/inventory`, `thumbnails` and `derived`. Inventory snapshots and source image computations currently write disk cache; gallery thumbnails are in-memory. Missing categories are valid and are not created by measurement/purge.

Outputs, attempts/queues, credentials, assets, presets, settings, export preferences, organisation metadata, logs, retained evidence and user export folders are never purge targets. This is not image deletion or an automatic retention policy.

## Coordination

`persistence/cache_coordination.py` provides a nonblocking reentrant thread lease plus an OS file lock at `<state>/cache.lock` shared across processes. The control file is outside purge categories, may remain after release, and is not a stale-ownership flag: only the live OS lock means busy. Do not delete it to force access. Process exit releases ownership; durable generation/dispatch ownership remains separate.

Inventory/LoRA cache writes, inventory reads, source-analysis reads/writes, measurement and purge use the same lease. Background persistence calls wait at most 0.5 seconds for short overlapping I/O (including startup measurement/read requests), avoiding false cache misses from transient contention. Measurement, purge and export acquisition remain immediate/nonblocking; the GUI never waits for persistence ownership. Reads treat contention/unavailable cache as misses; optional writes retain their existing best-effort callers. Export holds ownership across cache load/computation/checkpoints to prevent resurrecting a pre-purge computation snapshot. If it cannot acquire ownership, it exports without persistent cache and reports that fallback. `--no-cache` bypasses cache reads/writes and export ownership; explicit user-requested cleanup/measurement still use coordination.

Connection remeasures sizes asynchronously after inventory refresh, generation settlement, export completion and when the workspace is reopened; superseded measurements are ignored. The Connection UI also blocks cleanup during its active generation, queue dispatch or image export. Service-level coordination covers concurrent starts and other participating processes, not just this precheck. Network inventory refresh is not held inside the disk lease: an observation completed after cleanup can legitimately recreate inventory cache. Cleanup is not a caching-disable switch or an in-memory gallery/inventory reset.

## Safety and failures

Selected category names and every selected tree are preflighted before deletion. Cache root/category/file links and Windows reparse points (including junctions) are refused rather than followed. Measurement/purge traversal is bounded to 100,000 entries per category; invalid or unreadable trees report failure, not a misleading zero size. Operations resolve the application state root and never enumerate its durable siblings.

Permission or I/O failures during removal can leave partial cleanup. The service reports completed categories and the failing category; the UI reports the error and refreshes sizes. A retry removes only remaining disposable entries. Completed export copies survive cache failures. Coordination is for cooperating DTFlow processes, not protection against an external program maliciously replacing filesystem paths during deletion.

## Acceptance

Code implementation does not establish owner/runtime or packaged acceptance. Under fast-implement no new tests or agent test/smoke execution was performed. See the [8D plan and smoke checklist](../plans/2026-10-09-phase-8d-cache-completion.md). Offline temporary directories suffice for preservation/contention/path-safety validation; no generation is required.

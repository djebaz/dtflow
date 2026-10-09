# Plan: Phase 8E saved-image selection and bulk deletion

## Status and scope
Planned, not implemented or part of Phase 8D completion. The owner selects multi-image selection and bulk deletion from the existing 8E organisation candidates. Favourites, ratings, tags, notes and collections remain unapproved separate scope.

- In: multi-selection in the Images tab; bulk deletion of explicitly selected saved images or all saved images matching the current gallery filter across all pages.
- Out: exported-copy deletion, provider-side deletion, regeneration/replay, deleting unrelated images from the same attempt, broad filesystem cleanup, and changes to cache-cleanup ownership.

## Proposed behavior
- Identify selections by stable saved-output identity, not visible card positions or filename prefixes. Clearly distinguish the active preview image from the multi-selection set.
- Support per-image toggle, Select all on this page and Clear selection, plus an explicit all-filtered-results action. Show selection counts and define persistence across paging, filters, refresh and view changes before UI implementation.
- Capture a bounded deletion target snapshot through shared services. Require explicit confirmation showing the affected image count and whether the scope is selected images or all matching results across pages; cancellation before confirmation deletes nothing.
- Delete original generated image files only. Preserve exported copies, unrelated outputs, request/attempt/queue audit history and shared metadata needed by retained outputs. Never mutate a generation into success/failure or submit a replacement request.
- Reconcile durable artifact availability and Images/history references so deleted images do not reappear as normal available results. Missing/deleted outputs must remain distinguishable from never-produced outputs.
- Design a resumable/inspectable deletion record and per-item reconciliation for filesystem/database partial failure before implementation; do not claim that file deletion and SQLite commit form one atomic transaction.
- Run work off the Qt event loop, report deleted/already-missing/failed/unprocessed counts, and reconcile only affected cards/thumbnails/preview state. Coordinate with active export/readers and prevent accidental deletion of outputs still being written.
- Preserve provenance and prompt privacy; diagnostics should not expose raw prompts or private metadata.

## Action items
- [ ] Confirm deletion policy (permanent deletion versus Windows Recycle Bin), confirmation UX, selection lifetime and partial-failure recovery contract before runtime implementation.
- [ ] Refine shared service/persistence boundaries and durable deletion/availability schema with a supported migration policy.
- [ ] Implement Images multi-selection through canonical Designer/shared widgets without changing single-image preview/reuse semantics.
- [ ] Implement selected/all-filtered snapshot deletion and reconciliation with explicit safety bounds.
- [ ] Extend authoritative offline selection/deletion contracts as separately authorized; validate preservation, cancellation and interrupted recovery without live generation.
- [ ] Owner UI/runtime acceptance and relevant documentation/release-note synchronization.

## Acceptance direction
Use temporary offline artifacts: cross-page selection/filter scope, stable selection after refresh, confirmation counts, exported-copy and unrelated-output preservation, retained audit history, missing files, permissions, partial deletion/restart reconciliation and active export/generation coordination. No live or billable generation is required.

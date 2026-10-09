# Plan: Phase 8E saved-image selection and bulk deletion

## Status and scope
In-progress draft implementation work, not a shipped/owner-accepted feature or part of Phase 8D completion. Selection, gallery controls and checked export handoff exist in source; durable recycling and original-file coordination now exist in source, with owner runtime/recovery acceptance pending. The [Qt system Trash follow-up](2026-10-09-qt-system-trash.md) replaces Windows-only adapter/UI guards with platform-aware Qt behavior; it does not certify the whole app on Debian/macOS. See the [approved implementation subplan/task list](2026-10-09-phase-8e-implementation.md) and [owner UX reference](examples/phase-8e/owner-multiselect-ux-spec.md). The owner selects multi-image selection and bulk deletion from the existing 8E organisation candidates. Favourites, ratings, tags, notes and collections remain unapproved separate scope.

- In: multi-selection in the Images tab; checked-image export from both Images and Connection; Windows Recycle Bin actions for explicitly selected saved images, including a bounded, explicitly selected snapshot of images matching the current gallery filter across pages.
- Out: exported-copy deletion, provider-side deletion, regeneration/replay, deleting unrelated images from the same attempt, broad filesystem cleanup, and changes to cache-cleanup ownership.

## Proposed behavior
- Identify selections by stable saved-output identity, not visible card positions or filename prefixes. Clearly distinguish the active preview image from the multi-selection set.
- Support per-image toggle, Select page, Clear selection, and explicit Select all filtered. Keep the selection/action toolbar on **one row** at all widths (secondary actions move into overflow, never onto a second row). Its short gallery action labels are **Export** and **Delete**; Delete opens the fully worded Recycle Bin confirmation. Keep checks across pages, view/tab changes, sorting, filtering and refresh within the session; show total checks and checks hidden by the active filter. Select all filtered adds matching IDs in deterministic query order, **stopping at 10,000 total checked IDs**, retaining pre-existing checks and reporting how many results were not added; cancellation leaves selection unchanged.
- Capture a bounded exact-ID deletion target snapshot through shared services, not a live implicit all-filtered query. Require a confirmation for **every** move to Windows Recycle Bin with the eligible image count, hidden checked count and ineligible/missing count. Explain normal restoration through Windows without claiming a DTFlow Undo. Cancel is the default and changes nothing.
- Move only eligible original generated image files to **Windows Recycle Bin**, with no permanent-delete fallback. Preserve exported copies, unrelated outputs, request/attempt/queue audit history and shared metadata needed by retained outputs. Never mutate a generation into success/failure or submit a replacement request.
- Reconcile durable artifact availability and Images/history references so deleted images do not reappear as normal available results. Missing/deleted outputs must remain distinguishable from never-produced outputs.
- Design a resumable/inspectable deletion record and per-item reconciliation for filesystem/database partial failure before implementation; do not claim that file deletion and SQLite commit form one atomic transaction.
- Run work off the Qt event loop, report deleted/already-missing/failed/unprocessed counts, and reconcile only affected cards/thumbnails/preview state. Coordinate with active export/readers and prevent accidental deletion of outputs still being written.
- Preserve provenance and prompt privacy; diagnostics should not expose raw prompts or private metadata.

## Action items
- [x] Owner approved Windows Recycle Bin only (no permanent-delete fallback), clear confirmation for each move, a single-row selection toolbar with **Delete** as the gallery action label, and checked-image export initiated by gallery **Export** or from **Connection** through the same Connection workflow. Always-visible checkboxes, session-only cross-filter selection and the 10,000-item partial-fill rule follow the implementation subplan.
- [ ] Finalize durable recovery/eligibility and concurrency details before enabling recycling.
- [ ] Refine shared service/persistence boundaries and durable deletion/availability schema with a supported migration policy.
- [x] Implement Images multiselection, contextual Export and disabled Delete controls in one row and zero-selection Select page/Select all filtered commands through canonical Designer/shared widgets without changing single-image preview/reuse semantics. Source-only; owner visual/runtime acceptance pending.
- [ ] Implement selected/all-filtered snapshot deletion and reconciliation with explicit safety bounds.
- [ ] Extend authoritative offline selection/deletion contracts as separately authorized; validate preservation, cancellation and interrupted recovery without live generation.
- [ ] Owner UI/runtime acceptance and relevant documentation/release-note synchronization.

## Acceptance direction
Use temporary offline artifacts: cross-page selection/filter scope, stable selection after refresh, confirmation counts, exported-copy and unrelated-output preservation, retained audit history, missing files, permissions, partial deletion/restart reconciliation and active export/generation coordination. No live or billable generation is required.

# Plan: Incremental Images refresh (PR #37 follow-up)

## Scope
- In: reconcile Grid cards, List cells and Detail filmstrip by stable output identity on additions, removals, metadata changes and reordering; retain unchanged thumbnails and selected Detail image.
- Out: gallery service/schema changes, unbounded thumbnail caching, packaging, inference, version bump.

## Action items
- [x] Verify clean branch and open PR #37; inspect the existing render/decode paths.
- [x] Reconcile existing widgets/cells in place; create/remove only affected entries.
- [x] Replace page-wide thumbnail invalidation with per-output revision checks; retain unchanged in-flight work.
- [x] Preserve selected Detail pixels/zoom/pan when only other outputs or metadata change.
- [x] Sync user guide, viewer design/plan and unreleased notes.
- [ ] Run only static checks, review, commit/push and update the same PR; do not merge.

## Decisions and Design Changes
- 2026-10-06 Owner extends the original fix beyond identical pages. Preserve the completed original plan as history; this follow-up supersedes its changed-page full-render boundary.
- 2026-10-06 Keep only the bounded current page's mounted entries and thumbnail revision map. Reuse Designer filmstrip slots without deleting named widgets referenced by presentation callbacks; remove departed dynamic widgets.
- 2026-10-06 Reconcile by identity; compare complete records for metadata changes and path/size/availability for image decoding. Do not invalidate retained decodes when an unrelated item is added or reordered.
- 2026-10-06 Keep image and metadata callback revisions separate for Detail so metadata updates do not interrupt the selected image decode.
- 2026-10-06 Continue fast-implement: no new tests or test/smoke runs. No packaging is needed for this source fix.

## Open questions
- None.

## Validation
- [ ] Final tests and smoke tests deferred to user.
- Tests and smoke tests are not run; final acceptance remains with the owner.

- Static checks run: Ruff format/check on the changed controller, Python compilation/import and git diff --check. No tests or smoke tests were run.
- Source review covers additions/removals/reordering, empty-page transitions, metadata-only updates, bounded thumbnail revision ownership and stale callbacks; this is not runtime or visual acceptance.
- AGENTS.md/README.md need no new API/feature contract; detailed behavior is synchronized in DESIGN.md, desktop guide, viewer plan, current status and unreleased notes.

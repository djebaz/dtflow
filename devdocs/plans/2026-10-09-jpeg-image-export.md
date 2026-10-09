# Plan: JPEG image export

## Scope

- In: shared backend-free export service for committed saved images; readable Windows-safe filenames;
  JPEG quality 95 / 4:4:4; Explorer-visible EXIF Title, Subject, Comments and Tags;
  rich portable generation metadata; optional JSON sidecars and prompt privacy;
  single-image export from Generate/Images and bulk export of the current Images filter.
- Out: moving internal PNGs, automatic exports, storage/schema migrations, new generation,
  gallery multi-selection redesign, CLI redesign, version bump, packaging and release publication.

## Action items

- [x] Inspect clean branch, remote and open PRs; create feature/jpeg-image-export.
- [x] Write this plan before implementation.
- [ ] Commit/push the plan and open a new PR to main.
- [ ] Read canonical gallery, saved-result actions, async runner and persistence contracts.
- [ ] Declare Pillow through uv and keep pyproject.toml/uv.lock synchronized.
- [ ] Implement shared bounded export with collision-safe writes, portable metadata and per-item failures.
- [ ] Add a shared Designer export dialog and asynchronous UI controller with progress/cancel.
- [ ] Connect Generate current output and Images selected/filtered export without disturbing viewer state.
- [ ] Sync README, AGENTS, desktop guide, architecture, status, phase plan and unreleased notes.
- [ ] Run minimal static syntax/import/format checks only; commit/push implementation and update PR.

## Decisions and Design Changes

- 2026-10-09: Use the owner-accepted ignored tmp JPEG/EXIF PoC as a behavioral baseline, not production code.
- 2026-10-09: Preserve original PNGs and durable references. JPEG conversion is explicitly lossy.
- 2026-10-09: Include prompts by default with an explicit checkbox; disabling it removes prompts from
  filenames, visible EXIF, embedded JSON and sidecars. Always exclude local paths, server identities,
  credentials and prepared cloud-asset references; do not blindly copy raw sidecars.
- 2026-10-09: Keep requested and recorded effective settings separate, and unknown per-image seeds unknown.
  Use the durable saved-at UTC timestamp, labeled saved time, rather than invent generation time.
- 2026-10-09: Images currently has one selection. Export selected image and all filtered results across
  pages without adding a second selection model. Capture the query when export begins.
- 2026-10-09: Bound decoded image/metadata sizes and process images sequentially off the Qt thread.
  Cancellation stops between images; completed exports remain, originals are never touched.
- 2026-10-09: Do not install piexif. Pillow provides the accepted XP EXIF fields and full JSON UserComment.
  EXIF capacity overflow must preserve full JSON in a sidecar and report that fallback, never truncate silently.

## Open questions

None blocking. User/runtime acceptance remains separate from implementation.

## Validation

- [ ] Final tests and smoke tests deferred to user.
- No tests added or run. No smoke tests run under fast-implement.
- Earlier scratch-PoC JPEG decode, EXIF round-trip and Windows Shell-property checks belong to the PoC,
  not acceptance of production code in this PR.
- No inference/training or potentially billable provider actions are planned.
- Packaging skipped: this is a source feature PR, not a release/build request.

# Plan: JPEG image export

## Scope

- In: shared backend-free export service for committed saved images; readable Windows-safe filenames;
  JPEG quality 95 / 4:4:4; Explorer-visible EXIF Title, Subject, Comments and Tags;
  rich portable generation metadata; optional JSON sidecars and prompt privacy;
  one export hub in the middle panel of Connection for all saved images, the current Images filter/selection, or the selected Generate result.
- Out: moving internal PNGs, automatic exports, storage/schema migrations, new generation,
  gallery multi-selection redesign, CLI redesign, version bump, packaging and release publication.

## Action items

- [x] Inspect clean branch, remote and open PRs; create feature/jpeg-image-export.
- [x] Write this plan before implementation.
- [x] Commit/push the plan and open draft PR #55 to main.
- [x] Read canonical gallery, saved-result actions, async runner and persistence contracts.
- [x] Declare Pillow through uv and keep pyproject.toml/uv.lock synchronized.
- [x] Implement shared bounded export with collision-safe writes, portable metadata and per-item failures.
- [x] Add a Designer export section in Connection middle panel and an asynchronous controller with progress/cancel.
- [x] Expose read-only Generate/Images selection seams to Connection without changing those pages or viewer state.
- [x] Sync README, AGENTS, desktop guide, architecture, status, phase plan and unreleased notes.
- [x] Run minimal static syntax/import/format checks only.
- [x] Commit/push implementation and update draft PR #55.

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

- 2026-10-09: Owner clarified that export belongs in Connection middle panel. Keep Generate/Images unchanged except for read-only selection/query seams; no export dialog or new page buttons.

- 2026-10-09: Owner explicitly requires a flat folder: every JPG/JSON at the selected destination root.
- 2026-10-09: Initial uv add sync hit a running Windows dtflow.exe lock; used uv add --no-sync
  to update metadata/lock together. After the owner closed the app, uv sync completed successfully.
- 2026-10-09: Snapshot up to 10,000 committed identities before conversion (ignore gallery pagination,
  retain filters/order); reject larger selections rather than silently truncate. Per-image input limits
  are 64 MiB encoded / 32 million decoded pixels. Images are processed sequentially.
- 2026-10-09: Keep the established Connection cards inside a shared-role scroll area so export stays
  reachable without enlarging the main window. No new QSS palette, inline styles or tab-local shell.
- 2026-10-09: No CHANGELOG.md exists; use the canonical devdocs/releases/unreleased.md ledger.

## Open questions

None blocking. User/runtime acceptance remains separate from implementation.

## Validation

- [ ] Final tests and smoke tests deferred to user.
- No tests added or run by the agent. No agent smoke tests run under fast-implement.
- Static checks performed: uv lock --check; Ruff check/format on changed Python modules;
  AST syntax and import checks for nine changed/new modules; Designer XML parsing and unique
  widget-name check; dtflow --help. No QApplication creation, export invocation or GUI launch.
- Git whitespace check is corrected after removing trailing blank lines introduced by text writes.
- Dependency metadata and environment synchronized with uv; Pillow already existed transitively.
- Earlier scratch-PoC JPEG decode, EXIF round-trip and Windows Shell-property checks belong to the PoC,
  not acceptance of production code in this PR.
- No inference/training or potentially billable provider actions are planned.
- Packaging skipped: this is a source feature PR, not a release/build request.

## Owner smoke evidence

- 2026-10-09: Owner reported a smoke test using
  `C:/Users/dje/Documents/GitHub/dtflow/tmp/smoke_test_jpg_export`.
- Read-only directory listing found 345 JPGs at the root, zero subfolders and zero JSONs.
  No export/EXIF/decode check was run by the agent; exact source/executable revision and
  scenario assertions were not supplied. This is owner-reported source smoke, not packaged
  or comprehensive acceptance. See [evidence](../research/jpeg-export-smoke-2026-10-09.md).
- Final tests and remaining scenario/packaged smoke validation stay with the owner.

## Git outcomes

- Branch: `feature/jpeg-image-export`; base: `main`; PR: [#55](https://github.com/djebaz/dtflow/pull/55).
- Plan-first commit `b0eabae` was pushed before implementation and PR creation.
- Implementation/docs/owner-smoke evidence committed as `b345e4e` and pushed to origin.
- PR description updated with scope, static checks, owner smoke evidence and remaining gates.
- PR remains draft for owner final validation; not merged. No version/tag/release or packaged artifact.

## ImageDescription follow-up

- Owner-requested read-only comparison of the same source in PoC and both smoke exports found
  identical decoded pixels and identical JPEG bytes excluding EXIF. Both implementation exports
  omitted ImageDescription; the prompt was retained in Title, Comments and portable JSON.
- [x] Restore EXIF ImageDescription when prompts are included, matching the PoC's bounded
  160-character ASCII-compatible summary; omit the tag entirely for prompt-free export.
- Full Unicode prompts remain in XP fields and portable JSON; no original or existing export is rewritten.
- Final tests and smoke checks for this follow-up remain with the owner; no tests added/run and
  no new agent export/smoke invocation. Static checks only; no inference or billable actions.

## Owner follow-up smoke

- Owner reports the ImageDescription follow-up "seems ok" using `tmp/smoke3`.
- Read-only inspection: 345 JPGs at root, no subfolders/JSONs; all 345 have prompts
  enabled and matching ImageDescription summaries. No metadata read failures.
- No new export, GUI launch, pixel decode, tests or agent smoke invocation.
  Remaining privacy-off/scenario/packaged gates remain with the owner. See
  [updated evidence](../research/jpeg-export-smoke-2026-10-09.md).

## Noise/blank routing follow-up

Owner now requests a checked-by-default sorting checkbox, intentionally superseding the earlier root-only requirement for detected copies. Normal images remain at root; noise/blank and their sidecars go only in category folders. Checkbox off restores flat export. See the [routing plan](2026-10-09-export-noise-routing.md); no job-status or source changes, export/GUI acceptance pending.

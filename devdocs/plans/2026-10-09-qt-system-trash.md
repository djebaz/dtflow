# Plan: Qt-backed system Trash for Phase 8E

## Scope
- In: replace handwritten Windows deletion COM with a Qt adapter; preserve shared eligibility, durable outcomes, original-file leases and per-image stop/progress; platform-aware Windows/Linux/macOS wording.
- Out: full Debian application certification, packaging, new dependencies, schema changes, permanent deletion, tests or smoke runs. Preserve the pending List styling changes separately.

## Action items
- [x] Verify lockfile, installed Qt version and binding signatures without invoking trash operations.
- [x] Inspect version-matched Qt 6.11.2 native trash implementations.
- [x] Inject an isolated Qt adapter into the backend-neutral service.
- [x] Remove fixed-drive, filesystem-type and OS gates per owner review; use Qt capability and per-file operation results.
- [x] Sync platform-aware UI, architecture, status and release notes.
- [x] Run static lint/syntax/import checks only; prepare the adapter checkpoint for commit/push to PR #57.

## Decisions and Design Changes
- 2026-10-09: uv.lock and installed Qt report 6.11.2. The instance binding is `(self) -> bool`; the static binding returns `(bool, str)`. Use a worker-local QFile instance.
- Qt's [versioned Windows implementation](https://github.com/qt/qtbase/blob/v6.11.2/src/corelib/io/qfilesystemengine_win.cpp) requests recycling and vetoes non-recycling in PreDeleteItem; failed COM creation returns false. A nearby legacy-fallback comment is stale relative to this function's body.
- Qt's [QFile implementation](https://github.com/qt/qtbase/blob/v6.11.2/src/corelib/io/qfile.cpp) changes the instance filename to its trash location. Require a verifiable regular-file receipt and source absence; incomplete evidence remains uncertain.
- The [Unix implementation](https://github.com/qt/qtbase/blob/v6.11.2/src/corelib/io/qfilesystemengine_unix.cpp) uses Freedesktop Trash; unsupported mounts/permissions fail. Let Qt determine per-file Trash usability without a filesystem allowlist; failure remains safe and never retries automatically.
- Keep Qt imports in ui/system_trash.py, injected into services/image_recycle.py. No automatic replay, no remove/unlink fallback, no trash paths or raw Qt errors in diagnostics.

- 2026-10-09 review follow-up: remove restrictive drive/filesystem/OS policy while retaining managed-original checks. Missing or unverifiable receipts still produce durable uncertainty, but the result UI distinguishes Qt-reported success from confirmed moves. Runtime receipt relaxation remains an owner-tested decision.

## Open questions
- None blocking. Windows, Debian and macOS actual trash/restore acceptance remains owner work.

## Validation
- [x] Ruff check passed across the repo; changed Python syntax/imports and canonical Designer XML parsed; git diff --check passed. No Trash function was invoked.
- [ ] Final tests and smoke tests deferred to user. No tests added/run, no trash operation, no inference or billable action.

## Owner smoke and List indicators

Owner reports deletion smoke passed on 2026-10-09. No exact executable identity or scenario details were supplied; this is owner-reported deletion acceptance, not agent verification or full platform/interruption/restore acceptance. List indicator styling follow-up uses shared QSS/tokens for dark unchecked boxes and blue checked/partial states with explicit tick/dash glyphs; owner reports checkbox styling smoke passed on 2026-10-09 for the follow-up to ceb8e26. This does not establish high-DPI, other-platform or packaged acceptance.

# Cached Windows build and draft-release workflow

Date: 2026-10-05. Base: main `afda7ee` (published 0.3.0).

## Goal and scope

Adapt the manual build/release pattern from Splitvids into one DTFlow workflow with build-only and tagged draft-release modes. Reuse the canonical GUI builder; keep owner Windows acceptance before publication.

## Decisions and design changes

- Windows 2022 x64, managed Python 3.13.13, uv 0.11.19 and PyInstaller 6.22.3; no Python 3.14 migration, FFmpeg, UPX or Drive upload.
- Empty release tag builds the selected workflow ref to an Actions artifact. A supplied stable vX.Y.Z tag must exist, point into merged main and equal the source application version; no source override or tag creation.
- Release mode creates/attaches only to a draft. Refuse published releases and asset replacement; no automatic publication or latest change.
- Cache uv dependencies/managed Python, not .venv, build/, dist/ or stamps. Hash the complete lockfile with only DTFlow's own version normalized in an ignored fingerprint file: version-only releases share caches, actual dependency changes invalidate them. The real uv.lock is never edited by this step and sync remains locked.
- Build without dev dependencies; verify bundled identity/version, write SHA256/manifest, upload compressed EXE with ZIP compression disabled. Share the same artifact with the small draft-upload job, never rebuild there.
- Only the draft-upload job gets contents: write. Manual triggers only, no app credentials, generation or upload to DT+.

## Tasks

- [ ] Implement workflow and focused artifact verification helper.
- [ ] Synchronize building/releasing guides, repository contract, current status and pending release notes.
- [ ] Static workflow/Python/documentation checks, coherent commits and PR.

## Validation and handoff

No tests or smoke tests will be run under the fast implementation workflow; no build workflow will be dispatched during implementation. Final Windows runner execution/cache-hit timings and downloaded executable smoke remain with the owner. Cold/warm cache speed is not yet measured. Existing released 0.3.0 assets will not be replaced. No training/inference or potentially billable cloud workload is needed.

# Unreleased

## Highlights

## Added

- Manual Windows build/draft-release workflow (PR #31): one fresh canonical GUI build with locked runtime dependencies, uv/managed-Python caching across version-only bumps, bundled identity checks, checksum/manifest and a downloadable Actions artifact. Optional existing tag mode attaches only to a draft; it checks merged/tag/version provenance, refuses published releases/asset replacement and verifies downloaded uploads. Owner target-Windows smoke and explicit publication remain required. No new release/version bump, build-run acceptance or PyInstaller speedup is claimed.

## Changed

## Fixed

## Release audit

- PRs: #31.
- Scope: Since v0.3.0, opt-in cached Windows build and draft-release automation, artifact provenance/checksums and owner acceptance workflow documentation.

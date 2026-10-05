# Unreleased

## Highlights

## Added

- Manual Windows build/draft-release workflow (PR #31): one fresh canonical GUI build with locked runtime dependencies, uv/managed-Python caching across version-only bumps, bundled identity checks, checksum/manifest and a downloadable Actions artifact. Optional PR-number mode resolves the same-repository head SHA, isolates its cache and is strictly artifact-only; PR plus release tag is rejected before checkout. Optional existing tag mode attaches only to a draft; it checks merged/tag/version provenance, refuses published releases/asset replacement and verifies downloaded uploads. Owner target-Windows smoke and explicit publication remain required. No new release/version bump, build-run acceptance or PyInstaller speedup is claimed.

- Recorded the Phase 8 delivery sequence and selected read-only viewer scope, with a sample-derived Draw Things preset interchange contract and an anonymised representative configuration. Planning only; no gallery or preset runtime support is added.

- Draft PR #33 adds the canonical Detail Designer form, token-based shared QSS and an offline fixture preview for the compact header, filmstrip, metadata and inline additional/raw panels. This is a fixture-only Images tab; no saved-output query service or generation behaviour is added.

- Draft PR #33 mounts the fixture-only Detail preview in Images, preserving the shared logo/navigation in a compact strip and hiding both content rails for this page. The standalone launcher and app reuse one presentation controller. No saved-output browsing service or live operations are added.

## Changed

- Draft PR #33 compacts Connection navigation into a power-icon button with last-check status and accessible text, and reserves an Images button after Queues (disabled until mounted). No polling or extra connection requests; obsolete check completions do not recolor a changed selection.

## Fixed

- PR #33 styling pass aligns the compact navigation row, adds a token-based connected fill and removes the native checkerboard vertical-scrollbar page track. Actual guarded fixture shell captures reviewed; owner visual acceptance remains pending.

## Release audit

- PRs: #31.
- Scope: Since v0.3.0, opt-in cached Windows build and draft-release automation, artifact provenance/checksums and owner acceptance workflow documentation.


- PR #33 replaces the lighthouse Detail presentation with the approved reference
  layout, scoped gallery palette, illustrative portrait/filmstrip assets and
  presentation-only cover/fit/100%, zoom/pan, navigation and fullscreen controls.
  Leaving Images restores the other workspaces' rails and splitter widths.

- Images aligns logo/view controls and navigation/filters on the same two header
  rows. Its bottom metadata, prompt and additional/raw panels span the full width
  beneath the image and filmstrip. No tests added or run for this layout follow-up.

- Images adds fixture Grid and compact List views beneath the same Detail header,
  with uniform aspect-fit tiles, metadata columns, row hover, density control and
  click-to-Detail navigation. No checkboxes, multi-selection or real gallery
  service integration is added. No tests added or run for this follow-up.

- Fix the Images controller's shutdown event filter accessing an already-deleted
  QTableWidget. Cache viewport identities, ignore unrelated teardown events,
  guard deleted Qt objects and own deferred layout timers under the controller.
  No tests added or run; Windows close behavior awaits owner confirmation.

- Images restores the shared charcoal palette and muted blue selection states
  across Detail, Grid and List, including common sliders and scrollbars.
  Removes duplicate gallery color tokens while preserving the current compact
  header, full-width lower panels and presentation behavior. No tests added or run.

- Images removes extra outer/header padding and anchors shared logo/navigation
  slots outside the scrolling controls with shell dimensions and row spacing.
  Tightens image, filmstrip and metadata gutters while retaining the current
  full-width gallery composition. No tests added or run.

# Unreleased

## Highlights

- PR #33 adds an offline Images presentation and consolidates shared styling across Connection, Generate, Queues and Images. Saved-output gallery services remain outside this PR; no release/version bump is introduced.

## Added

- Manual Windows build/draft-release workflow (PR #31): one fresh canonical GUI build with locked runtime dependencies, uv/managed-Python caching across version-only bumps, bundled identity checks, checksum/manifest and a downloadable Actions artifact. Optional PR-number mode resolves the same-repository head SHA, isolates its cache and is strictly artifact-only; PR plus release tag is rejected before checkout. Optional existing tag mode attaches only to a draft; it checks merged/tag/version provenance, refuses published releases/asset replacement and verifies downloaded uploads. Owner target-Windows smoke and explicit publication remain required. No new release/version bump, build-run acceptance or PyInstaller speedup is claimed.

- Recorded the Phase 8 delivery sequence and selected read-only viewer scope, with a sample-derived Draw Things preset interchange contract and an anonymised representative configuration. Planning only; no gallery or preset runtime support is added.

- PR #33 adds canonical fixture Grid/List/Detail views and an offline launcher using the same gallery controller as the application. Grid uses uniform aspect-fit tiles; List has metadata columns, row-wide hover and density control. Activation opens Detail, with mounted browse scroll positions preserved. No bulk selection or real-output query is added.
- Detail offers Cover/Fit, 100%, zoom/pan, previous/next, keyboard navigation and fullscreen/Escape. Bundled portrait/reference assets and synthetic metadata remain explicitly illustrative; LoRA compatibility remains Unknown. Search/filters, refresh and saved-file actions remain disabled.

## Changed

- Images hides the content rails and reuses the logo/navigation in two aligned header rows outside the scrolling controls. Leaving Images restores chrome, splitter widths and Inspector preference. No extra outer/header padding or sample-count pill; search/filter labels are replaced by explicit placeholders/defaults and accessible names/tooltips.
- Detail's right filmstrip spans the content height to the workspace bottom. Metadata, prompt and Additional/Raw panels remain beneath the main image to its left; image overlays have a 10px interior inset. Additional/Raw panels are mutually exclusive.
- Shared charcoal tokens and muted blue states replace the superseded gallery palette. Repeated neutral-control colors use named tokens; obsolete/repeated QSS declarations are removed. No separate gallery token family remains.
- Connection, Generate and Queues use section cards with compact icon/title headers and separate content regions while preserving QGroupBox identities, accessibility and business-control wiring. Shared caption/value/supporting/section/identifier/code typography, panel headings and information surfaces establish a consistent hierarchy across all tabs.
- Unknown/availability text uses neutral secondary gray, compact regular weight and explicit wording. Gallery model identity uses an accent glyph tile; LoRA names, weights and Unknown badges are distinct compact elements. The prompt has no redundant visible caption; expanded disclosure controls use shared accent states.
- Connection navigation is a compact power-icon button with token-colored last-explicit-check status and accessible text. Images is enabled after Queues. No polling or new connection requests; obsolete completions do not recolor a changed provider/endpoint selection.

## Fixed

- Restores the Queues batch-plan card toggle after the section-card header covered its native checkbox. The covered native indicator is explicitly suppressed. A visible keyboard-accessible header checkbox synchronizes with the existing group state; collapsed contents hide without disabling the custom toggle. Planning/queue semantics remain unchanged.

- Prevents the gallery shutdown event filter from accessing deleted QTableWidget objects by caching viewport identities, guarding Qt lifetime and parenting deferred layout timers. The owner confirmed the closing error is resolved.
- Removes the native checkerboard vertical-scrollbar page track and corrects gallery-preview import ordering for Ruff I001.

## Validation and release audit

- Pending scope: PR #31 build/draft automation and Phase 8 planning; draft PR #33 offline UI presentation and shared styling. No real gallery service completion or executable release acceptance is claimed.
- The owner reported tests passed on 2026-10-06 after the import fix; no command/count or CI run was supplied. The assistant did not rerun tests. Earlier bounded checks and limitations are recorded in the [preview plan](../plans/2026-10-05-phase-8a-ui-preview.md).
- This follow-up reviews source/documentation alignment only. Exact native-DPI and packaged Windows visual acceptance remain separate. No live provider operations, inference, training or uploads ran.

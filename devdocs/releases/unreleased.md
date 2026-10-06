# Unreleased

## Highlights

- PR #33 establishes the Images presentation and shared styling; PR #34 connects it to real committed saved outputs through shared bounded gallery services, CLI inspection and the production Qt viewer. No release/version bump is introduced.

## Added

- `devdocs/design-system/`: Claude Design web reference (tokens, components, foundation cards, UI kits) mirroring `tokens.json`; documentation/prototype only, no runtime or QSS change.
- Manual Windows build/draft-release workflow (PR #31): one fresh canonical GUI build with locked runtime dependencies, uv/managed-Python caching across version-only bumps, bundled identity checks, checksum/manifest and a downloadable Actions artifact. Optional PR-number mode resolves the same-repository head SHA, isolates its cache and is strictly artifact-only; PR plus release tag is rejected before checkout. Optional existing tag mode attaches only to a draft; it checks merged/tag/version provenance, refuses published releases/asset replacement and verifies downloaded uploads. Owner target-Windows smoke and explicit publication remain required. No new release/version bump, build-run acceptance or PyInstaller speedup is claimed.

- Recorded the Phase 8 delivery sequence and selected read-only viewer scope, with a sample-derived Draw Things preset interchange contract and an anonymised representative configuration. Planning only; no gallery or preset runtime support is added.

- PR #33 adds canonical fixture Grid/List/Detail views and an offline launcher using the same gallery controller as the application. Grid uses uniform aspect-fit tiles; List has metadata columns, row-wide hover and density control. Activation opens Detail, with mounted browse scroll positions preserved. No bulk selection or real-output query is added.
- Detail offers Cover/Fit, 100%, zoom/pan, previous/next, keyboard navigation and fullscreen/Escape. Bundled portrait/reference assets and synthetic metadata remain explicitly illustrative; LoRA compatibility remains Unknown. Search/filters, refresh and saved-file actions remain disabled.

- PR #34 adds provider-neutral saved-output query/detail contracts, bounded read-only SQLite discovery, deterministic prompt/model/LoRA/date/dimensions/aspect filtering and sorting, `dtflow gallery list|show`, durable `viewer.json` preferences and safe metadata inspection.
- The production Images controller uses 80-output pages, six concurrent asynchronous thumbnail decodes, off-GUI-thread selected-image/metadata reads, anchored auto-refresh, previous/next across pages and missing/corrupt-file isolation. Copy path, Open folder and external-viewer actions target the recorded saved artifact.

## Changed

- Images hides the content rails and reuses the logo/navigation in two aligned header rows outside the scrolling controls. Leaving Images restores chrome, splitter widths and Inspector preference. No extra outer/header padding or sample-count pill; search/filter labels are replaced by explicit placeholders/defaults and accessible names/tooltips.
- Detail's right filmstrip spans the content height to the workspace bottom. Metadata, prompt and Additional/Raw panels remain beneath the main image to its left; image overlays have a 10px interior inset. Additional/Raw panels are mutually exclusive.
- Shared charcoal tokens and muted blue states replace the superseded gallery palette. Repeated neutral-control colors use named tokens; obsolete/repeated QSS declarations are removed. No separate gallery token family remains.
- Connection, Generate and Queues use section cards with compact icon/title headers and separate content regions while preserving QGroupBox identities, accessibility and business-control wiring. Shared caption/value/supporting/section/identifier/code typography, panel headings and information surfaces establish a consistent hierarchy across all tabs.
- Unknown/availability text uses neutral secondary gray, compact regular weight and explicit wording. Gallery model identity uses an accent glyph tile; LoRA names, weights and Unknown badges are distinct compact elements. The prompt has no redundant visible caption; expanded disclosure controls use shared accent states.
- Connection navigation is a compact power-icon button with token-colored last-explicit-check status and accessible text. Images is enabled after Queues. No polling or new connection requests; obsolete completions do not recolor a changed provider/endpoint selection.
- Images now opens Grid on first use, restores a saved view thereafter and defaults Detail image viewing to Fit. The shared thumbnail control is expressed as 25–250% with 100% as the standard size and scales Grid, List and Detail filmstrip thumbnails through the full range.
- The desktop header keeps only a compact positive-prompt search; negative-prompt search remains available through the shared query/CLI service. Exact-dimensions and deterministic sort controls are exposed alongside model, LoRA, date and ratio filters.

## Fixed

- Corrects the no-queue workspace stretching toolbar/hint/Plan header rows. The table or empty state owns flexible height; empty guidance stays centered and progress hides until a queue is selected. Queue actions and planning behavior remain intact.

- Restores the Queues batch-plan card toggle after the section-card header covered its native checkbox. The covered native indicator is explicitly suppressed. A visible keyboard-accessible header checkbox synchronizes with the existing group state; collapsed contents hide without disabling the custom toggle. Planning/queue semantics remain unchanged.

- Prevents the gallery shutdown event filter from accessing deleted QTableWidget objects by caching viewport identities, guarding Qt lifetime and parenting deferred layout timers. The owner confirmed the closing error is resolved.
- Removes the native checkerboard vertical-scrollbar page track and corrects gallery-preview import ordering for Ruff I001.
- Stabilizes asynchronously painted Grid/filmstrip button size hints so thumbnails do not collapse after loading; removes the old List 84 px row-height ceiling so scaling continues through 250%.
- Prevents debounced viewer-preference saves from raising into the Qt event loop after the shared async runner shuts down, avoiding the cascading pytest-qt setup failures observed during PR #34 validation.

## Validation and release audit

- PR #33 remains the historical offline presentation/style change. PR #34 is the current Phase 8A production viewer implementation; presets/reuse/cache-management/organisation remain later Phase 8 work.
- On 2026-10-06 the owner reported the full test suite passed after the PR #34 Qt lifecycle fix. No final command/count was supplied in that confirmation, so no exact count is claimed here. Earlier failing output showed the lifecycle exception cascading through Qt setup; the fix was then owner-retested successfully.
- Owner interactive smoke testing exercised the new Images viewer and identified Grid asynchronous thumbnail collapse and List scaling limits; both were corrected. The newest Grid-first/Fit-default adjustments have not been separately reported as smoke-tested yet.
- Documentation closeout is source review only. Exact native-DPI and packaged Windows visual acceptance remain separate. No live provider operations, inference, training, uploads or potentially billable actions ran during PR #34 implementation/documentation work.

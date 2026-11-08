# Unreleased

Changes through 6.2.0 are consolidated in [DTFlow 6.2.0](v6.2.0.md); its published GitHub release body records final acceptance. This file tracks subsequent changes.

## Highlights

## Added

- Images slideshow: F2 or the Slideshow toggle beside the buffered count opens fullscreen Detail, looping through the filtered gallery at 0.5–8 seconds per image. The right-hand thumbnail carousel is replaced by a scrollable grey prompt/LoRA panel reusing the info card, and restored on exit. Top-right translucent controls offer Forward/Backward playback and become opaque on hover/focus; wheel over controls adjusts rate, and Left/Right navigate manually. Playback stops on exit/navigation/shutdown and never starts generation. Source-reviewed only; native acceptance pending. See [plan](../plans/2026-11-08-images-slideshow.md).
- F5 opens Images from any workspace and enables Live + Follow with newest-first sorting, returning to the newest matching image without changing filters or the selected view. Repeating F5 resumes following; it never submits generation or starts a queue.
- Ctrl+Tab cycles Generate → Workflows → Queues → Images → Generate, entering at Generate from Connection or Logs without changing Images Live/Follow settings or submitting work.

## Changed

- Restyle the shared Images gallery link above ordinary right inspectors with selection-blue navigation states, a 14 px existing image icon and keyboard focus, rather than run-action accent blue. Keep navigation and responsive placement unchanged; align the web reference and document Qt styling differences. Source-reviewed only; no local tests or renders.

## Fixed

- Repair the offline Connection startup render fixture to supply the current credential-status shape (`keys` and `environment`), avoiding `KeyError: 'environment'` during the full UI render. Runtime credential behavior is unchanged. No local tests or renders were run; render completion remains unverified.
- Register the existing hidden-Connection counter and WARNING-and-worse Logs render states. Counter fixtures clear the earlier synthetic saved key and wait for credential identity rather than obsolete labels; the synthetic mapped-image presentation decodes its known local image without claiming cross-runtime managed-original verification. Decode failure now fails that fixture rather than saving a blank decoded state.

## Release audit

- PRs: #321, #322, #323
- Scope: Repair offline render fixtures/catalogue, restyle the shared Images navigation link, add F5/Ctrl+Tab navigation and the Images slideshow.

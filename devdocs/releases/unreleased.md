# Unreleased

Changes through 6.2.0 are consolidated in [DTFlow 6.2.0](v6.2.0.md); its published GitHub release body records final acceptance. This file tracks subsequent changes.

## Highlights

## Added

## Changed

- Restyle the shared Images gallery link above ordinary right inspectors with selection-blue navigation states, a 14 px existing image icon and keyboard focus, rather than run-action accent blue. Keep navigation and responsive placement unchanged; align the web reference and document Qt styling differences. Source-reviewed only; no local tests or renders.

## Fixed

- Repair the offline Connection startup render fixture to supply the current credential-status shape (`keys` and `environment`), avoiding `KeyError: 'environment'` during the full UI render. Runtime credential behavior is unchanged. No local tests or renders were run; render completion remains unverified.
- Register the existing hidden-Connection counter and WARNING-and-worse Logs render states. Counter fixtures clear the earlier synthetic saved key and wait for credential identity rather than obsolete labels; the synthetic mapped-image presentation decodes its known local image without claiming cross-runtime managed-original verification. Decode failure now fails that fixture rather than saving a blank decoded state.

## Release audit

- PRs: #321, #322
- Scope: Repair offline render fixtures/catalogue and restyle the shared Images navigation link.

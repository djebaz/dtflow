# Unreleased

Changes through 6.2.0 are consolidated in [DTFlow 6.2.0](v6.2.0.md); its published GitHub release body records final acceptance. This file tracks subsequent changes.

## Highlights

## Added

## Changed

## Fixed

- Repair the offline Connection startup render fixture to supply the current credential-status shape (`keys` and `environment`), avoiding `KeyError: 'environment'` during the full UI render. Runtime credential behavior is unchanged. No local tests or renders were run; render completion remains unverified.
- Register the existing hidden-Connection counter and WARNING-and-worse Logs render states. Counter fixtures clear the earlier synthetic saved key and wait for credential identity rather than obsolete labels; the synthetic mapped-image presentation decodes its known local image without claiming cross-runtime managed-original verification. Decode failure now fails that fixture rather than saving a blank decoded state.

## Release audit

- PRs: #321
- Scope: Repair offline credential-status/counter/image render fixtures and register missing catalogue states after the 6.2.0 full-render failure and warning audit.

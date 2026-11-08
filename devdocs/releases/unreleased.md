# Unreleased

Changes through 6.2.0 are consolidated in [DTFlow 6.2.0](v6.2.0.md); its published GitHub release body records final acceptance. This file tracks subsequent changes.

## Highlights

## Added

## Changed

## Fixed

- Repair the offline Connection startup render fixture to supply the current credential-status shape (`keys` and `environment`), avoiding `KeyError: 'environment'` during the full UI render. Runtime credential behavior is unchanged. No local tests or renders were run; render completion remains unverified.

## Release audit

- PRs: #321
- Scope: Repair the stale offline credential-status render fixture after the 6.2.0 full-render failure.

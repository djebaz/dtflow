# Unreleased

Changes through 6.1.0 are consolidated in [DTFlow 6.1.0](v6.1.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

## Changed

- DT+ keys: the key selected in the app (saved in Windows Credential Manager) now wins over `DRAWTHINGS_API_KEY`; several keys can be saved, named and switched, and the environment key can be selected explicitly. The Connection card shows which key is active and whether the environment variable is also set. The CLI still reads the environment only.
- Daily generation stats list every available key (even at zero) under one consistent name; no key is shown as unidentified.

## Fixed

## Upgrade and compatibility

## Validations

- [ ] pytest Full passed
- [ ] render_ui passed
- [ ] SmokeTests passed

## Release audit

- PRs: #305
- Scope: Version bump 6.1.0.

The `## CI build` section (source, artifact, SHA256) is appended by the build-and-release workflow to the draft; keep it in the published body.

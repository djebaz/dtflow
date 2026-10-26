# Unreleased

Changes through 5.3.0 are consolidated in [DTFlow 5.3.0](v5.3.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Images → Detail: hovering the info button at the top right of the main image shows a compact overlay with the full positive prompt (sized to fit, no scrolling) and the recorded LoRAs (#181). The overlay is source-implemented; render and Windows/DPI hover acceptance are pending.

## Changed

- Images → Detail: the main image no longer shows a path/"Saved output preview" tooltip; only image problems (missing, unreadable, corrupt) are shown. The path stays in the Additional details panel.

## Fixed

## Release audit

- PRs: #180.
- Scope: Version bump 5.3.0 and release-note consolidation; exact Windows artifact acceptance and publication remain separate.

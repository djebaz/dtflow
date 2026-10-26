# Unreleased

Changes through 5.3.0 are consolidated in [DTFlow 5.3.0](v5.3.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Images → Detail: hovering the info button at the top right of the main image shows a compact overlay with the full positive prompt (sized to fit, no scrolling) and the recorded LoRAs (#181). The overlay is source-implemented; render and Windows/DPI hover acceptance are pending.

- Generate and Queues: Ctrl+Enter (also keypad Enter) in the prompt editor presses the page's own action button: Generate image / Edit image, or in the queue composer Start now, otherwise Add to queue while a queue runs; never Save paused. Disabled, busy, repeated or confirmation-pending states do nothing, and the prompt tooltip names the target (#184). Source-implemented; owner keyboard acceptance is pending.

## Changed

- Images → Detail: the main image no longer shows a path/"Saved output preview" tooltip; only image problems (missing, unreadable, corrupt) are shown. The path stays in the Additional details panel.

## Fixed

## Release audit

- PRs: #180.
- Scope: Version bump 5.3.0 and release-note consolidation; exact Windows artifact acceptance and publication remain separate.

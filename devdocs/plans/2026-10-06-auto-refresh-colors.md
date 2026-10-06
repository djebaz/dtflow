# Plan: Auto-refresh colors (PR #37 follow-up)

## Scope
- In: reuse Connection's shared mint/teal On and neutral Off colors for the existing Auto-refresh button.
- Out: glyph/icon changes, label changes, dimensions/layout/radius changes, new tokens, refresh behavior, packaging or release.

## Action items
- [x] Inspect Connection reference colors and existing Auto-refresh QSS; owner clarifies colors only.
- [x] Use existing connection surface/accent/glyph tokens for On; neutral input/border/secondary tokens for Off; keep pressed/disabled/focus feedback.
- [x] Sync design, guide and unreleased notes; record owner-reported incremental refresh success separately from agent verification.
- [x] Static stylesheet resolution and diff review; commit/push to the same PR #37, without merge.

## Decisions and Design Changes
- 2026-10-06 Preserve the visible Auto-refresh On/Off text and 156x36 control. Apply colors through the canonical QSS and existing checked state; no new inline styles or token values.
- 2026-10-06 User reports incremental refresh works. This is owner feedback, not agent-run tests or visual acceptance of the new colors.
- 2026-10-06 Continue fast-implement: no tests added/run and no smoke tests. Packaging is unnecessary for this stylesheet-only follow-up.

## Open questions
- None.

## Validation
- [ ] Final tests and color/visual smoke tests deferred to owner.
- Tests and smoke tests are not run; final validation remains with the user.

- Ran canonical load_theme() token resolution successfully and git diff --check. No tests/smoke tests or packaging ran.
- README.md/AGENTS.md and token snapshots remain unchanged: no new public API, token value or feature; DESIGN.md, desktop guide, current status and release notes reflect the color-only change.
- Delivery: same fix/images-refresh-flicker branch and open PR #37; no merge/tag/release.

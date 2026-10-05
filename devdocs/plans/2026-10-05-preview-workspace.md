# Generate workspace refinement

Owner-approved direction: preserve the Qt Widgets application and shared services;
make the image, not the form header, the main working area. No generation provider
or compatibility policy is changed by this UI slice.

## First checkpoint

- Generate has rail-local branding and navigation, no page title/subtitle band.
- Prompt and negative prompt remain central; parameters move to the left rail.
- Original image pixels stay available. Fit contains portrait and landscape output
  without cropping; pixel zoom, Control-wheel zoom, drag-pan and overflow scrolling
  inspect the same saved artifact.
- Canvas-local controls inspect saved files, folders, paths and metadata. Interim
  preview is not a saved result. Expand/restore is presentation-only.

## Remaining before final review

- Shared QSS tokens and complete interaction-state coverage.
- Compact/expanded prompt, collapsible attempt details and narrow-window handling.
- Deterministic offline UI fixture models, LoRAs and receipt states; fake provenance
  remains explicit and no fixture action contacts a real provider.
- Render portrait, landscape, empty, busy, error and narrow-window states in Qt;
  inspect a bounded screenshot pass, correct defects and confirm once.

Verification is recorded in the PR as each checkpoint passes. Linux offscreen
renders do not establish Windows executable, DWM or deployed-backend readiness.

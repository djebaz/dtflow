# Unreleased

Changes through 6.5.1 are consolidated in [DTFlow 6.5.1](v6.5.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Loading a saved preset can select and remember renamed LoRAs that are already prepared for the selected DT+ key.

## Changed

- Requests with oversized numbers, including inpainting mask feather settings, report validation errors. Presets with unrepresentable guidance or LoRA weights preserve those values for re-export.

## Fixed

- Preset imports preserve the existing library if the selected file disappears. Import and save keep the library within the 4 MiB read limit.

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: none yet.
- Scope: Version bump 6.5.1 and release-note consolidation; no subsequent changes recorded.

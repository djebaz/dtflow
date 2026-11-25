# Unreleased

Changes through 6.5.1 are consolidated in [DTFlow 6.5.1](v6.5.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Development-mode source logs record the Git commit and tracked-worktree status when available; the desktop footer and About dialog show the corresponding short revision.

- Loading a saved preset can select and remember renamed LoRAs that are already prepared for the selected DT+ key.

## Changed

- Requests with oversized numbers, including inpainting mask feather settings, report validation errors. Presets with unrepresentable guidance or LoRA weights preserve those values for re-export.
- The desktop releases its references to background work as soon as that work starts, keeping tracking bounded during long sessions.

## Fixed

- Script analysis applies its deadline to parser startup and waits for the parser to be ready before sending the source.
- Preset imports preserve the existing library if the selected file disappears. Import and save keep the library within the 4 MiB read limit.
- Concurrent queue creation saves identical script sources using separate temporary files.
- Cancelling a video while local cleanup is running still frees its encoder slot and removes its temporary files.
- Changing the daily reset timezone preserves unreadable or unsupported preference files and honors the 4 KiB file limit.
- Script file reads enforce the 1 MiB limit before parsing or checking a stored source's hash.

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: none yet.
- Scope: Version bump 6.5.1 and release-note consolidation; no subsequent changes recorded.

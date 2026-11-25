# Unreleased

Changes through 6.5.1 are consolidated in [DTFlow 6.5.1](v6.5.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Development-mode source logs record the Git commit and tracked-worktree status when available; the desktop footer and About dialog show the corresponding short revision without a `g` prefix. New executable filenames also use the bare short revision.

- Loading a saved preset can select and remember renamed LoRAs that are already prepared for the selected DT+ key.

## Changed

- Requests with oversized numbers, including inpainting mask feather settings, report validation errors. Presets with unrepresentable guidance or LoRA weights preserve those values for re-export.
- The desktop releases its references to background work as soon as that work starts, keeping tracking bounded during long sessions.

## Fixed

- LoRAs stored on DT+ that this computer never prepared now appear in Generate, Queues, Workflows and Scripts. DTFlow checks each stored file with DT+ (nothing is uploaded) after reading the selected key's storage, and once per run for your other saved keys. A LoRA held only on another key shows as needing a re-attach on the selected key.
- The LoRA Check result lists in full only the LoRAs that need attention; the ones that match are summed up in one line.

- Restore previously acknowledged LoRA names that only have historical missing-key receipts to the shared selection pool. Dispatch still checks the selected key and never treats history as proof of current holdings.

- Discover provider-listed LoRAs in Generate, Queues, Workflows and Scripts even without local files or catalogue definitions, including conventional LoRA checkpoint names from metadata-less DT+ Echo. Compatible bundled LoRAs omitted by DT+ Echo remain selectable with availability unconfirmed. Unknown compatibility remains unverified; custom asset preparation and account safeguards are unchanged.

- Script analysis applies its deadline to parser startup and waits for the parser to be ready before sending the source.
- Preset imports preserve the existing library if the selected file disappears. Import and save keep the library within the 4 MiB read limit.
- Concurrent queue creation saves identical script sources using separate temporary files.
- Cancelling a video while local cleanup is running still frees its encoder slot and removes its temporary files.
- Changing the daily reset timezone preserves unreadable or unsupported preference files and honors the 4 KiB file limit.
- Script file reads enforce the 1 MiB limit before parsing or checking a stored source's hash.

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: #614.
- Scope: Version bump 6.5.1, release-note consolidation and cloud-only LoRA discovery.

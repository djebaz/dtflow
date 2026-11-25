# Unreleased

Changes through 6.5.1 are consolidated in [DTFlow 6.5.1](v6.5.1.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Loading a saved preset can select and remember renamed LoRAs that are already prepared for the selected DT+ key.

## Changed

- Requests with oversized numbers, including inpainting mask feather settings, report validation errors. Presets with unrepresentable guidance or LoRA weights preserve those values for re-export.
- The desktop releases its references to background work as soon as that work starts, keeping tracking bounded during long sessions.

## Fixed

- Discover cloud-stored custom LoRAs during the existing DT+ storage read. Candidate hashes from storage keys are checked through authenticated BYOM lookup before recording a reusable receipt; no weight upload or local copy is needed. Managed filename family tags supply the initial architecture hint.

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

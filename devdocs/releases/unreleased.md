# Unreleased

Changes through the 5.1.0 preparation baseline are consolidated in [DTFlow 5.1.0](v5.1.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Queue item table with saved-result thumbnails, prompt/model/LoRA/parameter columns, observed durations and an item inspector with Open in Images. No dispatch or database-schema change.
- Development-mode diagnostics summarize queue thumbnail outcomes/timings and record Open in Images handoff outcomes without prompts, image identities, paths or raw error text.

## Changed

## Fixed

- Queue previews now require live committed gallery records, revalidate cached availability and use owned shutdown-safe callbacks. Open in Images uses mapped availability, clears conflicting browse filters and reports missing results; composing a queue hides old item evidence.

## Planned (not implemented)

- [Prepare picker plan](../plans/2026-10-22-prepare-folder-and-multiple-files.md): remember the selected folder per runtime and prepare multiple selected cloud LoRA files sequentially. Planning only; no runtime change yet.

## Release audit

- PRs: #150.
- Scope: Post-5.1.0 Queue item-table/inspector delivery and review corrections merged through #150, with platform acceptance separate; the Prepare picker branch adds planning only, not delivered functionality or a published feature release.

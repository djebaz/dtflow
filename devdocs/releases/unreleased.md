# Unreleased

Changes through the 5.1.0 preparation baseline are consolidated in [DTFlow 5.1.0](v5.1.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Queue item table with saved-result thumbnails, prompt/model/LoRA/parameter columns, observed durations and an item inspector with Open in Images. No dispatch or database-schema change.
- Development-mode diagnostics summarize queue thumbnail outcomes/timings and record Open in Images handoff outcomes without prompts, image identities, paths or raw error text.
- Cloud LoRA Prepare remembers its picker folder per runtime and accepts multiple converted `.ckpt` files for sequential preparation. Count-based progress and final summaries distinguish confirmed, failed, unresolved and unstarted files; selected weights are preserved, uncertain files are never automatically retried, and failures emit privacy-safe diagnostics.

## Changed

## Fixed

- Queue previews now require live committed gallery records, revalidate cached availability and use owned shutdown-safe callbacks. Open in Images uses mapped availability, clears conflicting browse filters and reports missing results; composing a queue hides old item evidence.

## Release audit

- PRs: #150, #151.
- Scope: Post-5.1.0 Queue item-table/inspector delivery and review corrections merged through #150; #151 adds Prepare folder persistence and sequential multi-file preparation, pending acceptance/merge. Not a published feature release.

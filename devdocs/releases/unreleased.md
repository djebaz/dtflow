# Unreleased

Changes through the 5.1.0 preparation baseline are consolidated in [DTFlow 5.1.0](v5.1.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Generate checkpoint **Details** inspector combines exact-file Draw Things community technical descriptions, independent DTFlow classification and model-profile suggestions, and last-observed provider availability with per-field provenance. Purely local inspection; no cloud Compute Units estimate or generation behavior change.

- Queue item table with saved-result thumbnails, prompt/model/LoRA/parameter columns, observed durations and an item inspector with Open in Images. No dispatch or database-schema change.
- Development-mode diagnostics summarize queue thumbnail outcomes/timings and record Open in Images handoff outcomes without prompts, image identities, paths or raw error text.

## Changed

## Fixed

- Restore the status-bar About icon to 16 px within its existing 20 px button, matching the compact checkpoint-details control without padding that squeezes the glyph.

- Queue previews now require live committed gallery records, revalidate cached availability and use owned shutdown-safe callbacks. Open in Images uses mapped availability, clears conflicting browse filters and reports missing results; composing a queue hides old item evidence.

## Release audit

- PRs: #150, #152.
- Scope: Post-5.1.0 Queue item-table/inspector delivery and review corrections, plus PR #152 checkpoint metadata inspection and compact About sizing correction; scoped acceptance remains separate from source presence, not a published feature release.

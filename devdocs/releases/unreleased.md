# Unreleased

Changes through 6.3.0 are consolidated in [DTFlow 6.3.0](v6.3.0.md). This file tracks subsequent changes; release publication and exact-artifact acceptance remain separate gates.

## Highlights

## Added

- Prepare reads a `.safetensors` LoRA's descriptive header metadata: two inline lines with bold values and muted labels (training facts including the declared base model, top tags) with a styled selected-training-metadata tooltip (one trigger phrase, emphasized values, and a separate section of up to 50 retained tags with counts) in the preview band and in Edit metadata (training facts only: not the file's own title, author, license, date or description, which are also not stored; the CLI JSON report is unchanged), a Trigger Words suggestion only from an explicit trigger declaration (a typed value or a non-empty saved value wins; an empty saved value counts as not set, so the file's trigger is offered again), and the same details in Edit metadata after conversion without the original file. The metadata is display-only and never affects family detection or conversion. The converter CLI gains `--metadata` (`-m`), which converts nothing and writes a JSON report of the descriptive metadata of a file, folder or glob.

## Changed

- Developer UI screenshots use one **Preview** Action (`preview-ui.yml`) with change-selected, category, all-state and workflow-file modes; `preview:PART`/`preview:ALL` requests, automatic draft guards, shared GitHub artifacts/thumbnail comments and `preview_*` tooling names replace the separate render workflows. Preview execution and native acceptance remain pending (#421).
- Tooltips fade in quickly instead of sliding, app-wide (where the desktop allows UI effects). The Prepare rows align all their controls on one top edge.
- LoRA prompt hints now use an icon-only toggle beside each relevant prompt instead of a hover tooltip: Generate and Queues show a full-height, scrollable overlay in the right inspector below Images; Workflows shows it over the center canvas so node parameters remain accessible. A second click closes it. Removed redundant “File declarations · unverified” text from metadata tooltips and LoRA hover cards.

## Fixed

- Cloud Prepare distinguishes partial success from a wholly failed batch: mixed results use amber styling and a short toast, with concise per-file reasons in the side panel and full literal-text reasons in its tooltip and dev Logs. Definite failures with no successes retain red styling even while the editor stays open; a wholly failed closed-editor toast keeps full reasons. No retry, upload, receipt or uncertainty behavior changes (#419, #420). Review fixes are source-reviewed; edge-case Windows smoke remains pending.

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: #418, #417, #420
- Scope: Version bump 6.3.0 and release-note consolidation; #414 safetensors descriptive metadata and trigger suggestions (static checks, offscreen Qt checks of the editor and dialog on synthetic files; owner Windows smoke pending).

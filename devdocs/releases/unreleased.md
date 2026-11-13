# Unreleased

Changes through 6.3.0 are consolidated in [DTFlow 6.3.0](v6.3.0.md). This file tracks subsequent changes; release publication and exact-artifact acceptance remain separate gates.

## Highlights

## Added

- Prepare reads a `.safetensors` LoRA's descriptive header metadata: a two-line summary (training facts, top tags) in the preview band and in Edit metadata (training facts only, not the file's own title, author or description), a Trigger Words suggestion only from an explicit trigger declaration (a saved or typed value always wins), and the same details in Edit metadata after conversion without the original file. The metadata is display-only and never affects family detection or conversion. The converter CLI gains `--metadata` (`-m`), which converts nothing and writes a JSON report of the descriptive metadata of a file, folder or glob.

## Changed

- Tooltips fade in quickly instead of sliding, app-wide (where the desktop allows UI effects). The Prepare rows align all their controls on one top edge.

## Fixed

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: #418, #417
- Scope: Version bump 6.3.0 and release-note consolidation; #414 safetensors descriptive metadata and trigger suggestions (static checks, offscreen Qt checks of the editor and dialog on synthetic files; owner Windows smoke pending).

# Unreleased

Changes through 6.3.0 are consolidated in [DTFlow 6.3.0](v6.3.0.md). This file tracks subsequent changes; release publication and exact-artifact acceptance remain separate gates.

## Highlights

## Added

- Prepare reads a `.safetensors` LoRA's descriptive header metadata: a read-only "Declared by the file" panel in the editor, a Trigger Words suggestion only from an explicit trigger declaration (a saved or typed value always wins), and the same details in Edit metadata after conversion without the original file. The metadata is display-only and never affects family detection or conversion.

## Changed

## Fixed

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: #418, #417
- Scope: Version bump 6.3.0 and release-note consolidation; #414 safetensors descriptive metadata and trigger suggestions (static checks, offscreen Qt checks of the editor and dialog on synthetic files; owner Windows smoke pending).

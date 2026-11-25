# Unreleased

Changes through 6.5.5 are consolidated in [DTFlow 6.5.5](v6.5.5.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Generate has a collapsed **Advanced** section for text-to-image: Hires Fix (Size stays the final output; the first pass is shown before you generate), seed mode and tiled decoding. The section also applies to Inpaint and image edits; seed mode and tiled decoding apply to the Controlnet pose (not Hires Fix), and seed mode to video. Chosen values are sent, saved with the result and restored by Reuse settings and configuration files. Nothing is sent unless you choose it, and DT+ may still ignore a setting for a checkpoint it does not suit.

## Changed

## Fixed

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: #541, #542.
- Scope: Version bump 6.5.5, release-note consolidation and the first advanced-settings slice (Hires Fix, seed mode, tiled decoding) across the supported modes.

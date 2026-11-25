# Unreleased

Changes through 6.5.5 are consolidated in [DTFlow 6.5.5](v6.5.5.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Generate has a collapsed **Advanced generation** section for text-to-image: Hires Fix (Size stays the final output; the first pass is shown before you generate), seed mode and tiled decoding. The section also applies to Inpaint and image edits; seed mode and tiled decoding apply to the Controlnet pose (not Hires Fix), and seed mode to video. Scripts and Workflows do not send these values; a script that changes Hires Fix, seed mode or tiled decoding in its configuration is refused before any image is requested. Chosen values are sent, saved with the result and restored by Reuse settings and configuration files. Nothing is sent unless you choose it, and DT+ may still ignore a setting for a checkpoint it does not suit. Draw Things presets do not store these values, and the Compute Units estimate does not include the Hires Fix second pass. Seed mode affects how the provider draws noise; DTFlow always sends one image per request.
- Queues carry the chosen advanced settings: queue templates and queued items keep them, and the queue draft summary and the item panel show them. Templates saved before the settings existed load as before.

## Changed

## Fixed

## Upgrade and compatibility

- Queue templates saved by this version carry an `advanced` entry; an older DTFlow cannot read them, which matters only if you downgrade. Templates and queues from older versions load unchanged.

## Validations checklist

- [ ] Default Generate (text-to-image, nothing chosen in Advanced generation) behaves as before.
- [ ] Hires Fix 2× on text-to-image and Inpaint: returned size equals the requested Size and the image looks refined.
- [ ] Seed mode and tiled decoding on one checkpoint; seed mode on one video; tiled decoding with a Controlnet pose.
- [ ] Reuse settings and Regenerate from a saved image restore the advanced values.
- [ ] A queue item and a template with Hires Fix keep it across a restart and Retry.

## Release audit

- PRs: #541, #542, #543.
- Scope: Version bump 6.5.5, release-note consolidation and the first advanced-settings slice (Hires Fix, seed mode, tiled decoding) across the supported modes.

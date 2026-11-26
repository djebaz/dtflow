# Unreleased

Changes through 6.5.5 are consolidated in [DTFlow 6.5.5](v6.5.5.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Generate has a collapsed **Advanced generation** section for text-to-image: HiRes (Size stays the final output; the first pass is shown before you generate), seed mode and tiled decoding. The section also applies to Inpaint and image edits; seed mode and tiled decoding apply to the Controlnet pose (not HiRes), and seed mode to video. Scripts and Workflows do not send these values; a script that changes HiRes, seed mode or tiled decoding in its configuration is refused before any image is requested. Chosen values are sent, saved with the result and restored by Reuse settings and configuration files. Nothing is sent unless you choose it, and DT+ may still ignore a setting for a checkpoint it does not suit. Draw Things presets do not store these values, and the Compute Units estimate does not include the HiRes second pass. Seed mode affects how the provider draws noise; DTFlow always sends one image per request.
- Queues carry the chosen advanced settings: queue templates and queued items keep them, and the queue draft summary and the item panel show them. Templates saved before the settings existed load as before. Images Details shows an Advanced line for results made with them (the full record stays in Raw metadata), and Use settings tells you when recorded advanced values cannot be restored or sent.
- Cross LoRA weights in the Queues composer has **Constant LoRAs**, LoRAs used at their weight in every item beside the swept ones, and editable **Sweep weights** (0, 0.5 and 1 by default). Edit as new brings both back. Plain Cross and Pair also keep the Constant LoRAs with LoRA sets.

## Changed

- In the Queues composer the Fixed LoRAs row is now called **Constant LoRAs**, and LoRA sets add to the Constant LoRAs instead of replacing them (a LoRA in both takes the set's weight). Queues and templates saved before keep their items, and plan files keep the old behavior unless they set `"constant_loras": true`.

## Fixed

- The Compute Units estimate in Generate now follows HiRes: it adds the second pass to the smaller first pass, so turning HiRes on changes the number. The result is an approximation (Draw Things' own estimate ignores HiRes), and the "excludes the second pass" note is gone. The wording is now "HiRes" everywhere.

## Upgrade and compatibility

- Queue templates saved by this version carry an `advanced` entry; an older DTFlow cannot read them, which matters only if you downgrade. Templates and queues from older versions load unchanged. Templates saved by this version also carry a `constant_loras` entry that an older DTFlow cannot read.

## Validations checklist

- [ ] Default Generate (text-to-image, nothing chosen in Advanced generation) behaves as before.
- [ ] HiRes 2× on text-to-image and Inpaint: returned size equals the requested Size and the image looks refined.
- [ ] Seed mode and tiled decoding on one checkpoint; seed mode on one video; tiled decoding with a Controlnet pose.
- [ ] Reuse settings and Regenerate from a saved image restore the advanced values.
- [ ] A queue item and a template with HiRes keep it across a restart and Retry.
- [ ] HiRes on, then off, at factor 2 and 4: the Compute Units meter and the checkpoint details change with it, and no "excludes the second pass" note remains.
- [ ] Cross LoRA weights with a Constant LoRA and two Swept LoRAs: 9 items, each with the Constant LoRA; reopen with Edit as new. Change the Sweep weights and check the count.
- [ ] Cross with a Constant LoRA and two LoRA sets: each item has the Constant LoRA plus its set.

## Release audit

- PRs: #541, #542, #543, #544, #625 (refs #624).
- Scope: Version bump 6.5.5, release-note consolidation and the first advanced-settings slice (HiRes, seed mode, tiled decoding) across the supported modes.

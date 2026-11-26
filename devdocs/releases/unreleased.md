# Unreleased

Changes through 6.5.5 are consolidated in [DTFlow 6.5.5](v6.5.5.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

- Controlnet guides images with **Canny edges, depth or segmentation** as well as pose, from a photo or a prepared map, for SD 1.x and SDXL checkpoints as well as FLUX.1, when your DT+ inventory lists the control. Choosing a control, type or model family starts from suggested settings; saved settings are kept as they are. Queues, Gallery Use settings and templates keep the control type. Depth from a photo uses Depth Anything V2 Small, downloaded on request (about 94.5 MiB, Apache-2.0); Canny from a photo uses OpenCV, now included.
- Generate has a collapsed **Advanced generation** section for text-to-image: HiRes (Size stays the final output; the first pass is shown before you generate), seed mode and tiled decoding. The section also applies to Inpaint and image edits; all three settings also apply to Controlnet, and seed mode to video. Scripts and Workflows do not send these values; a script that changes HiRes, seed mode or tiled decoding in its configuration is refused before any image is requested. Chosen values are sent, saved with the result and restored by Reuse settings and configuration files. Nothing is sent unless you choose it, and DT+ may still ignore a setting for a checkpoint it does not suit. Draw Things presets do not store these values, and the Compute Units estimate does not include the HiRes second pass. Seed mode affects how the provider draws noise; DTFlow always sends one image per request.
- Queues carry the chosen advanced settings: queue templates and queued items keep them, and the queue draft summary and the item panel show them. Templates saved before the settings existed load as before. Images Details shows an Advanced line for results made with them (the full record stays in Raw metadata), and Use settings tells you when recorded advanced values cannot be restored or sent.
- The Queues composer has its own **Advanced generation** section (HiRes, seed mode, tiled decoding), applied to every item of the plan: the first pass is derived from each item's Size, and settings the draft's kind of request cannot send (video) are shown disabled with a note. Queue templates keep the values and Edit as new restores them.
- Cross LoRA weights in the Queues composer has **Constant LoRAs**, LoRAs used at their weight in every item beside the swept ones, and editable **Sweep weights** (0, 0.5 and 1 by default). Edit as new brings both back. Plain Cross and Pair also keep the Constant LoRAs with LoRA sets.

## Changed

- Controlnet offers **Union Pro 2.0** (q5p first) instead of Union Pro 1.0 for FLUX.1. Settings, queues or templates that name Union Pro 1.0 are kept as they are but show a notice and need you to choose Union 2.0 yourself; nothing is replaced silently.
- In the Queues composer the Fixed LoRAs row is now called **Constant LoRAs**, and LoRA sets add to the Constant LoRAs instead of replacing them (a LoRA in both takes the set's weight). Queues and templates saved before keep their items, and plan files keep the old behavior unless they set `"constant_loras": true`.

## Fixed

- The saved model list is used again after a restart: the app no longer gives up reading it when other startup work holds the cache for a moment, so Refresh models is no longer needed after every start.
- Controlnet keeps its own base model, checkpoint, LoRAs and settings (size, steps, guidance, sampler, seed and Advanced generation), apart from Text to image and Inpaint. A preset or a LoRA chosen in one mode no longer lands in the other, so a Text to image LoRA from another model family no longer blocks a Controlnet generation, and the sampler stays as you chose it when you go back and forth. Prompts stay shared. The first time you open Controlnet it starts from your current settings without LoRAs.

- The Compute Units estimate in Generate now follows HiRes: it adds the second pass to the smaller first pass, so turning HiRes on changes the number. The result is an approximation (Draw Things' own estimate ignores HiRes), and the "excludes the second pass" note is gone. The wording is now "HiRes" everywhere, and the HiRes factor goes up to 10 instead of 4.

## Upgrade and compatibility

- Queue templates saved by this version carry an `advanced` entry; an older DTFlow cannot read them, which matters only if you downgrade. Templates and queues from older versions load unchanged. Templates saved by this version also carry a `constant_loras` entry that an older DTFlow cannot read.

## Validations checklist

- [ ] Default Generate (text-to-image, nothing chosen in Advanced generation) behaves as before.
- [ ] HiRes 2× on text-to-image and Inpaint: returned size equals the requested Size and the image looks refined.
- [ ] Seed mode and tiled decoding on one checkpoint; seed mode on one video; tiled decoding with a Controlnet pose.
- [ ] Reuse settings and Regenerate from a saved image restore the advanced values.
- [ ] Controlnet and Text to image keep separate checkpoint, LoRAs, size, steps, guidance, sampler and seed when switching back and forth and after a restart; Use settings on a pose image fills Controlnet.
- [ ] A queue item and a template with HiRes keep it across a restart and Retry.
- [ ] HiRes on, then off, at factor 2 and 4: the Compute Units meter and the checkpoint details change with it, and no "excludes the second pass" note remains.
- [ ] Queues composer: HiRes 2× on a Cross plan with two Sizes: each item's first pass follows its Size; save a template, reload it and Edit as new from a queue: the values come back.
- [ ] Queues composer with a Controlnet pose draft: HiRes is available and the chosen values are retained and sent; provider compatibility is not guaranteed.
- [ ] Cross LoRA weights with a Constant LoRA and two Swept LoRAs: 9 items, each with the Constant LoRA; reopen with Edit as new. Change the Sweep weights and check the count.
- [ ] Cross with a Constant LoRA and two LoRA sets: each item has the Constant LoRA plus its set.

## Release audit

- PRs: #541, #542, #543, #544, #625 (refs #624).
- Scope: Version bump 6.5.5, release-note consolidation and the first advanced-settings slice (HiRes, seed mode, tiled decoding) across the supported modes.

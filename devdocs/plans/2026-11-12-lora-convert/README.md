# LoRA convert inside dtFlow: plan set (#357, epic #359)

Docs-only record of the plan for using the pure-Python LoRA converter (#391-#393, [format notes](../../guides/lora-ckpt-format.md)) inside dtFlow. Written 2026-11-12 against main `23f1254` by static reading; nothing here is verified behaviour.

| File | What it is |
| --- | --- |
| [options.md](options.md) | Ten places the feature could live, with facts only |
| [ranking.md](ranking.md) | Independent ranking; top pick: conversion inside the Prepare editor |
| [architecture.md](architecture.md) | `services/lora_convert/` package, API, registry, errors, LoraStore integration, PR split, Djé's decisions (section 16) |
| [prepare-progress-plan.md](prepare-progress-plan.md) | Prepare overlay close rule and real phased progress with Stop |

Work items (one PR each): #398 close rule, #399 real progress, #400 move the converter, #401 Prepare accepts `.safetensors`, #402 safetensors family detection, #403 metadata-editor convert from source, #404 optional drag and drop and CLI, plus the final validation issue.
Statements such as "project file" inside these documents refer to the original working copies; the files in this folder are the canonical ones.

## Closing status (2026-11-13, #405)

Shipped: #398, #399, #400, #401 and #402 (the Prepare editor accepts `.safetensors`, converts after explicit Prepare and suggests the source family from the header). Differences from this plan: conversion runs one file at a time inside the existing Prepare flow; family detection is its own `detect.py` and does not use `FamilySpec.landmarks` (removed); the CLI auto-detects when `--family` is omitted. Deferred: #403 (cross-version reuse and naming; revisit at the first `CONVERTER_VERSION` bump) and the metadata-editor "convert from source" action it superseded; optional: #404, #414. Final family statuses, evidence and current behavior are in [the converter guide](../../guides/lora-ckpt-format.md) and [the architecture reference](../../architecture/lora-convert.md); these plan files stay as the historical record.

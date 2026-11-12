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

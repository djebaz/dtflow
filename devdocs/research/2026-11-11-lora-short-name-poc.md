# LoRA short-name suggestions — standalone POC

**Status:** Exploratory; not integrated into DTFlow. **Related:** [#373](https://github.com/djebaz/dtflow/issues/373), [PR #377](https://github.com/djebaz/dtflow/pull/377).

## Purpose

Explore whether converted LoRA/checkpoint filenames can provide useful, editable short-name **suggestions** before considering changes to the Prepare LoRAs workflow. The prototype is the standalone, standard-library script [`tools/lora_short_name_poc.py`](../../tools/lora_short_name_poc.py). The owner supplied a real JSON catalog for review; the catalog is not committed.

## Current POC rules

- Accept a catalog (`--catalog`) and/or explicit filenames (`--name`); print a compact table or JSON (`--json`). Read-only: no rename, persistence, upload, or provider call.
- Strip common conversion/precision, training, and architecture tokens from the *name suggestion*; preserve original file paths and reported architecture evidence independently.
- Favor the first meaningful descriptive word. Compose additional **whole words** only while the result fits 1–20 ASCII letters/digits/underscores. Skip overlong words instead of truncating them. Short meaningful names such as `Nak` remain valid.
- Offer alternatives and heuristic confidence/review flags. No candidate is a valid outcome.
- Scope duplicate suggestion names **case-insensitively by (short name, model family, quantization)**, not globally. A family comes from reported `detected_family` (or supplied safetensors `ss_base_model_version` metadata), not filename guessing. Quantization comes from F16/Q8P filename hints; otherwise a `.ckpt` gets the converter F16 default, and non-CKPT files without a hint stay unknown. This is naming context, not verified compatibility or byte-level precision inspection.
- Propose `_2`, `_3`, etc. only for repeated names *within the same family and format*. Keep the entire proposal within 20 characters without cutting words. Flag numbered suggestions and unknown collision scope for review.

### Illustrative collisions

| Short-name suggestion | Family | Format | Outcome |
| --- | --- | --- | --- |
| `Bigdickdredd` | SDXL | F16 | Keep |
| `Bigdickdredd` | SDXL | Q8P | Keep |
| `RealisticNudesV1` | Krea 2 | F16 | Keep |
| `RealisticNudesV1` | MOSS | F16 | Keep |
| `Turbo` | Z-Image | F16 | Keep for first occurrence |
| `Turbo` | Z-Image | F16 | Propose `Turbo_2` for the next |

## Boundary and next decision

This script **does not change issue #373's approved Prepare contract**: new short-name cells remain blank, user-entered and required; actual managed-filename collisions are resolved manually, with no automatic suffixing or overwrites. Numbering here is exploratory **suggestion output only**. Distinct content, existing managed assets, user-declared architectures, manual quantization, and upload/receipt identity are not reconciled by the POC. Before any integration, review the naming quality against real files and explicitly decide whether to revise #373.

**Validation:** Source inspection only for this documentation update; no automated tests, renders, model inference, DT+ operations, or Windows checks were run.

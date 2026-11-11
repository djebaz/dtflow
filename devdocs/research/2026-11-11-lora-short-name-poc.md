# LoRA short-name suggestions — standalone POC

**Status:** Standalone POC; owner-approved naming baseline for issue #373 (2026-11-11), **not integrated** into DTFlow. **Related:** [#373](https://github.com/djebaz/dtflow/issues/373), [PR #377](https://github.com/djebaz/dtflow/pull/377).

## Purpose

Explore filename-derived, editable short-name **suggestions** for the approved future Prepare LoRAs editor without changing its runtime behavior yet. The prototype is the standalone, standard-library script [`tools/lora_short_name_poc.py`](../../tools/lora_short_name_poc.py). The owner supplied a real JSON catalog for review; the catalog is not committed.

## Current POC rules

- Accept a catalog (`--catalog`) and/or explicit filenames (`--name`); print a compact table or JSON (`--json`). Read-only: no rename, persistence, upload, or provider call.
- Strip common conversion/precision, training, and architecture tokens from the *name suggestion*. Architecture keywords and shortcodes cover the 30 Draw Things identifiers in [issue #373 §3](https://github.com/djebaz/dtflow/issues/373), including known filename aliases. Preserve original file paths and reported architecture evidence independently; never infer the LoRA's effective architecture from filename keywords. Do not discard standalone `V1`/`V2` where they may be a meaningful subject version.
- Favor the first meaningful descriptive word. Compose additional **whole words** only while the result fits 1–20 ASCII letters/digits/underscores. Skip overlong words instead of truncating them. Short meaningful names such as `Nak` remain valid.
- Offer alternatives and heuristic confidence/review flags. No candidate is a valid outcome.
- Scope duplicate suggestion names **case-insensitively by (short name, model family, quantization)**, not globally. A family comes from reported `detected_family` (or supplied safetensors `ss_base_model_version` metadata), not filename guessing. Quantization comes from F16/Q8P filename hints; otherwise a `.ckpt` gets the converter F16 default, and non-CKPT files without a hint stay unknown. This is naming context, not verified compatibility or byte-level precision inspection.
- Propose `_2`, `_3`, etc. only for repeated names *within the same family and format*. Keep the entire proposal within 20 characters without cutting words. Flag numbered suggestions and unknown collision scope for review.

### Illustrative collisions

The following short names are arbitrary, fictional examples, not suggestions from the supplied catalog.

| Short-name suggestion | Family | Format | Outcome |
| --- | --- | --- | --- |
| `CopperLark` | SDXL | F16 | Keep |
| `CopperLark` | SDXL | Q8P | Keep — different quantization |
| `PebbleArc` | Krea 2 | F16 | Keep |
| `PebbleArc` | MOSS | F16 | Keep — different family |
| `MoonQuill` | Z-Image | F16 | Keep for first occurrence |
| `MoonQuill` | Z-Image | F16 | Propose `MoonQuill_2` |
| `MoonQuill` | Z-Image | F16 | Propose `MoonQuill_3` |

## Integration boundary

The owner has updated [issue #373](https://github.com/djebaz/dtflow/issues/373): **new Prepare rows should prefill an editable suggestion when usable, otherwise stay blank**; manual changes and previously saved short names remain authoritative. Only same-name proposals under the **same exact selected architecture and format** may receive `_2`, `_3`, etc. The managed name remains `ShortName_FamilyTag_LoRA_Format.ckpt`. No upload starts without the explicit Prepare action.

This POC implements **advisory output only**, not UI prefill, persistence or actual managed-asset collision safety. Before integrating its naming function, check stored content identity, existing managed filenames, user-declared architecture and format, and reserved batch names. Never silently overwrite prepared identifiers, rename existing assets, alter a user-edited name or present filename-derived architecture words as verified compatibility. The real editor must still reject unsafe collisions at Prepare.

**Validation:** Source inspection only for this documentation update; no automated tests, renders, model inference, DT+ operations, or Windows checks were run.

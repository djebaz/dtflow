# LoRA short-name suggestions — standalone POC

**Status:** Standalone POC with shared production naming logic extracted in draft PR #380; approved naming baseline for issue #373 (2026-11-11), **not integrated into the Prepare UI**. **Related:** [#373](https://github.com/djebaz/dtflow/issues/373), [PR #377](https://github.com/djebaz/dtflow/pull/377).

## Purpose

Explore filename-derived, editable short-name **suggestions** for the approved future Prepare LoRAs editor without changing its runtime behavior yet. The prototype CLI is [`tools/lora_short_name_poc.py`](../../tools/lora_short_name_poc.py), now a consumer of the pure, standard-library [`services/lora_naming.py`](../../src/dtflow/services/lora_naming.py) module on PR #380. The owner supplied a real JSON catalog for review; the catalog is not committed.

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

## Production extraction (#379 / draft PR #380)

- The Qt-free, I/O-free naming module provides `suggest_short_name`, `allocate_batch_names` and `managed_filename`.
- The POC CLI imports the same naming implementation instead of maintaining a second algorithm. It retains its exploratory JSON-catalog input and output formatting.
- Allocation receives exact architecture and F16/Q8P from the caller, never from filename parsing; collision proposals are advisory and do not reserve database names or authorize Prepare.
- Canonical occupied-name reads, final write-time collision checks and user-edit-aware Qt Prepare integration remain dependent on #370/#373. No provider or Windows acceptance is claimed.

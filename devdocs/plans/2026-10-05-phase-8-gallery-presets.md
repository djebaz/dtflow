# Phase 8 — Gallery, presets and daily-use refinement

**Date:** 2026-10-05

**Status:** 8A is implemented. PR #46 implements the selected 8B–8D scope; owner/runtime acceptance and final 0.4.0 release acceptance remain pending.

**Target:** Proposed 0.4.0, not a shipped version.

**References:** [initial Phase 8](initial.md#phase-8--gallery-and-presets), [release roadmap](releases.md), [runtime layout](../architecture/runtime-layout.md).

## Sequence and boundaries

Implement in order: **8A viewer → 8B presets → 8C reuse/regeneration → 8D cache management → 8E organisation**.
Use small reviewable PR slices. This document records selected capabilities and constraints, not screen layouts or mockups.
The [sequencing plan](2026-10-05-phase-8-sequencing.md) owns phase order and handoffs; the [viewer plan](2026-10-05-phase-8a-viewer.md) owns selected 8A scope. This document retains preset-format evidence and supporting later-phase constraints. Detailed feature choices for 8B–8E remain to be refined.
Organisation is a later extension, not automatically a prerequisite for 0.4.0.

## 8A — Read-only output viewer

The [dedicated viewer plan](2026-10-05-phase-8a-viewer.md) owns all selected capabilities, implementation checkpoints, open semantics and viewer acceptance. Owner view-template decisions are recorded there; implementation is not started.

## 8B — Draw Things-compatible presets

### Format decision

Use the **Draw Things `custom_configs.json` format** for preset interchange, not a new DTFlow-only export format.
The owner supplied an export on 2026-10-05 containing 35 entries and 84 distinct configuration keys.
Its observed envelope is a JSON array of objects with `name` and `configuration`:

```json
[
  {
    "name": "Example preset",
    "configuration": {
      "model": "z_image_turbo_1.0_q6p.ckpt",
      "width": 448,
      "height": 704,
      "steps": 8,
      "guidanceScale": 1,
      "seed": 120004,
      "sampler": 10,
      "loras": [{"file": "example_lora.ckpt", "mode": "all", "weight": 0.35}]
    }
  }
]
```

This short illustration omits most fields. The [representative full configuration](examples/drawthings-custom-configs.json)
preserves one exported configuration, with an anonymised preset name and illustrative LoRA filenames.
Neither example is an official schema, a runnable compatibility claim or a complete inventory of supported upstream fields.
Numeric formatting is normalised; round trips must preserve JSON values/semantics, not byte-for-byte formatting.
`custom.json` describes models and is not this preset format.

### Shared-service contract

- Import/export the native array envelope, including multiple named presets; keep library operations separate from request dispatch.
- Preserve unknown and currently unsupported configuration fields through import, editing of supported fields and re-export. Show which settings are applied, preserved only or rejected; never silently claim full compatibility.
- Map supported fields into the existing shared configuration/resolution path. The current `services/config.py` version-1 DTFlow config is not a native Draw Things parser; add an adapter rather than changing the backend-neutral request to upstream-specific types.
- Verify upstream numeric sampler and seed-mode mappings, dimension units and LoRA `mode` semantics before applying them. Do not infer meanings from these sample values or silently substitute an unknown sampler.
- Preserve LoRA `file`, `mode` and `weight`; availability and compatibility still require the established preflight evidence. A preset does not authorize downloading, conversion or upload.
- The supplied export has no positive/negative generation prompt text. Loading it must not clear or invent prompts; prompt inclusion/extension policy must be decided explicitly before implementation.
- The sample has no explicit envelope schema/version. Do not insert a DTFlow version field into native exports; version DTFlow's durable library/adapter metadata separately and define unsupported-format handling.
- Keep credentials, provider connection settings, cloud receipts and DTFlow organisation metadata outside the native export. Preservation of unknown fields does not override credential rejection.
- Presets are durable user data, not cache. Keep existing DTFlow configuration save/load compatibility; loading a preset never starts generation.

### Planned delivery and acceptance

Refine named preset save/load, browse, rename, duplicate/delete and import/export scope before implementation.
Use `8-O1` for semantic round trips, preservation of unsupported fields, malformed/oversized inputs, duplicate-name policy and unknown numeric values.
Add focused Qt composition coverage only for new wiring (`8-O5`); do not repeat backend protocol tests.
Before claiming Draw Things compatibility, have the owner import an exported fixture back into Draw Things and compare effective settings.
The upstream version that produced the sample and exact enum meanings remain unverified; importing a JSON example alone does not prove interoperability.

## 8C — Reuse and explicit regeneration

- Reuse recorded image settings in the generation form without automatically submitting them.
- Explicit regeneration creates a fresh resolved request/attempt through existing services, with source lineage and visible provider/availability validation.
- Preserve original outputs and records; never treat ambiguous attempts as replayable.
- Refine fixed-seed versus varied-seed actions before implementation. Do not promise pixel-identical results across providers or backend versions.
- Validate new-request and original-preservation semantics (`8-O3`) plus necessary Qt wiring (`8-O5`); existing generation safety remains authoritative.

## 8D — Disposable cache management

- Measure cache by type and expose confirmed category-specific purge operations, including new thumbnails and any rebuildable gallery index.
- Preserve outputs, attempts, queues, presets, organisation, settings, last session, credentials, model assets and retained live-test evidence.
- Respect `-nc` / `--no-cache`; make rebuilding possible without treating cache as authoritative provenance.
- Define safe coordination with active readers/writers before implementation. Test preservation and category boundaries at one authoritative layer (`8-O4`).

## 8E — Organisation (scope to choose later)

Choose favourites, ratings, tags, notes, collections and any bulk operations separately; these are candidates, not all approved requirements.
Store selected organisation data durably outside disposable indexes and native Draw Things preset exports.
Destructive file actions, if ever selected, require a separate explicit policy; none are approved by this viewer plan.

## Implementation checkpoints

1. Refine open semantics and the relevant slice's service contract before coding; read `DESIGN.md` when actual desktop design begins.
2. Deliver 8A with bounded queries and cache separation; then 8B with verified native format mappings.
3. Deliver 8C and 8D through existing shared services; decide 8E scope independently.
4. Update status, architecture and unreleased notes as behaviour lands. Use local fixtures and focused tests, not one redundant test per feature bullet.
5. Record owner Windows acceptance of each usable slice and the final packaged build separately. No generation, training or billable action is required for this planning work.

# Checkpoint metadata inspector and resolver

**Status:** Implementation plan (draft PR; no runtime acceptance yet)
**Scope:** Read-only metadata inspection in Generate; no generation, queue, auth, inventory or catalogue mutation.

## Goal

Expose published checkpoint metadata that DTFlow already bundles, alongside its
classification, generation profile and current provider's last-observed availability.
Keep those sources independently maintainable. This PR prepares the data boundary
for a later offline Compute Units estimator; it does not calculate or display units.

## Existing authorities (do not merge)

- `Models_prime.json` / `Models_niche.json`: classification, family, domain and tier.
- `model_profiles.json`: suggested steps, guidance, sampler and profile extras.
- `community_model_specs.json`: published Draw Things technical descriptions,
  matched by **exact checkpoint filename**; no cross-quantization substitution.
- Provider `InventorySnapshot` (local or direct DT+ Echo): observed presence,
  provenance and timestamp; a snapshot is not generation authorization.
- `core/model_profiles.py`, `services/catalogue.py`: existing lookup boundaries.

## Implementation

1. Add headless `services/checkpoint_metadata.py` to return a typed, immutable,
   read-only checkpoint-details projection. Resolve independent fields from
   `Catalogue`, `exact_model_spec`, `profile_for` and optional provider
   inventory. Keep `None`/Unknown for missing evidence and preserve each
   source's provenance. Do not use derived `fallback_spec` as published proof.
2. In canonical `src/dtflow/ui/forms/generation_page.ui`, add a compact
   `Checkpoint details` action near the exact checkpoint field.
   Use the existing shared inline metadata/inspection surface and Generate
   controller for presentation, without introducing another catalogue browser.
3. Add offline fixture coverage for populated, missing, and long identifiers in
   `tools/ui/render_states.py`. Do not render screenshots unless requested.
4. Add focused tests for exact variant identity, unknown/derived metadata,
   independent profile lookup, catalogue/availability distinction, and
   provider switching. Existing selection, request serialization, generation,
   cache, queue and credentials must remain unchanged.
5. Update desktop/cloud-integration guidance and pending release notes;
   preserve original metadata provenance in user-facing strings.

## Non-goals

No Compute Units calculations, Swift dependency, catalogue materialization or
regeneration, model downloads, new cloud/API requests, network lookups while
inspecting, billable generation, LoRA compatibility guesses, or changes to
dispatch/validation permissions. An unknown checkpoint remains unknown.

## Acceptance

- The metadata view reads only the selected exact checkpoint, never changes it.
- Published technical fields, DTFlow classification/profile and last-observed
  provider inventory are clearly distinct; missing fields read `Not available`.
- Switching provider/checkpoint refreshes the view without stale evidence.
- Existing Generate controls and submission flow are untouched.
- Focused offline checks pass and canonical Qt state preview includes the new UI;
  native Windows DPI/packaged smoke acceptance remains owner-gated.
- No cloud inference/training, downloads or potentially billable actions used.

## Owner-requested About correction in this PR

The owner reported a tiny status-bar About glyph and requested matching the new checkpoint-details button in PR #152. Source history traces the shrink to PR #105's fixed 20 × 20 button combined with the older 2 px vertical / 6 px horizontal QSS padding. Keep the existing 20 × 20 button, explicitly request a 16 × 16 icon, and remove the About-specific padding; preserve its action, tooltip, accessible name and hover behavior. Added offline state `connection-40-about-status-button` includes this footer state and checks the configured button/icon sizes. Source implemented; no local tests or renders, native appearance remains owner-gated. No provider or generation behavior changes.

## Next PR

Implement a tested Python port/adaptation of Draw Things `ComputeUnits.from`,
validated against official Swift reference calculations and exact-file model
metadata; then display request estimates and queue totals with unknown fallback.

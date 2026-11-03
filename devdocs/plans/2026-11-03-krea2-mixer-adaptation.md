# Plan: finish the portable Krea2 mixer adaptation (#216)

## Scope and acceptance boundary

- Base: merged PR #218, `9e767fe`. Implement read-only LoRA enumeration, a genuinely non-submitting Setup preview, source-version-scoped mixer preferences, and explicit deterministic bounded chunks. Preserve the original Draw Things fallback and the pinned upstream facade.
- DTFlow uses a disclosed reduced-fidelity preset: inherit Generate's checkpoint/steps and unchanged unsupported values; change only supported dimensions, sampler, guidance, seed and LoRAs. Do not claim the original iOS preset's shift/clip/tile/guidance-embedding behavior or broaden #115 without evidence.
- No tests added/modified/run, checks, renders, app launch, model downloads, uploads, inference or provider calls by the agent. Owner-only fake/native/backend/packaged acceptance is required before closing #216; implementation presence alone is not full epic acceptance.

## Actions

- [x] Inspect issue, canonical services, existing portable source, merged main and instructions; create a separate feature branch.
- [ ] Add a bounded read-only DTFlow inventory facade, using existing catalogue/backend metadata and prepared receipts; keep listed/prepared/compatibility/unknown evidence separate.
- [ ] Let explicit preparation forms advance while the original child remains behind the queue admission gate. Preview must neither create a queue nor open a backend session. Run Script remains separate consent.
- [ ] Add bounded versioned preferences scoped to the exact source SHA, independent of PNG markers, sandbox outputs and dispatch authority. Store LoRA selections by identifier, not inventory position.
- [ ] Adapt the mixer to dynamic eligible/skipped LoRAs, exact deterministic preview, persistent authoring settings and bounded manual chunks with global indices. Never automatically rerun an uncertain or previously submitted chunk.
- [ ] Keep original Draw Things marker persistence and sequential path isolated from the new DTFlow extensions. Keep large-sweep limits, skipped reasons and reduced-fidelity configuration visible.
- [ ] Extend offline preview definitions only; sync guides/status/release notes and source-review the changes without execution.
- [ ] Commit/push and evolve the draft PR; update #216 progress without premature closure.

## Design notes

- Inventory enumeration must not scan arbitrary directories or return credentials, backend endpoints, private paths or remote receipt identifiers. Cached inventory may be stale and must disclose that; metadata compatibility is not runtime/model acceptance.
- Preferences are convenience authoring state, never a saved queue, remote inventory, execution cursor authority or dispatch consent. Unknown/newer schemas remain untouched; fixed bounded storage and source-version isolation prevent cross-version positional recalls.
- Plans larger than one run use deterministic start/end chunks under the actual captured generation cap; continuation is explicit and uses prior durable attempts to decide what is safe. No new auto-resume dispatcher or replay mechanism.
- The original supplied archive is reference material only; do not rewrite it or expose private inputs in diagnostics. Backend acceptance for the exact checkpoint/LoRAs is not inferred from the portable script.

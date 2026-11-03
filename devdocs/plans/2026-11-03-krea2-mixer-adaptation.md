# Plan: finish the portable Krea2 mixer adaptation (#216)

## Scope and acceptance boundary

- Base: merged PR #218, `9e767fe`. Implement read-only LoRA enumeration, a genuinely non-submitting Setup preview, source-version-scoped mixer preferences, and explicit deterministic bounded chunks. Preserve the original Draw Things fallback and the pinned upstream facade.
- DTFlow uses a disclosed reduced-fidelity preset: inherit Generate's checkpoint/steps and unchanged unsupported values; change only supported dimensions, sampler, guidance, seed and LoRAs. Do not claim the original iOS preset's shift/clip/tile/guidance-embedding behavior or broaden #115 without evidence.
- No tests added/modified/run, checks, renders, app launch, model downloads, uploads, inference or provider calls by the agent. Owner-only fake/native/backend/packaged acceptance is required before closing #216; implementation presence alone is not full epic acceptance.

## Actions

- [x] Inspect issue, canonical services, existing portable source, merged main and instructions; create a separate feature branch.
- [x] Add a bounded read-only DTFlow inventory facade, using existing catalogue/backend metadata and prepared receipts; keep listed/prepared/compatibility/unknown evidence separate.
- [x] Let explicit preparation forms advance while the original child remains behind the queue admission gate. Preview must neither create a queue nor open a backend session. Run Script remains separate consent.
- [x] Add bounded versioned preferences scoped to the exact source SHA, independent of PNG markers, sandbox outputs and dispatch authority. Store LoRA selections by identifier, not inventory position.
- [x] Adapt the mixer to dynamic eligible/skipped LoRAs, exact deterministic preview, persistent authoring settings and bounded manual chunks with global indices. Never automatically rerun an uncertain or previously submitted chunk.
- [x] Keep original Draw Things marker persistence and sequential path isolated from the new DTFlow extensions. Keep large-sweep limits, skipped reasons and reduced-fidelity configuration visible.
- [x] Extend offline preview definitions only; sync guides/status/release notes and source-review the changes without execution.
- [x] Open draft PR #219 after the initial coherent plan checkpoint; source implementation, docs and acceptance scope evolve on this same branch. Keep #216 open pending owner gates.

## Design notes

- Inventory enumeration must not scan arbitrary directories or return credentials, backend endpoints, private paths or remote receipt identifiers. Cached inventory may be stale and must disclose that; metadata compatibility is not runtime/model acceptance.
- Preferences are convenience authoring state, never a saved queue, remote inventory, execution cursor authority or dispatch consent. Unknown/newer schemas remain untouched; fixed bounded storage and source-version isolation prevent cross-version positional recalls.
- Plans larger than one run use deterministic start/end chunks under the actual captured generation cap; continuation is explicit and uses prior durable attempts to decide what is safe. No new auto-resume dispatcher or replay mechanism.
- The original supplied archive is reference material only; do not rewrite it or expose private inputs in diagnostics. Backend acceptance for the exact checkpoint/LoRAs is not inferred from the portable script.

## Source delivery and remaining gates

- Draft [PR #219](https://github.com/djebaz/dtflow/pull/219) opened after the initial planning checkpoint. The portable adapter is version 00015; the supplied archive is untouched. Inventory projection freezes on first read, returns 100-row pages of at most 4096 known LoRAs, bounds identifiers/display labels, and exposes no endpoint/path/remote receipt fields. The native form requires filtering above 400 candidates and explicitly reports bounded skipped text.
- `script_preferences.sqlite3` schema 1 is independent of attempts schema 6 and ScriptRun schema 3. It stores at most 128 exact source versions and 256 KiB ASCII JSON per version; no eviction or execution cursor. Preferences are not uploaded, cache-purged or treated as consent. Unknown schemas fail closed; failed saving leaves active inputs intact and visibly disclosed.
- Preparation-only form ordinals use the existing durable bridge. They do not grant `_start_permitted` or queue admission. Run Script service checks reject marked, in-between or completed previews; generation-first code still gets the ordinary explicit ready form. The UI uses existing Designer hosts; no alternate layout/dialog, protocol client or dispatcher.
- The exact chunk preview uses the captured baseline and explicit positive seeds. Global job indices remain stable across deterministic chunks; the full source-scoped plan SHA and index enter existing request evidence. Continuation is manual, with changed-plan warnings, not an assertion that earlier jobs succeeded or can safely repeat.
- Plan fingerprints bind selected prepared content SHA/size internally without returning raw receipt references/hashes. Recheck after receipt refresh at admission, before batch submission; changed prepared content requests a new preview. Raw built-in model bytes remain unverified backend observations. The analyzer recognizes the explicit `pipeline.runBatch` overlay separately from the unchanged pinned facade; alias/global dynamic calls remain conservatively disclosed.
- Offline state definitions cover settings preview, exact preview (wide/narrow), final Run Script review and completed preview. They are not rendered. The adapter keeps all parameters in one settings form, followed by read-only plan/consent stages rather than guessed aggregation of arbitrary future forms.
- [ ] Owner: zero-provider/zero-queue preview with Fake inventory, eligible/skipped/unknown reasons and exact job/seed/weight counts; edit/finish/Run consent; prefs/newer schema and inventory/source changes; Stop/close/admission races; one explicit fake generation. No acceptance claim is made from source inspection.
- [ ] Owner: exact checkpoint/LoRA/backend and original Draw Things fallback smoke; supported reduced-fidelity preset versus #115; native Windows/DPI, packaged and broader fault coverage via #198. Keep #216 open until these explicitly scoped results are recorded. No agent test/check, app launch, provider request, upload/download, generation or billable action performed.

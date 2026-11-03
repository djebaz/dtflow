# Plan: finish the portable Krea2 mixer adaptation (#216)

## Scope and acceptance boundary

- Base: merged PR #218, `9e767fe`. Implement read-only LoRA enumeration, one preloaded Setup form and direct Run Script, source-version-scoped mixer preferences, and explicit deterministic bounded chunks. Preserve the original Draw Things fallback and the pinned upstream facade.
- DTFlow uses a disclosed reduced-fidelity preset: inherit Generate's checkpoint/steps and unchanged unsupported values; change only supported dimensions, sampler, guidance, seed and LoRAs. Do not claim the original iOS preset's shift/clip/tile/guidance-embedding behavior or broaden #115 without evidence.
- No tests added/modified/run, checks, renders, app launch, model downloads, uploads, inference or provider calls by the agent. Owner-only fake/native/backend/packaged acceptance is required before closing #216; implementation presence alone is not full epic acceptance.

## Actions

- [x] Inspect issue, canonical services, existing portable source, merged main and instructions; create a separate feature branch.
- [x] Add a bounded read-only DTFlow inventory facade, using existing catalogue/backend metadata and prepared receipts; keep listed/prepared/compatibility/unknown evidence separate.
- [x] Owner UX correction: remove the preview-only bridge, hooks, wizard and extra consent. Use the existing parked Setup form and ordinary Run Script queue admission.
- [x] Add bounded versioned preferences scoped to the exact source SHA, independent of PNG markers, sandbox outputs and dispatch authority. Store LoRA selections by identifier, not inventory position.
- [x] Adapt the mixer to dynamic eligible/skipped LoRAs, deterministic jobs, persistent authoring settings and bounded manual chunks with global indices. Never automatically rerun an uncertain or previously submitted chunk.
- [x] Keep original Draw Things marker persistence and sequential path isolated from the new DTFlow extensions. Keep large-sweep limits, skipped reasons and reduced-fidelity configuration visible.
- [x] Extend offline preview definitions only; sync guides/status/release notes and source-review the changes without execution.
- [x] Open draft PR #219 after the initial coherent plan checkpoint; source implementation, docs and acceptance scope evolve on this same branch. Keep #216 open pending owner gates.

## Design notes

- Inventory enumeration must not scan arbitrary directories or return credentials, backend endpoints, private paths or remote receipt identifiers. Cached inventory may be stale and must disclose that; metadata compatibility is not runtime/model acceptance.
- Preferences are convenience authoring state, never a saved queue, remote inventory, execution cursor authority or dispatch consent. Unknown/newer schemas remain untouched; fixed bounded storage and source-version isolation prevent cross-version positional recalls.
- Plans larger than one run use deterministic start/end chunks under the actual captured generation cap; continuation is explicit and uses prior durable attempts to decide what is safe. No new auto-resume dispatcher or replay mechanism.
- The original supplied archive is reference material only; do not rewrite it or expose private inputs in diagnostics. Backend acceptance for the exact checkpoint/LoRAs is not inferred from the portable script.

## Source delivery and remaining gates

- Draft [PR #219](https://github.com/djebaz/dtflow/pull/219) opened after the initial planning checkpoint. The portable adapter is version 00017; the supplied archive is untouched. Inventory projection freezes on first read, returns 100-row pages of at most 4096 known LoRAs, bounds identifiers/display labels, and exposes no endpoint/path/remote receipt fields. The native form requires filtering above 400 candidates and explicitly reports bounded skipped text.
- `script_preferences.sqlite3` schema 1 is independent of attempts schema 6 and ScriptRun schema 3. It stores at most 128 exact source versions and 256 KiB ASCII JSON per version; no eviction or execution cursor. Preferences are not uploaded, cache-purged or treated as consent. Unknown schemas fail closed; failed saving leaves active inputs intact and visibly disclosed.
- One ordinary Setup form contains all parameters. No preview action, review wizard or intermediate form. Run Script alone grants queue admission.
- Global job indices and source-scoped plan identity remain in request evidence. Continuation is manual; matching inputs do not prove earlier jobs succeeded or can safely repeat.
- Plan fingerprints bind selected known prepared content internally. Jobs are constructed after Run Script admission; no preview-to-dispatch gap or preview recheck remains. Dynamic calls stay conservatively disclosed.
- Offline definitions now cover the single mixer Setup (wide/narrow), concise inspection warning and parser-resource failures; not rendered.
- [ ] Owner: Inspect preloads Setup without queue/provider; one form contains all parameters; Run Script directly submits. Check unknown metadata advisory, preferences, chunks, Stop/close and fake generation. Not run by the agent.
- [ ] Owner: exact checkpoint/LoRA/backend and original Draw Things fallback smoke; supported reduced-fidelity preset versus #115; native Windows/DPI, packaged and broader fault coverage via #198. Keep #216 open until these explicitly scoped results are recorded. No agent test/check, app launch, provider request, upload/download, generation or billable action performed.

## Owner feedback follow-up

- [x] Widen the Remove hit target to 22 × 22 px while retaining its inset 14 × 14 px visual and shared confirmation behavior. Register the presentation-only promoted Designer button; update normal/armed/narrow offline fixture annotations. No tests, renders or runtime validation performed.

- Remove button size reduced from 28 × 28 to 14 × 14 px per owner feedback, with a smaller glyph and 3 px corner radius. The armed state keeps the same target size.

- [x] Add Designer-owned rounded-square Remove buttons to script library cards. Reuse the shared button-only double confirmation, Escape/navigation cancellation and danger tokens; remove exact-version index membership asynchronously, retain immutable source for queued work and history, protect active runs/dirty drafts, and extend offline normal/armed/narrow definitions without rendering.
- [ ] Owner: click/arm/cancel/confirm, removing selected/unselected/last version, dirty draft and active-run safeguards, reimport and preserved history/output acceptance. No tests or checks run.

- Removed preview entirely at the owner’s request. Shortened the static-inspection warning and footer; metadata-unknown LoRAs no longer require an opt-in. Grouped prompts, mixing, output, chunk and inventory in one form.
- Parser resource exhaustion now reports Analysis incomplete without a fabricated source location; reserved parser native-stack headroom leaves runtime limits unchanged. No tests, checks, renders or app launch performed.

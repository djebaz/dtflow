# Prepare conversion reuse (#403)

## Scope

Extend the existing explicit Prepare worker and retained store. Keep the converter, source inspection, consent, upload, credential ownership and durable schemas unchanged. Refs #403. The owner-created branch is `403-prepare-reuse-or-refresh-previous-lora-conversions`.

## Implementation

- Prefer verified current numeric converter versions, then the highest newer version; timestamps break ties.
- Treat older or unknown provenance as requiring conversion; skip damaged candidates using the shipped #416 behavior.
- After output bytes are known, allocate a collision-safe name using the existing allocator; retry retention at most once.
- Carry the final retained identifier through per-item Prepare metadata, asset negotiation and ordinary progress.
- Preserve old accepted bytes, names, metadata and receipts; keep malformed-index and unsafe-path failures.
- Emit bounded count/version/source-reference diagnostics through the existing pipeline.

## Evidence boundary

The first issue comment owns AC-1 through AC-13 and their Must/Should levels. The PR copies these unchanged. No new or modified tests, fixtures, screenshots, model downloads, conversion runs, uploads or provider generation are authorized during implementation. Static checks do not verify runtime ACs; owner/CI evidence remains separate. Synthetic version labels or output substitution would prove decision/plumbing behavior only, not real cross-release compatibility.

## Progress

- PR review follow-up: show the actual managed identifier in editor row status without changing the typed draft or consent. Reuse preserves the retained name; explicit Re-Prepare remains the rename path.
- Validate non-canonical retained identities before metadata backfill or successful-reuse reporting; fail with an explicit backup-recovery message rather than silently renaming accepted data or reconverting into the same invalid index.
- Refresh accepted-name reservations after conversion and again before the bounded retention retry. Authoritative retention/registry checks still handle concurrent changes after that snapshot.
- Follow-up validation is static only; earlier owner smoke and CI apply to `18191665`, not the revised head. No tests or renders added or run.
- Source implementation and focused review completed; draft PR #435 tracks source/static evidence separately from pending runtime ACs.
- Environment readiness, changed-module imports and focused Ruff checks passed; no tests or renders ran. The exact final source is rechecked before push. No source conversions, uploads, providers, model downloads or inference ran.

## Runtime evidence to collect later

Use the first-comment checks as a plan, not commands that already exist. There is no complete #403 synthetic runner, and creating one is not authorized under the current no-tests/no-fixtures rule. A future authorized real-source smoke can observe `lora_convert.prepare_started`, `lora_convert.prepare_completed` and repeat `lora_convert.prepare_reused`; the new `lora_store.conversion_candidates`/`conversion_selected` events explain numeric version selection. Do not treat log presence alone as proof of retained bytes, provider identity or cancellation. Synthetic provenance edits and different-output substitution, if later authorized, use disposable state only and establish plumbing behavior, not real cross-release compatibility.

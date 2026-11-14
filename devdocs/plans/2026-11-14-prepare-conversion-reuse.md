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

- Initial source implementation prepared; source review and documentation reconciliation are in progress.
- Environment readiness and changed-module imports passed; no tests or renders ran.

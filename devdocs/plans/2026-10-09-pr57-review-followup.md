# Plan: PR #57 source review follow-up

## Scope
- In: Fail-closed restoration reconciliation, bounded Trash preflight database reads and List header positioning on horizontal scroll/section movement.
- Out: Content-hash schema migration or new recovery UI, Qt adapter/receipt changes, export rules, tests, smoke and packaging.

## Action items
- [x] Remove path/size/mtime automatic restoration; conservatively withdraw historical heuristic restored outcomes without deleting files.
- [x] Read selected preflight records and path-reference counts in one snapshot per at-most-128 batch; keep per-file filesystem validation and per-image execution revalidation.
- [x] Track header checkbox position on horizontal scrollbar changes and section moves.
- [x] Sync contracts/docs and confirmation wording; locked Ruff lint/format, syntax/import/API linkage and diff checks passed. Commit/push this checkpoint to PR #57 and synchronize its body.

## Decisions and Design Changes
- 2026-10-09: Choose the review's conservative recovery option. No trustworthy stored content fingerprint exists; Refresh cannot prove content identity. Recycled and previously heuristic-restored records remain hidden until a future explicit content-verified recovery workflow. Reconciliation still settles interrupted intent without replay. No ledger schema or file-content changes.
- Preserve Qt boundary, strict receipts, durable ledger and shared export controller. Preflight batching reduces repeated database setup, not the authoritative per-file safety checks immediately before mutation.

## Open questions
- None.

## Validation
- Static source checks only. No tests, GUI smoke, Trash/export operations, training/inference, provider calls, benchmarks or billable actions. Prior owner smokes do not accept this follow-up; recovery/preflight/header-scroll smoke remains pending.

Historical heuristic restored records are excluded by gallery/export discovery and preflight even before reconciliation withdraws them. The ledger schema and original files remain unchanged. Static Ruff lint/format, Python syntax/import/API linkage and Git diff checks passed; no runtime or benchmark claim.

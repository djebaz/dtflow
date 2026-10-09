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

## Gallery checkbox glyph parity follow-up
- 2026-10-09: Owner reports checked List indicators show a white tick, but Grid and Detail do not. Extend the shared gallery-specific indicator selector groups to the existing `galleryCardCheck` and `galleryDetailSelectionCheck` object names, reusing the same checked/partial SVG assets and token-based states. Preserve Grid wrapper/geometry, Detail layout, selection/controller behavior and unrelated application checkboxes. Perform theme/resource/static checks only; owner visual smoke remains pending.

Glyph parity static checks passed: shared selector groups, token expansion, absolute SVG resource resolution, SVG XML, Ruff and diff checks. No GUI, tests, smoke, export/Trash operation, inference or billable action; owner visual acceptance remains pending.

Owner reports smoke tests validate the checkbox style fix at 8ec3a55 (2026-10-09). Record glyph-parity visual acceptance only; recovery/preflight/header-scroll and broader DPI/platform/packaged acceptance are separate. This documentation update changes no source and runs no tests, GUI, file operations, inference or billable actions.

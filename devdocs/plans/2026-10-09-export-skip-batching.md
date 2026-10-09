# Plan: Restore bounded repeat-export skips

## Scope
- In: Replace per-skipped-image full record/connection/lock work with bounded identity-only availability checks; preserve recycle coordination and real-export behavior.
- Out: Destination indexes, original or exported file changes, encoding changes, tests, smoke, benchmarks, dependencies and packaging.

## Action items
- [x] Add a read-only primary-key-targeted availability query for at most 128 identities.
- [x] Check consecutive existing-export identities in short locked batches; preserve per-image locks for actual exports and per-ID failures, progress and bounded cancellation.
- [x] Sync architecture/contracts/status/release notes; Ruff lint/format, syntax/import and diff checks passed in the locked environment. Commit/push this checkpoint to PR #57.

## Decisions and Design Changes
- 2026-10-09: Source comparison with bb92217 identifies the regression: every skip now locks originals and opens/attaches SQLite for a full gallery record. The safety check stays, but a selected-attempt join and identity-only query avoids full gallery projection and repeated setup. Batch only consecutive skips so no availability snapshot spans a slow real export. Count each batch while holding its lease; release before progress notification or the next batch.
- Owner reports smoke passed for the checked-only Images panel at c7a8f70. This does not validate skip performance; no measured speedup is claimed.

## Open questions
- None.

## Validation
- No tests, smoke or benchmarks run; owner repeat-export/mixed/missing/recycled/cancel smoke remains pending. No actual export/Trash operation, inference or billable action.

## Owner smoke acceptance (2026-10-09)

Owner reports smoke tested at runtime head 314737b and repeat-export skipping is very fast again. Record repeat-skip responsiveness as owner-accepted, not an agent test or measured benchmark. Mixed new/existing targets, recycled/unavailable records, cancellation and broader packaged/platform scenarios remain separate acceptance gates. This documentation follow-up changes no source and runs no tests, export/Trash operation, inference or billable action.

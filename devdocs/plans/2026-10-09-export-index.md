# Plan: fast repeat-export index

## Scope
- In: bounded recoverable destination index, quick exported-file fingerprint checks, skip saved identities before SQLite per-item lookup/source reads/detection/encoding.
- Out: source/output rewrites, new dependencies, filename-only identity guesses, job/provider changes, cross-process export locking.

## Action items
- [x] Implement validated index persistence and incremental destination discovery.
- [x] Update index after completed exports; early skip by durable identity; preserve missing-copy recovery and collision protection.
- [x] Sync docs and static checks; delivery continues in PR #55.

## Decisions and Design Changes
- 2026-10-09: Owner reports checksum-based repeat skips are slow and approves an export index.
- .dtflow-export-index.json is rebuildable destination-local convenience data, not application state or a durable success ledger. Store only relative filenames, saved identities, original PNG checksums and exported-file and JSON-sidecar size/mtime fingerprints; no private source paths, credentials or full prompts.
- Index root/noise/blank JPEGs once. Every run enumerates those folders and checks cheap file fingerprints; new/changed candidates alone get bounded EXIF/sidecar inspection. Missing entries are pruned; unrecognized files may be cached but never confer identity.
- Durable saved-output identity is the early skip key; committed originals are treated as immutable, so no source checksum reread on a cache hit. External original modifications under the same identity are not detected; use a fresh folder to re-export intentionally altered originals.
- Flush index after initial discovery, every 25 completed copies and at end/cancel. If an index checkpoint is lost, unindexed JPEG discovery repairs it next run. Index-write failures warn without invalidating completed copies.
- Index validation failure rebuilds from export metadata; bounded scan is preserved. Atomic replacement prevents partial JSON but is not a cross-process deduplication lock.

## Open questions
- None blocking.

## Validation
- [x] Whole-repo Ruff lint/format, AST/imports, dtflow help and diff checks passed. No new tests or export invocation.
- No new tests or export/GUI smoke under fast-implement; owner repeat-speed, index rebuild, changed/missing-file and privacy acceptance remains pending.
- The previous turn's 426-pass full suite is historical, not validation of this index change. No generation, inference, provider calls or billable actions.

# Unreleased

Changes through 6.5.0 are consolidated in [DTFlow 6.5.0](v6.5.0.md). The published GitHub release and its artifact identity establish availability.

## Highlights

## Added

## Changed

- Independent LoRA conversions and DT+ Prepare can coexist with Downloads and Generate. Temporary contention visibly queues/waits and resumes automatically; same-content/name ownership preserves retained bytes and receipts. Three download transfers remain unchanged. Refs #548.

## Fixed

- Restart recovery preserves the recorded outcome of finished workflows if DTFlow exits before settling their queue item.

## Upgrade and compatibility

## Validations checklist

## Release audit

- PRs: #584.
- Scope: Version bump 6.5.0 and release-note consolidation; no subsequent changes recorded.


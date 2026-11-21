# Video queues — issue #546

## Scope and decisions

Use ordinary generation queue items and the existing rolling dispatcher. Generate remains a single interactive run. Repeat and the independent composer capture video requests from Generate, retain duration/source identity, and use the existing Connection concurrency default (maximum 10). Raising this to 20 is not authorized. Workflows, Scripts, video templates and Hires Fix are outside this slice.

Queue creation must stay local and paused unless the owner explicitly uses the existing Start/Add-to-queue actions. Start-image bytes must be retained durably and hash-checked, not depend on the original file remaining in place. Dispatch uses the same exact-checkpoint start-image permits as Generate. No video retry path, including safe repair, may run automatically.

## Implementation sequence

1. Shared video queue validation, durable source capture and exact-scope dispatch permits; preserve manual Retry/Relaunch and zero automatic retries.
2. Explicit process/payload/disk limits and pre-submission tool/storage admission for concurrent video pipelines. Limits are safety policy, not measured RSS or provider throughput claims.
3. Enable the existing Generate queue popup and independent composer; expose captured video settings and per-item live video phases through existing table/activity presentation.
4. Synchronize desktop/queue guidance, source-review the resulting diff and run only non-test, non-render static checks.

## Acceptance and evidence

The canonical AC-1 through AC-8 and their Must/Should levels are in [issue #546's first comment](https://github.com/djebaz/dtflow/issues/546#issuecomment-7064485245). All behavioral criteria initially remain unverified. The owner supplies Windows 2/5/10-clip concurrency, failure and Stop evidence; no agent tests, renders, inference or paid provider requests are authorized. CI runs only through normal PR readiness, never manual dispatch.

## Progress

- Initial source investigation: existing queue dispatcher already rolls concurrent ordinary items, artifact saving already supports MP4/poster/sidecar, and Generate already disables video automatic retries. Queue admission, start-image permits, UI capture and resource enforcement require deliberate extension.
- Implemented local capture/validation, content-addressed durable sources, shared Generate/Queue permits, manual Retry source revalidation and zero-retry safe-repair gating. Existing schemas retain the optional video request fields; no database migration is introduced.
- Added process-local video pipeline/buffer/disk admission through final save, pre-submit storage recheck after backend awaits, bounded tensors/audio/encoder queue/output files, two codec threads and the existing video deadline for queue items. The [resource policy](../guides/queues.md#video-resource-policy) distinguishes logical accounting from actual/native RSS and external disk consumption.
- Enabled the existing Generate Queue popup and composer capture, exposed video summaries and transient row/activity phases, and keyed the existing GUI relay's transient coalescing per queue/item/kind. Generate remains single-run; 20 slots and video templates are refused, not silently downgraded.
- No automated tests/fixtures were created, modified or run, no renders were added/run and no provider/inference/billable actions occurred. Runtime, failure/Stop, capacity, native-memory and Windows/packaged acceptance remain pending owner evidence and ordinary eligible CI.
- Local non-test checks passed: configured environment readiness; focused Ruff lint/format; changed-Python syntax/import checks; theme-token expansion; canonical main-window Designer XML compilation; `dtflow queue --help`; `git diff --check`. No test files changed. Existing tests were deliberately not updated; CI/maintainer coverage may need to reflect the newly admitted video queue behavior.

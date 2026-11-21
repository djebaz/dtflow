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

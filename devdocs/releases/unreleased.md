# Unreleased

Changes through 5.2.0 are consolidated in [DTFlow 5.2.0](v5.2.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

- Check the restored connection once in the background at desktop startup; retain its last-check indicator across navigation without generating or resuming work.

- Save and reload named text-to-image queue recipes, with searchable selection, explicit update/rename/delete, dirty-draft replacement and backend-rebinding review. Original prompts, variation settings and seeds stay independent of Generate and queue history; loading never starts work.

## Changed

- Load optional credential and library summaries on workers, discover saved credentials only when needed, and construct Logs widgets on first use. Native Windows startup speedup remains to be measured.

## Fixed

- Queue concurrency coverage checks FIFO at durable admission, without assuming asynchronous preparation reaches the backend in the same order.

- Queue-history thumbnails and hover previews stay visible during background availability refresh instead of blinking empty. Failed checks still clear previews, and Open in Images continues to require fresh availability.

## Release audit

- PRs: #164, #166, #167, #171.
- Scope: Version bump 5.2.0 and release-note consolidation; queue thumbnail refresh continuity correction; local queue authoring templates and durable-admission FIFO coverage; startup connection check and deferred optional startup work (#168/#169). Exact Windows executable acceptance and publication remain separate.

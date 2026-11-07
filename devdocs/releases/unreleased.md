# Unreleased

Changes through 5.5.0 are consolidated in [DTFlow 5.5.0](v5.5.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

## Changed

- Scripts keep a run in its queue item (#243, part of #220). The separate `script_runs.sqlite3` run database, its per-run lock files and the `script_child_slots` table are gone; child links, forms and answers are written into the item by the dispatch owner before they take effect, and crash recovery is the one queue reconcile (a running run becomes interrupted, `unknown` while a step is unresolved, never replayed). Form answers from another process (Scripts, `dtflow script --answer-run`) are posted as queue controls and checked again before the script sees them. **One-way upgrade to attempts schema 9**: the existing database is backed up first, each script item's run is copied from the old file (left untouched and no longer written), and older DTFlow versions refuse the upgraded state. `dtflow script --run` without `--queue` now also creates a one-item script queue, starts it and reports from it (it shows in Queues); the report adds `queue_id` and `item_state`. When a step fails in Scripts, the form now has two buttons, Retry with the same settings and Continue without this image, instead of a choice and Apply.

## Fixed

- The bottom-bar DT+ completed-image counter now schedules its local saved-evidence refresh on activity changes from every workspace, rather than only while Connection is open. No provider polling or generation behavior changes.
- Workflows bind existing prepared custom DT+ LoRA receipts for Generate, Inpaint and Img2img nodes, independently of ordinary Generate's selection. Previously names/weights reached dispatch without their uploaded-content binding, so the cloud request could omit the custom LoRA metadata. Invalid bound receipts stop the affected node before image submission; check the active account and reconcile or explicitly prepare the asset again. Pause is rechecked after receipt binding. No implicit preparation/upload or replay is added.
- Workflow cloud LoRAs with unknown availability and no bound preparation receipt now produce an advisory amber overlay, retained output/node warnings and a warning in the Queues activity log. Normal diagnostic logs retain a privacy-safe warning summary. Existing saved outcomes and generation consent are unchanged.
- LoRA warning wording no longer assumes an unconfirmed cloud LoRA is custom. Opening historical finished workflow runs retains warning details without replaying the overlay; fresh completion warnings remain visible.
- The existing queue Pause/Resume/Cancel check no longer assumes concurrent preparation reaches the backend in FIFO order. Durable admission ordering and queue-control outcome checks remain unchanged.

## Release audit

- PRs: #238, #246
- Scope: Workflow-only prepared custom cloud LoRA receipt binding, Pause-after-binding admission, invalid-receipt recovery guidance and retained missing-receipt warning presentation; global bottom-bar DT+ counter refresh; offline render definitions for GitHub Actions. No provider uploads or automatic replay.

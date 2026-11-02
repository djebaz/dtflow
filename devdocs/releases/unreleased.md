# Unreleased

Changes through 5.4.0 are consolidated in [DTFlow 5.4.0](v5.4.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

## Changed

## Fixed

- PR #201 independent-review follow-up: offline terminal/review actions, guarded Start/queue/input previews, fixture syntax checks and in-memory version Save, live/unresolved history cues, detached Inpaint source/mask entry and local form/editor/results polish. Native Photos/Files pickers are supported in the target design, with runtime hook integration deferred. No real script execution, parser, persistence or provider calls are connected.

- PR #201 Scripts desktop preview: Designer-owned static views, visible library actions, wrapped form rows, distinct queued/cancelled evidence and Cancel/waiting presentation, selected-script inspection facts, saved-versus-draft status, Find/Replace and inline dirty-draft protection. The labelled Inspector toggle sits at the right edge of the Scripts toolbar with a right-sidebar SVG, not beside the product logo. Inspector drawer and offline source/mask fixtures remain presentation-only; runtime Start/save/history integration is not included.


## Scripting (PR #199, pending merge)

- Added opt-in headless execution for locally supplied Draw Things JavaScript scripts with an explicit fake/local/cloud backend, bounded run generations, durable ScriptRun/child attempt links, headless form answers and per-run sandbox files.
- Unsupported changed configuration fields and masks fail before generation; empty model identifiers are rejected. Missing/unreadable script paths report their concrete file error.
- Owner Windows source smoke verified two fake generations and empty-model rejection (2026-11-02). Native UI, Tier B/detection, local/cloud acceptance and packaged execution are not included. No automated tests added or run by owner direction.

## Release audit

- PRs: #193, #201.
- Scope: Version bump 5.4.0 and release-note consolidation; Scripts Qt preview review corrections and canonical Designer layout migration, without runtime execution integration; exact Windows artifact acceptance and publication remain separate.

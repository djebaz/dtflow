# Plan: Queue pose editor (#515)

## Scope

- In: edit the Queue draft's ControlNet checkpoint/quantization, strength, control force and apply range; show its attached map and explicitly replace it with a prepared body map. Support captured poses, loaded templates and Edit as new from recorded queues.
- Out: photo extraction, joint editing, broader model compatibility, provider calls during editing, tests/fixtures/renders, packaging and unrelated branch-cleanup tooling.

## Action items

- [x] Inspect issue/ACs, current main, independent drafts, pose preparation and queue/template persistence paths.
- [x] Mount a compact canonical Designer form and bind controls to the existing draft representations without modifying Generate or saved queues/templates.
- [x] Decode previews and prepare replacement maps on workers; reject invalid input and stale deliveries without losing the previous map.
- [x] Preserve exact edited controls across queue creation and explicit template Save/Update, retaining current validation, size limits and consent boundaries (source implementation; behavioral acceptance pending).
- [x] Update design/user/developer guidance and release notes; source-review the complete change and report static evidence separately from owner/CI acceptance.
- [ ] Commit/push coherent checkpoints and prepare the feature PR for review without manually dispatching CI.

## Decisions

- Preserve both existing draft paths: captured/template poses use immutable PoseJob; recorded queue drafts retain their request and verified map bytes. Control edits must update the matching template projection too.
- Explicit map replacement creates a fresh PoseJob and clears old execution inputs and extraction evidence. It preserves base settings, fixed LoRAs, seeds and plan variations; no staging or provider operation occurs until existing explicit queue actions.
- Numeric editors must not clamp imported values. The existing PoseControl constructor remains the validator; invalid in-progress text blocks queue/template capture rather than falling back to old values.
- Control choices come only from Generate's already cached supported inventory. Retain an unavailable saved identifier visibly, without substituting another control or claiming availability.
- Draft PR #522 was opened after the initial coherent plan commit. `queue_pose_editor.ui` owns static controls; `QueuePoseEditor` binds them and calls only shared pose loading/fitting/validation. The existing recorded-map template projection is updated with edited controls, preventing an explicit Save/Update from retaining stale settings.
- Keep ordinary text-to-image/Inpaint behavior and template/database schema unchanged. Preview/load work is local and bounded; stale results are rejected on draft, settings, navigation or shutdown changes.

## Evidence and acceptance

- Initial state: clean main at `44e43bbb`, equal to current origin/main. Issue #515 and first-comment AC-1 through AC-8 are the scope/acceptance authority.
- No tests, fixtures, renders, inference, uploads, builds or billable operations are authorized. Behavioral Must ACs remain pending appropriate owner/CI evidence; static inspection does not establish Windows/DPI or backend acceptance.
- Passed the documented locked readiness check (Python 3.13.13, SQLite 3.53.1), focused Ruff lint, Python AST/import checks and Designer XML parsing. Ruff initially identified a missing explicit zip strictness flag; corrected to `strict=True` before the passing lint run. No widget/render was created by the source import checks.
- Final focused Ruff lint/format checks, `pyside6-uic` form compilation into ignored scratch Python (not imported/executed), and `git diff --check` passed. No test or fixture files changed.
- Source review traced captured/template controls through PoseJob into create_pose_queue and recorded controls through the request into create_saved_pose_queue, with the retained template control projection updated alongside it. Replacement loads/fits only via existing body-map services, drops prior execution/map evidence and snapshots its target before the picker and worker; existing template serialization and limits remain authoritative.
- The canonical packaging script collects dtflow package data, covering the new tracked `.ui` form; no packaging command or executable acceptance was performed. New feature behavior and visual fit still require owner/CI evidence; AC-5's structural reuse is supported by source inspection, while behavioral ACs remain unchecked.

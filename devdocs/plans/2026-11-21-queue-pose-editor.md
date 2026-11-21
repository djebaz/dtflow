# Plan: Queue pose editor (#515)

## Scope

- In: edit the Queue draft's ControlNet checkpoint/quantization, strength, control force and apply range; show its attached map and explicitly replace it with a prepared body map. Support captured poses, loaded templates and Edit as new from recorded queues.
- Out: photo extraction, joint editing, broader model compatibility, provider calls during editing, tests/fixtures/renders, packaging and unrelated branch-cleanup tooling.

## Action items

- [x] Inspect issue/ACs, current main, independent drafts, pose preparation and queue/template persistence paths.
- [x] Mount a compact canonical Designer form and bind controls to the existing draft representations without modifying Generate or saved queues/templates.
- [x] Decode previews and prepare replacement maps on workers; reject invalid input and stale deliveries without losing the previous map.
- [x] Preserve exact edited controls across queue creation and explicit template Save/Update, retaining current validation, size limits and consent boundaries (source-reviewed; owner functional acceptance recorded below).
- [x] Update design/user/developer guidance and release notes; source-review the complete change and report static evidence separately from owner/CI acceptance.
- [x] Commit/push coherent checkpoints and prepare the feature PR for review without manually dispatching CI.

## Decisions

- Preserve both existing draft paths: captured/template poses use immutable PoseJob; recorded queue drafts retain their request and verified map bytes. Control edits must update the matching template projection too.
- Explicit map replacement creates a fresh PoseJob and clears old execution inputs and extraction evidence. It preserves base settings, fixed LoRAs, seeds and plan variations; no staging or provider operation occurs until existing explicit queue actions.
- Numeric editors must not clamp imported values. The existing PoseControl constructor remains the validator; invalid in-progress text blocks queue/template capture rather than falling back to old values.
- Control choices come only from Generate's already cached supported inventory. Retain an unavailable saved identifier visibly, without substituting another control or claiming availability.
- Draft PR #522 was opened after the initial coherent plan commit. `queue_pose_editor.ui` owns static controls; `QueuePoseEditor` binds them and calls only shared pose loading/fitting/validation. The existing recorded-map template projection is updated with edited controls, preventing an explicit Save/Update from retaining stale settings.
- Keep ordinary text-to-image/Inpaint behavior and template/database schema unchanged. Preview/load work is local and bounded; stale results are rejected on draft, settings, navigation or shutdown changes.

## Evidence and acceptance

- Initial state: clean main at `44e43bbb`, equal to current origin/main. Issue #515 and first-comment AC-1 through AC-8 are the scope/acceptance authority.
- No agent tests, fixtures, renders, inference, uploads, builds or billable operations are authorized. Static inspection does not establish Windows/DPI or backend acceptance; subsequent owner/CI evidence is recorded separately below.
- Passed the documented locked readiness check (Python 3.13.13, SQLite 3.53.1), focused Ruff lint, Python AST/import checks and Designer XML parsing. Ruff initially identified a missing explicit zip strictness flag; corrected to `strict=True` before the passing lint run. No widget/render was created by the source import checks.
- Final focused Ruff lint/format checks, `pyside6-uic` form compilation into ignored scratch Python (not imported/executed), and `git diff --check` passed. No test or fixture files changed.
- Source review traced captured/template controls through PoseJob into create_pose_queue and recorded controls through the request into create_saved_pose_queue, with the retained template control projection updated alongside it. Replacement loads/fits only via existing body-map services, drops prior execution/map evidence and snapshots its target before the picker and worker; existing template serialization and limits remain authoritative.
- The canonical packaging script collects dtflow package data, covering the new tracked `.ui` form; no agent packaging command or executable acceptance was performed. AC-5's structural reuse is supported by source inspection; independently attested executable identity and exhaustive native/visual acceptance remain separate.
- Implementation `585d120b` was pushed to draft PR #522. The initial handoff checked structural AC-5 and left behavioral ACs pending; the owner acceptance below supersedes that initial checklist status. Ready for review requests ordinary eligible CI automatically; no manual Action or Preview dispatch is authorized.

## Owner acceptance and merge preparation — 2026-11-21

- The owner supplied diagnostic session `fd2b833ab12d`, inspected read-only through the shared reader: complete with 618 records, eight cloud generation successes and eight succeeded Queue items, no ERROR/CRITICAL records. Five warnings were one unprepared-LoRA notice and four compatibility-unknown notices; no failed generation was recorded. UI-gap observations are not attributed to this editor or promoted into a performance claim.
- The session reports Windows/Python 3.13/DTFlow 6.4.1 with unknown source commit/build state; the owner confirmed it used PR head `d5017f70`. Rebase onto `82ef7a13` produced code head `c22d8cd8`, with all three feature commits patch-equivalent by range-diff. Automatic [CI](https://github.com/djebaz/dtflow/actions/runs/44865921062) and [Preview](https://github.com/djebaz/dtflow/actions/runs/44865921004) passed on that rebased code head.
- The owner subsequently reported **“all works”** and requested only a separate UI/UX enhancement review before preparing to merge. This is recorded as owner functional acceptance of #515's common paths, combined with source review of preservation/validation/consent boundaries. The agent checks AC-1 through AC-8 on that basis (AC-5 structurally); no levels are changed or Must waived. This is not an invented itemized failure-path test report, independent build attestation, exhaustive DPI/accessibility check or cross-platform acceptance.
- Two independent isolated source assessments reviewed the native pose surface at `c22d8cd8`. No functional or safety blocker was identified. The owner-requested non-blocking UX enhancements are recorded in [#528](https://github.com/djebaz/dtflow/issues/528), with first-comment Should ACs: targeted numeric feedback/semantics, preview/replacement status and failure explanations, larger map inspection, and lower explanatory density. No UX changes are added to this PR, and no known functional defects were deferred.
- The static design detector reported zero findings but did not establish Qt `.ui` coverage, so it supplies no substantive native UI/accessibility pass. No new app launch, tests, renders, providers or smoke reruns occurred; source-only review cannot establish pixel fit, actual loading delays, keyboard traversal or screen-reader announcements.
- This merge-preparation change updates documentation only; the UI implementation remains unchanged. Final-head automatic checks and readiness are recorded on PR #522 after publication. The PR is prepared for the owner's merge, not merged by the agent. Further UX questions are skipped because the owner already selected a separate follow-up issue.

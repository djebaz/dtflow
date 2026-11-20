# Plan: PR #513 review fixes

## Scope

- In: repair the newly enabled pose Generate/Retry queue-behind path and implement owner-approved local pose templates on the existing PR branch.
- Out: tests, fixtures, renders, provider calls, packaging, merge, and changes to local pose staging or acceptance levels.

## Action items

- [x] Pin PR #513 head, inspect the clean branch and review findings, and return the PR to draft during revision.
- [x] Correct queue-behind arguments and single queue-ID return handling.
- [x] Support live and recorded poses with template-owned bounded body-map bytes and exact control settings, not execution identities.
- [x] Synchronize documentation and issue scope/criteria; source-review and run permitted static checks.
- [x] Commit and push implementation to the existing branch; prepare the ready-for-review handoff on PR #513.

## Decisions and Design Changes

- 2026-11-20: Owner approved expanding PR #513 to local pose templates. Supersedes the initial rejection-only repair; retain no attempt identities, input paths, receipts, extraction evidence or consent in recipes.
- 2026-11-20: Keep the schema-1 SQLite store and its existing 256 KiB recipe/4 MiB library/128-name limits. Recipe v2 embeds a SHA-256-verified RGB body map (at most 128 KiB encoded PNG before Base64) and exact pose controls; older text-only recipe v1 stays supported. Oversize/invalid recipes refuse without mutation. No separate asset store or cleanup is needed: map and recipe update/delete atomically together.
- 2026-11-20: Template Save/Load never stages inputs, contacts a provider, extracts a pose or downloads a model. Recorded poses use the already verified retained fitted body map; queue creation fits/stages a fresh input set through the existing pose path. Template loading preserves seeds and never changes Generate or the connection.
- 2026-11-20: Owner requested aligning AC-3 with the implemented boundary: permit inventory refresh and local durable hash-verified map fitting/staging after scope checks, with no upload/generation during creation. Start/Start now or explicit Add to queue scheduling grants later upload/generation consent. Issue #505's first-comment criterion and PR #513's copy retain the same 🟦 Must level. This wording approval is not runtime acceptance or a waiver; the PR checkbox remains unchecked.

## Open questions

- No wording questions remain. AC-3 behavioral verification remains an acceptance gate; owner approval of the revised wording does not supply execution evidence.

## Owner smoke follow-up: plan pose capture

- Owner report: [PR comment 7051433713](https://github.com/djebaz/dtflow/pull/513#issuecomment-7051433713), 2026-11-20, reported while PR head was `7d93d475`. The report does not identify the tested executable/source revision, so that head is context, not proven executable identity.
- Owner confirms Images N and Repeat → Add to queue preserve pose settings; Prepare Queue / New queue plan instead records strength 0.70, Balanced and range 0..1 after Generate was set to 0.85, Control and non-default range. Exact checkpoint/quantization propagation and provider-effective values were not established by the smoke report. AC-1 remains unchecked; no other runtime AC is inferred passed.
- Source trace: Images N/quick Repeat takes the current Generate request and `queue_edit_job`; New queue plan takes `_composer_request` and `_draft_job`, captured earlier by the independent composer. `create_pose_queue` copies that job's control into every request, request serialization preserves it, and the wire uses its exact checkpoint/strength/mode/range with a consistency check. This identifies stale captured input as the source-supported explanation, not provider defaulting; the failing runtime capture timing itself is not yet reproduced.
- [x] Owner selected “Refresh pose explicitly, keep plan variations”: only the explicit Controlnet popup's Plan start/add action reuses Use Generate settings in pose-only mode. It refreshes the map/exact controls while keeping the Queue draft's base, seeds, LoRAs and plan variations (capture Generate's base only for a missing draft); merely opening the popup, Repeat and global/composer Plan remain unchanged. The popup discloses the transfer. Invalid current pose, changed connection or incompatible image draft refuses without falling back to the old draft. Existing reconstruction disclosures and saved template content remain intact.
- [x] Repair the capture/presentation path and source-review persistence/wire propagation. The composer summary and queued Parameters tooltip show the exact checkpoint identifier, strength, force and range; no new logging sink, protocol mapping or diagnostic contract was needed.
- [x] Run only permitted static checks: locked readiness (Python 3.13.13 / SQLite 3.53.1), focused Ruff lint/format and AST/import checks for `queue_page.py`, `queue_popover.py`, `queue_model.py` and `main_window.py`, and `git diff --check` passed. Independent Standards/Spec source reviews found no actionable issue. Final publication/head and current CI evidence are recorded on PR #513; no agent tests/renders/provider calls or paid owner-test reruns were performed. Runtime retest of non-default controls remains pending.

## Validation

- [x] Static checks and source review completed.
- Passed the documented locked environment readiness check (Python 3.13.13, SQLite 3.53.1; Qt initialized offscreen, no UI render).
- Passed focused `uv run --frozen ruff check`, `ruff format --check`, AST parsing and imports for `services/queue_templates.py`, `ui/queue_templates.py` and `ui/queue_page.py`, plus `git diff --check`. The initial format check identified two formatting changes, corrected with Ruff before the final check.
- Independent Standards and Spec source-review passes found no additional actionable defect. Parsing verifies bounded encoded bytes, SHA-256 and declared RGB metadata; full pixel decoding remains in the shared fitting path before queue staging/submission, not a claim of pixel/runtime validation at template load.
- Implementation commit `2a2442871a678b3f4d1cbe80a4437e5c7412aa8a` was pushed to the existing `feat/505-pose-queues` branch. Issue #505's scope/first-comment AC-9 and AC-10 and PR #513's copied criteria/limitations were updated; the new behavior criteria remain unchecked pending execution evidence. PR readiness/current-head CI are reported on GitHub, not inferred from the source checks.
- Tests and smoke tests are not run by the agent; final runtime validation remains with the owner and eligible automatic GitHub CI.
- No inference, training, upload, billable action, render or packaging is planned.

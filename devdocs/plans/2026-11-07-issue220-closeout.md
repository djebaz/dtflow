# Issue #220 — acceptance close-out record (#245)

Updated: 2026-11-07. Source: current `main` after the merged #248–#255 sequence, plus the original acceptance criteria in [#220](https://github.com/djebaz/dtflow/issues/220). This is an evidence map, not a claim that all gates have passed.

## Before / after: friction, simplification, benefit, trade-off

| Before | Implemented simplification | User benefit | Deliberate trade-off and retained protection | Evidence |
| --- | --- | --- | --- | --- |
| Generate/Queues hid useful provider failures behind internal codes. | Share the original meaningful error and plain outcome wording. | Understand what went wrong without opening diagnostics. | Remote outcome remains unknown when unconfirmed; provider messages are not invented. | [#248](https://github.com/djebaz/dtflow/pull/248) |
| Retrying Generate reread the edited form; Queues retried by creating another queue. | Retry the recorded request/seed; Queues retries its item in place. | One action without reconstructing settings or duplicating queues. | Retry may consume credits; old attempt evidence remains. | [#251](https://github.com/djebaz/dtflow/pull/251) |
| No consistent bounded automatic recovery across runners. | A shared one-retry, five-second policy for definite transient failures, plus the owner-approved narrowly matched DT+ gRPC INTERNAL case. | Recover routine transient errors without a second click. | A bounded duplicate submission is possible; ambiguous remote outcomes and hard refusals are not automatically replayed. | [#254](https://github.com/djebaz/dtflow/pull/254), [retry policy](../../src/dtflow/core/retry_policy.py) |
| Script step failure or uncertainty blocked continued generation. | In-run Retry or Continue; clear status for skipped/unknown steps. | Resume work without restarting a whole script. | A continued unknown remains unknown; explicit retry counts toward the run limit. | [#252](https://github.com/djebaz/dtflow/pull/252) |
| Scripts kept a second run database and synchronized it with queue state. | Store the run in the queue item and migrate to execution schema 9. | Fewer recovery mechanisms; a single queue-owned history. | A one-way schema upgrade requires a backup; original results and earlier ledger are preserved. | [#253](https://github.com/djebaz/dtflow/pull/253) |
| Queued edits required extra per-item consent evidence even after Start. | Treat Start as the user's consent while retaining scoped dispatch preparation. | Less internal paperwork with no extra click. | Explicit backend, account, upload and paid-operation boundaries remain. | [#250](https://github.com/djebaz/dtflow/pull/250) |

## Product acceptance criteria — evidence and remaining gates

1. **Before/after inventory:** the table above covers the six implementation slices. **Evidence recorded**; review wording against actual owner experience before final acceptance.
2. **Easy Retry with original settings:** [#251](https://github.com/djebaz/dtflow/pull/251) implements Generate/Queues; [#252](https://github.com/djebaz/dtflow/pull/252) implements Scripts; Workflows already had in-place step retry. Issue #220 records owner smoke for #251 and #252. **Implementation and reported owner smoke exist.**
3. **Bounded repeated transient failures:** [#254](https://github.com/djebaz/dtflow/pull/254) introduces `AUTO_RETRIES = 1` and `AUTO_RETRY_DELAY = 5` shared across Generate, Queues, Scripts and Workflows. The PR reports ad-hoc offline `connect_failed`, INTERNAL and lost-connection checks, but explicitly reports no local pytest run. A focused regression test for repeated transient failure, unknown connection loss, authentication refusal and the approved INTERNAL exception has been added to `tests/integration/test_session.py`. **Verified on Linux CI:** [run 42397417692](https://github.com/djebaz/dtflow/actions/runs/42397417692) executed the offline pytest suite on code commit `5b38f9b` — **698 passed, 3 skipped**. The test covers the shared generation-session path. The distinct script/workflow/queue runner matrix and native DT+ acceptance are not individually proven by that one test.
4. **Remove/consolidate redundant code and tests:** [#253](https://github.com/djebaz/dtflow/pull/253) removes the 550-line `script_runs.py` and separate ledger; [#250](https://github.com/djebaz/dtflow/pull/250) removes per-item permit requirements. The test-module inventory and focused overlap audit below are recorded. No removal is justified solely by overlapping names: session, provider, queue and Qt tests exercise distinct failures. The missing repeated-failure cap check is now covered by a focused offline regression test in this PR, pending execution.
5. **Provenance retained / retries finite:** [#251](https://github.com/djebaz/dtflow/pull/251) retains prior attempts and original settings, [#253](https://github.com/djebaz/dtflow/pull/253) preserves linked script children, and [#254](https://github.com/djebaz/dtflow/pull/254) bounds automatic retries. **Code/PR evidence exists; final acceptance should also check restart, original outputs and migrations.**
6. **Updated guidance and architecture:** implementation PRs updated the Queues, Scripts, Workflows and backend contract guides. This PR reconciles stale `current_dev_status.md` schema and PR status, the scripting architecture's removed ledger/form statements, and a historical backend-contract limitation. Queues, Scripts and Workflows guides already describe the bounded policy. Further acceptance evidence is separate from documentation consistency.
7. **Representative offline/backend/Windows acceptance:** PRs report some fake/offline scenarios and selected owner smoke, but these were not all performed against this exact close-out branch. **No Windows, real-backend or packaged-executable checks have been performed as part of #245 here.** Record each separately; do not infer one from the others.

## Test audit by behavior protected

On 2026-11-07, the current `main` test tree was inventoried: **53 test modules** (`38` unit, `15` integration), plus shared `tests/conftest.py`. Top-level test names and references to retired state machinery were inspected in each module; relevant test bodies were reviewed in the generation, queue, scripts, crash, cloud and Qt suites. This is a focused source audit, **not a pytest collection/execution result**. The **408 test functions** in #245 is a dated issue-body count, not a revalidated count of test cases after parametrization.

| Candidate overlap or obsolete mechanism | Audit decision | Meaningful protection kept |
| --- | --- | --- |
| `test_fake_service.py` success/failure vs `test_session.py` success/busy | **Keep both:** service and durable output evidence vs session ownership/lifetime | Durable writes, unknown outcomes, and releasing the lock between operations |
| `test_queue.py` vs `test_queue_session.py` vs `test_qt_queue.py` | **Keep:** scheduler/SQLite state vs session composition vs real Qt actions | FIFO, bounded admission, active-owner exclusion, restart reconciliation and truthful UI |
| `test_queue_presenter.py` vs Qt queue outcome checks | **Keep:** wording/data projection vs Qt command dispatch and row identity | An unknown item is not silently displayed as confirmed failure or replayed |
| `test_script_batch.py` child slots/restart | **Keep updated schema-9 checks:** uses `lanes_json` and `ScriptRun` rather than removed `script_child_slots` and the separate run store | Atomic child linking and bounded cross-queue slot accounting |
| `test_cloud_assets.py` upload non-retry vs shared generation auto-retry | **Keep:** uploads intentionally have a separate consent and uncertainty boundary | No hidden repeat upload after uncertain transfer |
| `test_drawthings_cloud.py` backend non-retry vs shared generation retry | **Keep:** adapter does not own the session's retry loop | Provider adapter remains single-attempt and reports the original error |
| Legacy `script_runs.sqlite3` and `script_child_slots` test assertions | **No obsolete assertions found** in the inventoried test modules; #253 updated its affected tests | No test removed merely for a smaller number |
| New automatic retry cap (#241) | **Add one focused test** in `test_session.py` with the real service/store and a scripted offline backend | Repeated transient failures stop after two submissions; unknown/hard refusal do not retry; original seed and link evidence preserved |

**Remaining evidence gap:** no dedicated automated v8-with-legacy-script-ledger → v9 migration test was found in the inventoried suite. #253 records ad-hoc migration and recovery scenarios, while the existing `test_queue.py` tests an older schema upgrade/rollback. An owner-controlled upgrade of a real historical state and final Windows acceptance remain unverified. Do not substitute an unrelated migration test for this gate.

Group tests by meaningful behavior rather than aiming for a target count. Remove or consolidate only demonstrated duplicate assertions with an explicitly retained risk test. Across the reviewed #220-relevant suites, the superficially similar checks exercise different layers; no additional deletion is justified now.

### Coverage that must survive

- Definite failure vs unknown remote outcome; provider message propagation and suppression of speculative quota diagnoses.
- Original-request manual Retry, including unchanged seed, in-place queue retry and preserved attempt history.
- One bounded automatic retry and no auto-retry for remote uncertainty, hard refusal, stop or exhausted script/workflow limits.
- Ownership/slot accounting, stop and cancellation, concurrency bounds and script form/picker state.
- Schema 8 → 9 upgrade/backups, interrupted-run reconciliation and old images/metadata still available.

## Acceptance evidence ledger

| Check | Recorded result | Provenance / limitation |
| --- | --- | --- |
| #251 Generate/Queues manual Retry | Owner smoke reported passed | [#220](https://github.com/djebaz/dtflow/issues/220); specific backend/artifact not specified here |
| #252 Scripts Retry/Continue | Owner smoke reported passed | [#220](https://github.com/djebaz/dtflow/issues/220); no new #245 run |
| #253 schema 9 migration and script run durability | Offline scenarios described in PR; merged | [#253](https://github.com/djebaz/dtflow/pull/253); native upgrade and crash acceptance separate |
| #254 automatic retry cap | **Verified for shared session:** 698 passed, 3 skipped on Linux CI, including one parametrized retry-cap regression | [GitHub Actions run 42397417692](https://github.com/djebaz/dtflow/actions/runs/42397417692), source commit `5b38f9b`; runner-specific and live DT+ checks remain separate |
| #256 CI on `e956208e` | Workflow completed successfully but **pytest, Ruff and environment setup were skipped** by the change gate | [GitHub Actions run 42396999415](https://github.com/djebaz/dtflow/actions/runs/42396999415); not a new validation pass |
| #256 CI on `5b38f9b` | **Linux offline pytest completed: 698 passed, 3 skipped (62.07 s)**; lockfile check passed; Ruff steps executed | [GitHub Actions run 42397417692](https://github.com/djebaz/dtflow/actions/runs/42397417692); tests were actually executed, unlike the previous documentation-only run |
| #245 test audit, complete offline matrix and native/real checks | **Source audit + shared-session offline suite complete; native/real acceptance pending** | Code commit `5b38f9b`: 698 passed, 3 skipped; no owner schema-9 live migration, DT+ retry or packaged Windows check performed in #245 |

## Close-out order

1. **Done (source audit):** inventory existing tests for obsolete mechanisms and actual overlap; retain distinct regression protections and add the missing retry-cap scenario.
2. **Done (documentation review):** reconcile stale scripting/status/backend-contract statements with merged behavior, without claiming unperformed runtime acceptance.
3. **Done for the tested code head:** [CI on `5b38f9b`](https://github.com/djebaz/dtflow/actions/runs/42397417692) executed `uv run pytest -n auto`, with 698 passed and 3 skipped. The focused test covers exhausted retries and definitive non-retryable errors; it does not test the distinct queue/script/workflow retry loops. Subsequent documentation-only commits do not change the tested Python source.
4. Record any separately authorized Windows/real DT+ owner checks as evidence, or leave explicitly unverified.
5. Only then tick each supported criterion in [#220](https://github.com/djebaz/dtflow/issues/220) with a link to this record or the owning PR. Close #245 and #220 only after all gates are met.

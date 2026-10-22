# Plan: correct the BYOM upload checksum contract

## Scope

Fix DTFlow's unsupported equality requirement between the locally computed SHA-256 and the server-returned `signedUrls.urls[filename].sha256`. Add safe format/comparison evidence before consuming the upload entry. Keep local content hashing, safe destinations, explicit upload consent, post-upload receipt confirmation and no automatic retry.

## Evidence

- Owner runtime warning identifies `upload_checksum_mismatch` before submission; the redacted body does not expose the returned value.
- Official [LoRAStore.swift](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/MediaGenerationKit/Sources/LoRAStore.swift#L167-L179) computes a lowercase hex SHA-256 for the request. [Response consumption](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/MediaGenerationKit/Sources/LoRAStore.swift#L263-L279) forwards the response field as `serverSha256` to upload completion without requiring equality to the request digest. [Completion](https://github.com/drawthingsai/draw-things-community/blob/82bd56c81a313060dceb34092bcf9d671c39a739/Libraries/MediaGenerationKit/Sources/LoRAStore.swift#L286-L308) returns that server value.
- Retrieved upstream blob: `1e166763ea86049079f8c12a4e455ec445572461`. No upstream code or provider request was executed by the agent during source retrieval.
- This demonstrates that DTFlow's equality rejection is stricter than the official client. It does not prove the encoding or meaning of the owner's hidden returned value, successful upload, or generation compatibility.

## Action items

- [x] Implement source corrections for the owner-reported pytest failure groups after format commit `8e4ad3e`, retaining that commit and using source-only follow-up review.
- [ ] Owner rerun establishes the corrected head's pytest result; keep #158 draft until that evidence is reviewed.
- [x] Accept usable server-returned upload references independently of local checksum equality in negotiation and metadata lookup.
- [x] Record bounded non-identifying type/length/format/comparison diagnostics before consuming upload evidence.
- [x] Synchronize contract documentation, preview definitions, status and release notes.
- [x] Commit/push implementation (`6baf6cd`) in draft [PR #158](https://github.com/djebaz/dtflow/pull/158); not merged.
- [x] Review standards and spec separately, reconcile current `main`/#159 documentation conflicts, and align existing contract expectations without executing tests.
- [x] Record bounded owner-session evidence and remaining acceptance limits before preparing the PR for owner merge.

## Decisions and acceptance

- The local SHA-256 remains local content identity and the key for subsequent metadata checks. Server-returned values stay private and are not substituted into the local digest or exposed in logs.
- Keep destination HTTPS validation, streamed local re-hashing, durable outcomes and fresh post-upload metadata confirmation. No inferred receipt from a successful PUT alone.
- No new test functions and no tests run, validation, UI renders, package builds or agent live calls. During merge preparation, existing unit/integration expectations and their shared fixture were updated in source to reflect independent server values, branch precedence and unknown lookup entries. They are not passing-test evidence.

## Owner runtime evidence — bounded observation, not exact-head acceptance

The owner first supplied `backend.asset_negotiated` with `outcome: exists` at 2026-10-22 14:08:57.945 UTC. This confirms only the reported existing-asset negotiation path, not a new upload.

The owner then supplied the still-active `logs/dtflow-f579bf8480c4.jsonl`. To avoid treating a growing file as sealed evidence, this review uses records 1–594 only, ending at 2026-10-22 14:38:23.306 UTC. Raw logs stay ignored and are not committed. The startup identifies DTFlow 5.1.0, `source_status: dev`, and unknown source commit/build state; this is owner runtime evidence, not certification of the exact PR head.

| Observation in that bounded window | Scope |
| --- | --- |
| Three `assets.prepared` events with `source: already_exists` (seq 54, 87, 220) | Existing remote assets were accepted and preparation reported success; no fresh upload established. |
| Six `generation.outputs_saved` events, each with one output (seq 172, 266, 355, 457, 518, 590) | The owner session reported six saved images; not a numerical comparison, LoRA application proof or packaged acceptance. |
| Three `prepared_asset_unknown` blocks with `reference_mismatch` (seq 38, 67, 205) | Saved/current references differed. A stale receipt is plausible, but wrong local bytes or a specific historical registration defect is not proven. |
| One `grpc_unavailable` generation failure reported at backend, service and UI layers | One operation's failure, not three independent failed generations; later success does not erase it. |
| Four snapshot publication warnings with `CacheBusyError` | Best-effort cache publication warnings, separate from saved generation outputs; outside this PR. |
| No `backend.byom_upload_evidence` events | This window does not demonstrate the differing-server-value upload branch, successful PUT or post-upload confirmation. |

Receipt migration/repair is not part of #158. Dispatch still refuses a saved/current reference mismatch. Existing metadata reconciliation also keeps a confirmed mismatched reference unresolved rather than silently replacing it; explicit preparation remains a separate user action. Do not claim all historic receipts were repaired or infer a need to delete remote assets/re-upload them.

## Initial merge-preparation review — 2026-10-22 (superseded by owner pytest below)

The initial production review was pinned to PR head `2f93f6ba68090d2100695e09c3031976415f9453`, base `33193340b4cddacde095877b035ca00b441a9a96` and merge-base `c590a72fc2034424f8387d027ec9cd1c09cdf025`. Current `main` is merged into this branch without rewriting published history, preserving #159. No merge to `main` is authorized by this review request.

### Standards axis

- **Static-only, no production blocker found:** `cloud_assets.py` emits only fixed diagnostic labels, lengths and booleans through the public diagnostics API. No new sink/dependency, raw server value, checksum, filename or URL is emitted by the new event. Provider interpretation remains backend-owned.
- **Addressed in documentation:** preserve both #158's contract correction and merged #159's Logs changes in status/release audit; do not imply provider-success evidence is exact-head CI/native acceptance.

### Spec axis

- **Static-only, no production blocker found:** both lookup and negotiation remove the unsupported local/server equality requirement while safe destinations, local streamed content validation and fresh post-upload metadata confirmation stay unchanged. No automatic retry or generation-wire expansion.
- **Addressed, unexecuted:** existing `tests/unit/test_cloud_lora_assets.py` and `tests/integration/test_cloud_assets.py` expected the superseded mismatch/whole-response rejection. Their existing cases now express current branch precedence, distinct server values and unknown unusable lookup entries; the shared fixture deliberately returns a different server value. No new test functions or execution.
- **Unverified/out of scope:** fresh differing-value upload, receipt migration, exhaustive privacy/fault/Windows/DPI and packaged acceptance.

### CI and merge boundary

The reviewed-head UI run `39792996010` is tied to `2f93f6b`; its only job was skipped with no executed steps. It is not a green test/render result, and it does not cover later merge-preparation edits. No CI, tests or rendering was manually triggered. Refresh the final PR head/mergeability before any separately authorized squash merge; owner decides whether the disclosed source-only boundary is sufficient.

## Owner pytest failures after formatting — current merge disposition

The owner supplied `Desktop/run.txt` after format commit `8e4ad3eb75e4bf74a687b49e63111725503a8a1d`: **11 failed, 629 passed, 3 skipped in 198.68 seconds**. This supersedes the earlier source-only no-blocker assessment; #158 is returned to draft pending an owner rerun. Raw output remains outside Git, and no real credentials or private request data are copied into documentation.

| Reported failure group | Source correction | Evidence limit |
| --- | --- | --- |
| Four local generation tests fail on absent metadata; one service test gets a masking protocol error | Local generation legitimately returns no auth metadata. The exception projection now treats absent metadata as empty without changing the original request or retry behavior. | Source-addressed, not rerun. |
| Two storage-error and one malformed-BYOM privacy assertions expose compound secret-shaped values; two token/auth refusal privacy assertions expose unknown/unissued credential-shaped values | Shared API text projection conservatively redacts compound credential-shaped atoms while retaining ordinary wording such as `token expired`. Storage error projection additionally excludes every signed-query value, including repeated keys and raw-encoded forms, not just the full URL. | Source-addressed, not a comprehensive privacy guarantee or passing-test result. |
| One Qt assertion searches an unbroken code in deliberately wrapped label text | Normalize intentional zero-width wrap characters in the existing assertion; leave runtime UI and its uncertainty wording unchanged. | Existing expectation corrected, not executed. |

No tests, syntax/lint validation, renders, package builds or agent live/provider calls were run for these corrections. Preserve the owner's formatting commit and keep all fixes on #158. A new successful owner run and a separately authorized merge remain outstanding.

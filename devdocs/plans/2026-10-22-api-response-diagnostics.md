# API response errors in the UI and WARNING logs

## Scope

- In: bounded, sanitized server error explanations for all seven transport operations DTFlow currently uses, warning-level failure observations, shared UI presentation and offline preview definitions.
- Out: retries, credential/account switching, billing/quota policy, provider submission changes, dependencies, storage migrations, packaging, tests/validation/renders and further live calls.
- Workflow: owner requested `$python-gui-fast-implement` and a new PR. Do not add/run tests or validation; do not merge.

## Endpoint inventory

| Operation | Response branches | Safe diagnostic projection |
| --- | --- | --- |
| HTTP POST `/sdk/token` | Token/expiry, rejection, malformed success, timeout/connectivity | Status/operation/timing, sanitized error envelope and allowlisted headers; no key/token |
| HTTP POST `/authenticate` | Generation token, rejection, malformed success, timeout/connectivity | Same, without signed token or request blob |
| HTTP POST `/get_byom_urls` | Already stored, upload required, unknown lookup, inconsistent/malformed result, rejection | Branch/count summary; sanitized error envelope; no signed URL or private cloud reference |
| HTTP signed-storage PUT | 2xx, 4xx, 5xx, timeout/disconnect/local transfer validation | Status, timing and transfer certainty, safe storage error; never destination URL/contents |
| gRPC Echo | Inventory, secret refusal, status/transport/protocol failure | Counts/timing, status/message and allowlisted metadata; not generation permission |
| gRPC FilesExist | Present/missing/hash result, invalid/incomplete response, status/transport failure | Counts/timing, status/message; no filename/hash dumps |
| gRPC GenerateImage | Progress/preview/final/OK, rejection, interruption, invalid/missing final | Status/message/details/trailers, phase/timing/submission certainty; no prompts/images/tensors |

## GUI components affected

- Shared connection/generation/asset failure presentation and existing inline feedback rows; no new form or style.
- Existing offline render-state definitions gain server-rejection examples; they will not be executed.
- No assets, configuration format, runtime request schema or PyInstaller changes.

## Action items

- [x] Inventory canonical endpoint call sites and current error-information loss.
- [x] Extend the existing central privacy authority with a bounded API-error projection exposed through the public diagnostics API.
- [x] Preserve HTTP/gRPC error evidence through backend-neutral failures and WARNING events, including malformed HTTP 200 responses.
- [x] Present sanitized server explanations without inventing credential, throttle reset, billing or remote-execution conclusions.
- [x] Synchronize architecture, desktop guide, preview definitions, status and unreleased notes.
- [ ] Commit/push implementation and update the new PR; do not merge.

## Decisions

- Prepared-asset dispatch validation retains the underlying API's projected diagnostics instead of reducing them to `prepared_asset_unknown`. The generation WARNING includes status, safe server message and causal code when available; local reference mismatch remains a local validation reason. No response body is persisted into the asset registry, and generation stays not submitted.
- The owner's one approved live replay returned `PERMISSION_DENIED` with `user failed to pass throttlePolicy, 24_hour in 500`. Token exchange, BYOM validation and generation authorization returned HTTP 200. The returned authorization identified `plus` and matched the request checksum. This is historical diagnostic motivation, not validation of this implementation.
- Preserve raw status and safe server explanation independently of DTFlow categories. In particular, `PERMISSION_DENIED` alone must not tell the user their key is wrong.
- All API/transport/protocol failures produce WARNING through the shared logging pipeline. Successful per-call summaries remain DEBUG; existing application lifecycle levels remain distinct. Avoid one warning per stream progress item or duplication at each error-wrapping layer.
- Error capture is bounded and explicitly marks truncation/omission. JSON/text/XML/HTML errors may be projected after redaction; successful tokens, signed URLs, request blobs and image/binary payloads are never dumped. Unknown binary structured gRPC details are omitted with type/count evidence.
- Preserve request-specific private values before truncation, in addition to generic key/path/payload redaction. No alternate sink, reader or persistent handler is introduced.
- No automatic retry or inference that a rejected request was never executed. Existing durable uncertainty remains authoritative.
- Source review corrections redact sensitive XML elements and overlapping private values longest-first, preserve BYOM request/response private identities across malformed-response handling, emit warnings for expired token/shared-secret/schema refusals, and reject incomplete FilesExist existence arrays instead of inventing missing-file evidence. No executable validation was performed.
- Preview definitions add `generate-11a-api-throttle-explanation` and `generate-37a-api-asset-refused` through the existing shared presentation paths. Definitions were not rendered or run; no tests were added.
- Owner additionally requested multiple manually selected credential profiles with a persistent override above environment and legacy saved credentials. This is a separate follow-up; PR #156 changes neither credential storage nor precedence.

## Open questions

None blocking. New server error strings remain descriptive text, not a closed list of guessed failure causes.

## Validation and delivery

- No tests, validation, UI renders, application smoke, package build or additional live calls will be run by the agent. Final validation and Windows acceptance belong to the owner.
- No new inference/upload/billable operation is authorized by this implementation task. The preceding single live replay is separate evidence.

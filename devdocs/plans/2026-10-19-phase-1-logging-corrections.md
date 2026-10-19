# Phase 1 diagnostic logging corrections — PR #105

## Scope and authority

Follow-up to the [Phase 1 review](PR%20105%20phase%201-logging%20review.md) against the [binding implementation plan](2026-10-19-structured-log-viewer.md), G1–G6 and the [JSONL contract](../architecture/diagnostic-event-schema.md). Correct the existing pipeline, not replace it. Owner reports pytest green on `843b36b`; the restored UI failure code/category was a real regression, not solely stale assertions. No local acceptance is implied.

In scope: writer/retention/privacy/notification/aggregation corrections, producer fidelity and Phase 2 handoff. Out of scope: native Logs workspace, new segment headers, benchmark ranking changes, historical-log deletion and tests/validation execution or modification. Keep the existing six-field envelope, 1 MiB physical record limit, 1,000,000-byte rollover target, three backups and bounded capture policy.

## Fix checklist

Checked items below mean implemented source plus diff inspection only, not runtime acceptance. F9 stays open until the separately authorized evidence exists.

- [x] F1 — Distinguish retryable rotation collisions from terminal writes. Retry with bounded monotonic backoff; never append indefinitely past the target. Count every unavailable data write, keep closure explicitly incomplete and preserve fail-closed behavior after partial/failed writes.
- [x] F2 — Make pruning best effort and protect active sessions using cross-process ownership, not timestamps alone. Tolerate disappearing/locked candidates without disabling the new sink. Reject redirected lease/segment inputs; keep legacy files untouched.
- [x] F3 — Deliver once-only diagnostic warnings through bounded thread-safe state and a window-owned GUI receiver using shared inline feedback. No listener-thread native dialog; retain separate bootstrap reporting when Qt cannot start. Respect shutdown and receiver lifetime.
- [x] F4 — Fix generic formatting and path sanitization. Format only bounded primitive snapshots before final sanitization; preserve MIME/ratios/model identifiers, redact sensitive paths including spaces and retain safe traceback locations without source/locals.
- [x] F5 — Expire idle batches on monotonic deadlines without changing source windows/populations. Serialize sink write/rotation/close without making GUI shutdown wait indefinitely for the handler lock. Classify closed-stream failures as sink failures, not innocent-record serialization failures.
- [x] F6 — Reduce redundant capture work without allowing unbudgeted object copies, short-secret bypasses or stale credential caches. Replacement for full reservations must enforce incremental global accounting before allocation. No performance acceptance is inferred.
- [x] F7 — Restore safe lease identity; use numeric/null observations instead of mixed numeric/unknown strings; correct misleading event names and refresh trigger/reason evidence. Describe build wall time honestly. Add bounded source decode-attempt wall/thread-CPU summaries, not inferred codec-only spans or new benchmark scores.
- [x] F8 — Update desktop/development/tooling documentation and the parent plan. Distinguish overlapping individual/source-summary/listener populations, remove confirmed dead listener phases and hand the missing-thumbnail report mapping to Phase 2 without silently changing benchmark semantics. Do not add repeated startup headers or shrink size limits without a contract decision.
- [x] F9 — Record source changes and remaining acceptance per fix. Owner/CI later checks fault injection, privacy/formatting, concurrent sessions, idle expiry and shutdown plus native Windows rotation/notifications. No tests, lint, formatters, syntax/import checks, renders, benchmarks, inference or billable actions run by this implementation.

## Decisions and cautions

- A Windows rotation failure depends on the reader handle's sharing flags; it is not inevitable for every reader. Phase 2 should use short-lived verified handles/share-delete where available, with bounded retry and explicit incomplete snapshots.
- Genuine failed writes remain terminal as already required by G2; retrying arbitrary partial writes risks corrupted or duplicated evidence. Recovery is limited to rotation before writing a record.
- Never use file age/PID alone to authorize pruning. Ownership must remain held across writes, rotation and final close; unknown/legacy ownership is skipped conservatively.
- Retained fragments without startup cannot certify a complete canonical session. Repeated headers would require explicit sequence/schema/reader design and are not part of this correction.
- Cached invariant home/package context is safe; secret environment values must remain current unless an explicit refresh policy exists. Review microbenchmarks are provisional Linux observations, not native Windows evidence or an acceptance target.

## Progress and evidence

Plan recorded before code changes; F1–F8 source implemented on the existing PR branch. Existing user review is preserved. Validation and tests remain unrun; no acceptance boxes in the parent completion audit are satisfied merely by code presence.

| Fix | Source checkpoint | Remaining evidence/risk |
| --- | --- | --- |
| F1 | `JsonlHandler._write_record`, `DiagnosticListener._write`/`_unwritten`/`_flush_losses` | Native Windows transient rename collisions and genuine disk/partial-write failures; no recovery of already lost observations, no clean ranking after any failure. Retry delay 100 ms initially, capped at two seconds; the current output is dropped rather than buffered without bound. |
| F2 | `_diagnostic_ownership.ownership`, `logging_setup._prune`, listener finalization | Active idle process, concurrent pruning/rotation, redirected/locked/disappearing files, process death and Windows locks. Stable tiny `.owner` files deliberately remain; unknown/pre-policy owners are skipped and can exceed the retention target. Never automatically unlink ownership locks. |
| F3 | `_diagnostic_notifications.DiagnosticNotice`, `ui/diagnostic_notice.DiagnosticNoticeReceiver`, desktop/app composition | Pre-window warning, protected editors, shutdown/lifetime and packaged Windows acceptance. New `shell-21-diagnostic-warning` offline preview definition added, not rendered; CLI retains console reporting. No new Designer form/theme is introduced. |
| F4/F6 | `_diagnostic_schema._Snapshot`/`capture`/`sanitize`/`_exception`, `CaptureHandler.emit` | Formatting widths/precision (including mapping forms), secrets changing between observations, legitimate slash values, spaced absolute paths, traceback privacy, concurrent capture pressure and real producer cost. Pre-allocation incremental charges include a base reservation and conservative format expansion; no fast path bypasses secret sanitization. |
| F5 | `DiagnosticListener._monitor` and batch source-monotonic expiry; sink handler lock | Idle flush under backlog/out-of-order source records, timeout plus interpreter shutdown and concurrent close. Explicit `LogSession.close` waits two seconds; arbitrary blocked OS I/O during interpreter shutdown is not promised bounded. |
| F7 | `original_io`, `gallery_snapshot`, gallery refresh/query/decode instrumentation and removal refresh call site | Runtime diagnostic fidelity and unchanged refresh/mutation behavior. Deferred refreshes retain their trigger. Decode summaries count attempts including cache fallback; they overlap worker/end-to-end populations and include Qt reader/plugin work, not pure CPU-only decoding. |
| F8 | Architecture/catalogue, parent plan, desktop/development/tooling guides, AGENTS and unreleased notes | Phase 2 shared-reader population rules, unsupported historical benchmark input handling and explicit approval before correcting the formerly-null thumbnail report metric. |

The owner-updated retention test at `843b36b` fabricates JSONL files without ownership metadata. Those inputs are now deliberately unknown owners and must not be pruned; its old unconditional retention assertion will need separately authorized reconciliation using ownership-aware fixtures. GUI harness stubs may likewise need the new optional mailbox/refresh-trigger arguments. No tests were edited or run here, and the previous green result does not certify this new head.

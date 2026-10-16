# Plan: explicit Trash / permanent-delete split action

## Status and working rules

Draft PR #76, branch `feature/image-removal-modes`. Initial implementation is source-present at `b935bca`; review identified blocking A1–A3 issues and the related B1 refusal-message issue. The agreed remediation below is planned, not implemented. Runtime removal acceptance remains pending. Reuse the existing workflow rather than adding a parallel deletion framework. No merge, tests, builds or renders by the agent. Owner permits focused, non-destructive WSL CLI probes and has separately requested Ruff lint/format.

## Scope and agreed contract

- One split control: main action is Move to Trash or Delete permanently; the adjacent menu only chooses a mode and never submits work.
- Default Trash. Persist the mode separately for Windows, WSL and native Linux; never persist consent, substitute Permanent or replay uncertain work.
- Disabled Trash leaves the mode menu usable. Every permanent deletion needs a fresh inline irreversible warning/count and explicit Delete permanently consent.
- Remove managed original images only; retain metadata, audit history and exports.
- Include Windows-recorded originals from WSL through an explicit mutation boundary, not the gallery's read-only path alias.
- Cancel between files; mode/scope/navigation changes invalidate pending consent.

## Minimal implementation direction

Reuse existing managed-root, bounded preflight, atomic intent and durable outcomes. Keep Trash and permanent outcomes distinct in the shared removal ledger, and hide both removed identities without discarding attempt history. Persist mode in separate environment-specific convenience files; unreadable/newer state fails closed for saving, not an implicit switch of action.

Keep orchestration, confirmation and ledger settlement in the existing removal workflow. Add one explicit operation mode and only the platform-specific file/lock code needed for safety. Do not duplicate the complete service inside a helper.

Before implementing mapped Windows deletion from WSL, use focused probes to establish path identity and shared-lock behavior. Do not assume Linux `flock` and Windows `msvcrt` exclude each other. If a Windows-side helper is necessary, keep it limited to the demonstrated platform boundary; choose its interface after the probes, not before. Missing support refuses the operation clearly.

Execution must revalidate the exact confirmed managed original, its current committed references and its mapping while holding the relevant leases. Reject changed files, redirected paths, hardlinks, ambiguous mounts and custom output roots. A missing file after an unconfirmed operation is uncertain, not proof of successful deletion. Never replay pending/uncertain/removed outcomes.

## Agreed review fix scope: A1–A3 and B1

### A1 — Clear, early shared-lock readiness

Keep one interoperable shared-lock boundary on every path that touches originals, including gallery image reads, generation saves, export and both removal modes. Do not restrict native Windows leases to permanent deletion and do not silently fall back to Linux `flock` when the Windows boundary is required.

Keep DTFlow open for settings and metadata-only browsing when that boundary is unavailable. Show a clear availability state identifying the specific prerequisite or unsupported setup; do not misreport missing bridge support as ordinary lock contention. Original-image loading, generation, export and removal must refuse affected work until readiness is established. Unrelated settings/metadata/connection operations remain usable; do not decode originals behind the refusal state.

Check readiness off the GUI thread at startup and recheck it when each affected operation starts. Gate generation in the shared submission path, covering desktop, CLI, queue dispatch and image-operation submission before provider dispatch or potentially billable preparation, not only when saving the returned result. Startup readiness is not a permanent authorization: retain actual per-operation/per-file leases and handle later contention or capability loss without weakening existing durable outcome rules. A readiness probe must release its lease; do not hold the originals lease throughout remote generation.

For this PR, source-run WSL requires this checkout's Windows `.venv/Scripts/python.exe`; do not add interpreter discovery or a configurable Windows Python. Missing interpreter, unavailable interop/helper, or unsupported/unverified/ambiguous mappings must produce a sanitized, actionable reason. Refuse unsupported WSL2/9p mappings clearly and defer WSL2 support; no real WSL2 behavior has been verified and no support claim follows from source inspection.

### A2/A3 — Expected refusal versus unconfirmed mutation

Normalize expected native Windows validation/fingerprint `ValueError` refusals into the same handled result contract as helper refusals, so they settle the current image rather than aborting the batch after `delete_pending`. Keep the helper narrow; use stable phase/status information instead of raw exceptions, private paths or a blanket `ok:false` with no mutation-phase distinction.

Distinguish confirmed refusal before the delete-disposition call from unconfirmed execution. A confirmed pre-mutation refusal becomes retryable `failed` only when the original is still unchanged; if it disappeared or changed, retain `uncertain`. This includes validation refusal and a known open/sharing/permission failure before mutation. Continue with later files after a settled per-image refusal.

Timeout, killed/lost helper, missing/malformed acknowledgment, or a failure at/after the disposition-call boundary remains `uncertain`, even when the original appears unchanged. Never infer refusal from source presence alone. Preserve durable intent before mutation, per-image settlement, interruption reconciliation and the prohibition on replaying pending/uncertain/removed outcomes. Durable-write failure is not a confirmed pre-mutation refusal and must not be downgraded into a safe retry.

### B1 — Phase-correct refusal presentation

Present sanitized readiness/preflight reasons directly and state that the refused operation changed no originals; distinguish unavailable support from a busy lease. Reserve durable-outcome uncertainty/manual-review wording for execution that may have mutated an original or could not settle its durable result. In a partial batch, a later refused item must not produce an operation-wide claim that nothing changed.

### Preserve safety; defer unmeasured optimizations

Keep `RecycleTarget.mounts` and fresh per-file mapping discovery: the frozen-target comparison uses the snapshot to reject changed mappings. Recompute committed ownership at execution time under the original-file lease; never substitute preflight counts or narrow ownership to only the selected attempt. Process-spawn and normalized-query costs remain unmeasured follow-ups, not defects to optimize in this remediation. No recovery/reset UI, new dependency, general cross-platform state-owner/SQLite concurrency support or WSL2 implementation is included.

### Remediation action items (pending)

- [ ] Add a reusable, sanitized readiness result distinguishing unsupported bridge prerequisites from transient contention, while preserving the shared lock on every original-touching path.
- [ ] Wire nonblocking startup availability and operation-start rechecks, preserving settings/metadata browsing and blocking original reads and affected work when unavailable.
- [ ] Gate every generation submission path before provider dispatch/potentially billable preparation; retain the actual save/read/mutation leases and later-failure safeguards.
- [ ] Add the native/helper confirmed-pre-mutation refusal contract; contain expected native validation exceptions and settle unchanged refusals as `failed` without aborting later files.
- [ ] Retain `uncertain` for possible mutation, acknowledgment loss, changed originals and unsettled durable writes; preserve no-replay behavior.
- [ ] Separate readiness/preflight, per-item refusal and execution-uncertainty UI messages; extend offline availability/refusal fixture states without rendering them.
- [ ] Synchronize architecture, desktop/policy/release docs and PR body with the implemented remediation, then run owner-authorized Ruff lint/format and report remaining runtime acceptance separately.

## Ordered implementation steps

1. Inspect existing preflight, eligibility, reference counting, intent claim and settlement; identify the smallest reusable changes for an explicit mode.
2. Probe WSL path identity and Windows/WSL lock exclusion using disposable files. Resolve the mutation/lock boundary before implementing permanent file removal.
3. Extend the existing service/ledger for distinct permanent outcomes. Keep per-file revalidation, durable intent before mutation, and no automatic replay.
4. Add environment-specific mode persistence, then the canonical split control. Mode selection alone does no work; all consent remains session-only.
5. Wire explicit irreversible consent, progress/results and stop-between-files through the existing controller. Invalidate consent on mode/scope/navigation.
6. Extend offline preview states, synchronize policy/design/docs/release notes, push implementation to this same draft PR and hand runtime acceptance to owner.

### Safety gate before permanent-removal code

The focused probe found that WSL `flock`/`lockf` and Windows `msvcrt` do not exclude one another, in either direction. Windows/WSL size, mtime_ns and inode matched for the disposable drive file; device did not. Chosen boundary: one source-only Windows helper for native named leases and fingerprinted managed-file handle deletion, leaving all orchestration/ledger work in the existing service. Missing Windows checkout `.venv`, unsupported mappings or helper failure refuses shared access rather than using Linux-only locks. Permanent Windows targets are derived from state/identity, never an arbitrary delete pathname. POSIX-native originals retain guarded descriptor-relative removal.

The implemented helper's lock-only probe showed: WSL-started holder blocks native Windows contender; native holder causes WSL-started contender to refuse; parent-pipe EOF releases the lock. A further non-mutating Windows handle probe using the intended open/access flags confirmed path/handle size, mtime_ns, inode and link-count equality for the same disposable file. Disposable files only (`tmp/removal-lock-probe-fa3xpnke`); no disposition/delete request or user-image mutation. Actual handle deletion, application composition and failure/restart behavior remain owner acceptance, not established by these probes.

## GUI components

- [x] Canonical Images Designer split-control layout and shared QSS.
- [x] Mode menu, independent availability and mode persistence.
- [x] Inline preflight/confirmation/progress/results and cancellation.
- [x] Offline fixture states for unavailable Trash and permanent consent (not rendered).
- No provider, model, packaging or dependency changes planned.

## Action items

The completed items below describe the initial implementation only; they do not establish completion of the pending review-remediation checklist above.

- [x] Read current removal service, ledger, gallery and inline consent paths.
- [x] Implement mode state and explicit policy change in AGENTS/design/docs.
- [x] Complete focused path/lock probes and record the minimal safe boundary.
- [x] Extend the existing service with guarded permanent mode/distinct outcomes; ledger schema 2 preserves schema-1 records and refuses old-app access after upgrade.
- [x] Wire split UI and invalidate stale consent on mode changes.
- [x] Finish offline previews, docs, release notes and PR body; implementation goes to the same draft PR.

## Probes / acceptance

Restrict agent probes through wsl.exe to non-mutating capability/path/handle inspection and disposable-file locks; never delete user images or run generation. No tests, builds or renders under fast-implement. Owner additionally requested Ruff lint/format: environment readiness passed; `uv run --frozen ruff check .` passed; Ruff formatted 9 changed Python files and repository-wide `ruff format --check .` passed (320 files). Record these static checks separately from runtime acceptance.

Those Ruff results and probes cover the initial implementation, not the pending remediation. This plan-only update runs no new runtime checks, probes or validation.

- [ ] Owner missing Windows `.venv`, unavailable helper and unsupported-mapping checks: app remains open, settings/metadata remain usable, original reads refuse clearly and no generation is submitted.
- [ ] Owner readiness-change checks after startup and before operation start, including queue/image-operation dispatch; no silent `flock` fallback or stale readiness authorization.
- [ ] Owner native/helper pre-mutation sharing/permission/validation refusal: unchanged file settles `failed`, later files continue, and a fresh user-authorized retry is possible.
- [ ] Owner helper timeout/termination or post-disposition uncertainty: retain manual-review/no-replay outcomes, never relabel as a safe retry because the source still exists.
- [ ] Owner refusal wording distinguishes zero-mutation preflight from partial execution and unconfirmed/durable outcomes; unsupported WSL2 remains explicitly deferred.

- [ ] Owner Windows/WSL split-menu and separate preference acceptance.
- [ ] Owner mixed native/Windows-recorded managed-source checks and confirmations.
- [ ] Owner permanent deletion, partial/failed/uncertain and restart/no-replay.
- [ ] Owner two-process Windows/WSL lock exclusion and helper interruption.
- [ ] Owner cancellation, changed file/reference/mount and redirect refusals.
- [ ] Owner exports/history/metadata untouched and Trash regression checks.

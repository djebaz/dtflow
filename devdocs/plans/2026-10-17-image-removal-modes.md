# Plan: explicit Trash / permanent-delete split action

## Status and working rules

Open, unmerged PR #76, branch `feature/image-removal-modes`. The initial implementation was source-present at `b935bca`. The A1–A3/B1 review fixes and the later owner decisions on **no new generation recovery**, uncertain-delete alerts and split-menu styling are **planned, not yet implemented**. Runtime removal acceptance remains pending. Reuse the existing workflow rather than adding a parallel deletion framework. No merge, tests, builds or renders by the agent. Owner permits focused, non-destructive WSL CLI probes and has separately requested Ruff lint/format.

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

Surface bridge capability at startup without blocking the GUI and recheck cheap static prerequisites at every affected operation start. Gate desktop, CLI, queue and image-operation generation submission before provider dispatch, but **do not spawn a helper process, test/acquire the originals lock or perform a Windows handshake solely as a pre-generation readiness probe**. Keep this check limited to static interpreter/mapping/capability facts. Actual reads, saves, exports and removals still take the same interoperable shared lock at their normal original-file I/O boundary, and readiness at startup is never permanent authorization. If a provider result arrives but the save lease has become unavailable, report local-save failure clearly and retain the existing attempt/history evidence. The owner accepts that such a generation may be lost, potentially requiring another paid request; do not add special staging or automatic recovery. Never hold the originals lock throughout remote generation.

For this PR, source-run WSL requires this checkout's Windows `.venv/Scripts/python.exe`; do not add interpreter discovery or a configurable Windows Python. Missing interpreter, unavailable interop/helper, or unsupported/unverified/ambiguous mappings must produce a sanitized, actionable reason. Refuse unsupported WSL2/9p mappings clearly and defer WSL2 support; no real WSL2 behavior has been verified and no support claim follows from source inspection.

### A2/A3 — Expected refusal versus unconfirmed mutation

Normalize expected native Windows validation/fingerprint `ValueError` refusals into the same handled result contract as helper refusals, so they settle the current image rather than aborting the batch after `delete_pending`. Keep the helper narrow; use stable phase/status information instead of raw exceptions, private paths or a blanket `ok:false` with no mutation-phase distinction.

Distinguish confirmed refusal before the delete-disposition call from unconfirmed execution. A confirmed pre-mutation refusal becomes retryable `failed` only when the original is still unchanged; if it disappeared or changed, retain `uncertain`. Classify by execution phase rather than Windows error codes: request/path validation, redirect and metadata checks, every `CreateFile` failure, `open_osfhandle` failure before disposition, and `fstat`/fingerprint/link-count/reparse validation are confirmed pre-mutation refusals. The Windows helper returns a stable phase (for example `{"ok":false,"phase":"pre"}`) only for such confirmed failures. Native Windows raises a dedicated handled `OSError` subclass such as `RefusedBeforeMutation`, never an escaping `ValueError`. Any error at `SetFileInformationByHandle`, handle close or acknowledgment, and any absent/malformed helper response, is unconfirmed. Do not whitelist Win32 error codes. A confirmed pre-mutation refusal settles to retryable `failed` only if `_unchanged(target)` succeeds; otherwise `uncertain`. Continue later batch items after a settled refusal.

Timeout, killed/lost helper, missing/malformed acknowledgment, or a failure at/after the disposition-call boundary remains `uncertain`, even when the original appears unchanged. Never infer refusal from source presence alone. Preserve durable intent before mutation, per-image settlement, interruption reconciliation and the prohibition on replaying pending/uncertain/removed outcomes. Durable-write failure is not a confirmed pre-mutation refusal and must not be downgraded into a safe retry.

### B1 — Phase-correct refusal presentation

Present sanitized readiness/preflight reasons directly and state that the refused operation changed no originals; distinguish unavailable support from a busy lease. Reserve durable-outcome uncertainty/manual-review wording for execution that may have mutated an original or could not settle its durable result. In a partial batch, a later refused item must not produce an operation-wide claim that nothing changed.

### Owner decision — omit generation recovery from PR #76

The owner explicitly accepts the risk that a provider-completed generation may be lost if its final local save fails, including the possibility of paying again to regenerate. **Do not implement** a new durable output-staging area, save-retry state machine, startup generation-recovery pass, scheduled/local automatic retries, special retention policy, or recovery notification/UI in this PR. Keep the existing generation attempt/history records and normal save/error handling; surface a clear failure when the actual original-file lock or local save is unavailable. Do not automatically resubmit potentially billable generation. The cheap static bridge capability check before provider dispatch and the interoperable original-file lock at actual save time remain required. This simplification applies **only to generation-output recovery**; deletion-ledger restart reconciliation, durable outcomes, no-replay and amber warnings for uncertain deletes remain in scope.

### Owner decision — warn only on newly uncertain permanent deletions

When a permanent-delete attempt first settles as `uncertain` during normal operation, show the existing amber/orange warning overlay once for that newly uncertain outcome. `persistence/image_recycle.py:reconcile()` currently transitions both Trash `pending` and permanent `delete_pending` and returns a combined count. Change the result/signature minimally to distinguish newly reconciled Trash from permanent-delete rows. **After any successful reconciliation pass** (startup or later `_request_recycle_reconciliation`), warn only for that pass's newly transitioned `delete_pending` → `uncertain` records; do not start warning for Trash rows that previously reconciled silently. Aggregate counts when appropriate. Preexisting `uncertain` rows never re-alert. Do not add a tracking table, timestamp-based alerts or recovery UI.

Reconciliation requires both `recycle_batch_lock` and `original_images_lock`. When unavailable, log the skipped pass and explain in the existing readiness UI that interrupted removal intents cannot be reconciled until shared-lock readiness returns. Do not claim that they were settled. Rows still in `delete_pending` remain ineligible for retry; that is intentional. Only a successful pass can generate newly reconciled warnings. Do not assume reconciliation runs only once per session, and never replay removal. Explain that deletion **may or may not have occurred** and manual inspection is necessary. The durable `uncertain` outcome remains unchanged and blocks automatic replay; dismissal/expiry of the warning does not modify ledger state. A *confirmed* refusal before the disposition boundary, with an unchanged original, remains retryable `failed` as defined by A3. **Known limitation accepted by the owner:** if the app crashes after recording `uncertain` but before showing its warning, that outcome may never generate an overlay; durable evidence and no-replay behavior still protect the original.

### Local-save failure contract (no recovery)

In `services/generation.py`, distinguish errors raised while `saving` after the provider has returned successfully from pre-dispatch capability refusal. A lock/bridge-unavailable error, ordinary save-time lock contention (`OriginalImagesBusyError`, including its normal timeout), or `OSError` from `commit_originals` should produce a distinct `PERSISTENCE` failure such as `local_save_unavailable`, with remote outcome `SUCCEEDED` and attempt state `save_failed`. Keep distinct error types for logging and readiness/UI explanations: unsupported capability is not equivalent to a contended lease. Explain: "The image was generated but could not be saved locally; it may not have been saved or may be incomplete, and generating again may be billed." The saving loop may have written some final files before an error. Keep existing attempt/history evidence and any partial file evidence available from the current workflow; never claim that all files were absent, or that partial files constitute a completed durable save. Do not add staging, retry or provider resubmission. For pre-dispatch refusal, say nothing was submitted and therefore no provider charge was incurred by that refused request. Verify that the UI presents `failure.message` rather than replacing it with a generic error.

### WSL2 release note and refusal contract

Document in `devdocs/releases/unreleased.md` the split action, per-Windows/WSL/native-Linux preference files, ledger schema 2 and the incompatible older-reader/no-rollback consequence after first schema-2 write. Source-run WSL on Windows-hosted state requires the checkout's Windows `.venv/Scripts/python.exe`. WSL2 9p/unverified Windows mappings now fail closed for gallery original loads, generation, export, Trash and permanent deletion, even where these worked before. Give an explicit workaround: move DTFlow application state to the WSL Linux filesystem (ext4), and defer WSL2 drive-mount support to issue #77 after real WSL2 probes. Mention the accepted possibility of losing remote generation output on local save failure. The refusal UI must explain the ext4 workaround.

### Owner decision — refine the split-action dropdown visually

Polish the existing joined Images action/menu control, not a redesigned component. Its compact dropdown should look like part of the button family: consistent DTFlow dark surface, shared border and rounded corners, typography, hover/focus/pressed states, and tidy alignment. Show exactly two icon-labeled choices: **Move to Trash** and **Delete permanently**. Indicate the selected mode with a small checkmark and a restrained danger-red accent for permanent deletion. Keep the popup compact and closely aligned with the button; do not add a third action or custom UI framework. Use the canonical Designer form, shared QSS and tokens where appropriate. Selecting a mode only updates the existing environment-specific preference; it **never executes removal**, saves destructive consent or bypasses the required fresh confirmation. Preserve keyboard, DPI, unavailable-Trash and state-change behavior. Extend `tools/ui/render_states.py` with offline menu-open/selected/disabled fixtures; render only if separately requested.

### Preserve safety; defer unmeasured optimizations

Keep `RecycleTarget.mounts` and fresh per-file mapping discovery: the frozen-target comparison uses the snapshot to reject changed mappings. Recompute committed ownership at execution time under the original-file lease; never substitute preflight counts or narrow ownership to only the selected attempt. Process-spawn and normalized-query costs remain unmeasured follow-ups, not defects to optimize in this remediation. No dedicated recovery/reset UI, new dependency, general cross-platform state-owner/SQLite concurrency support or WSL2 implementation is included.

### Remediation action items (pending)

- [ ] Add a reusable, sanitized readiness result distinguishing unsupported bridge prerequisites from transient contention, while preserving the shared lock on every original-touching path.
- [ ] Wire nonblocking startup availability and operation-start rechecks, preserving settings/metadata browsing and blocking original reads and affected work when unavailable.
- [ ] Gate every generation submission path using cheap static readiness checks only; do not spawn/test a Windows lock helper before billable provider dispatch; retain actual save/read/mutation leases.
- [ ] Implement the helper's stable `{\"ok\":false,\"phase\":\"pre\"}` reply for confirmed pre-disposition failure and native `RefusedBeforeMutation` (`OSError` subclass); classify by phase without Win32 error-code whitelists and settle unchanged refusals as retryable `failed` without aborting later files.
- [ ] Retain `uncertain` for disposition/close/acknowledgment ambiguity, changed originals and unsettled durable writes; preserve no-replay behavior. Extend `reconcile()` return details with separate newly uncertain Trash `pending` and permanent `delete_pending` counts.
- [ ] Separate readiness/preflight, per-item refusal and execution-uncertainty UI messages; use the amber overlay for **new** uncertain deletion outcomes only, including newly reconciled transitions on startup.
- [ ] Add `PERSISTENCE` / `local_save_unavailable` for bridge unavailability, `OriginalImagesBusyError` contention and `OSError` during save after remote success; retain `SUCCEEDED` + `save_failed`, accurately describe possible partial files and possible re-billing, and preserve existing evidence without generation recovery or provider resubmission.
- [ ] Avoid repeated startup overlays for previously recorded `uncertain` deletions; reconcile as usual, announce only newly transitioned cases, and preserve their durable ledger status.
- [ ] Polish the two-item split dropdown with shared Qt tokens/QSS, checkmark/icon treatment and an offline open-menu preview fixture.
- [ ] Synchronize architecture, desktop/policy/release docs and PR body when the remediation is implemented, then run owner-authorized Ruff lint/format and report runtime acceptance separately.

## Ordered implementation steps (remaining, coherent commits)

1. Implement helper phase-based refusal protocol, native `RefusedBeforeMutation` handling and service settlement (A2/A3); distinguish Trash/permanent newly uncertain counts in `reconcile`.
2. Fix phase-correct readiness/preflight, refused-before-mutation and unconfirmed-execution UI messages (B1).
3. Add static-only shared-lock readiness, distinct unavailable-vs-contention errors, nonblocking startup availability, and one authoritative gate covering desktop/CLI/queue/image-operation generation submission (A1).
4. Add `local_save_unavailable` handling for bridge unavailability, ordinary save-time lock contention and disk I/O failure, with partial-file-aware messaging while preserving `save_failed` and no recovery.
5. Implement one-shot amber alerts for newly uncertain permanent deletions on execution or any successful reconcile pass; report skipped reconciliation without replay.
6. Polish split-action dropdown using shared QSS/Designer style and extend offline `tools/ui/render_states.py` fixtures.
7. Sync architecture, desktop/policy and concrete `unreleased.md` compatibility notes plus PR body; perform owner-authorized Ruff checks and report runtime acceptance separately.

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

Those Ruff results and probes cover the initial implementation, not the pending remediation. This plan-only update runs no new runtime checks, probes, tests, builds, renders, training, inference or billable operations.

### Owner acceptance

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
- [ ] Owner local-save unavailability, contended `OriginalImagesBusyError` and disk `OSError`: remote `SUCCEEDED` plus `save_failed`, `PERSISTENCE`/`local_save_unavailable`, message allowing partial files and re-billing, UI preserves `failure.message`, no new staging or resubmission.
- [ ] Owner normal-operation warning: amber/orange overlay appears once when a permanent-delete attempt newly settles `uncertain`, without triggering another deletion.
- [ ] Owner reconciliation warning: after any successful pass, alert only newly transitioned permanent `delete_pending` rows, not Trash `pending` or older `uncertain`; a skipped pass reports that unsettled rows remain blocked and a later successful pass warns once.
- [ ] Owner split-menu visual, keyboard, accessibility/DPI and disabled-Trash acceptance; choosing a mode never performs deletion.

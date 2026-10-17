# Plan: explicit Trash / permanent-delete split action

## Status and working rules

Post-review follow-up (2026-10-17, after `f36a339`): P1 widget recovery and P2 missing cross-runtime locations are fixed in separate commits; a third implementation commit adds the owner-agreed browsing snapshot. These additions are source-present, not covered by the earlier owner acceptance below. No new tests, generation, deletion, builds or renders by the agent; Ruff lint/format is the bounded static gate.

### Gallery recovery and snapshot checkpoint

#### Snapshot source review follow-up

- [x] Finding 1: refuse snapshot-selected `export_identity()` before export setup, matching checked-scope refusal; Generate scope remains independent.
- [x] Finding 2: apply publication deadline/logical page-size checks before/after each batch in both copy loops, SQLite progress cancellation, commit checks and a post-flush deadline before replacement. Failed work leaves the previous publication intact.
- [x] Finding 3: connection creation is inside the reader's normalization boundary; cleanup closes only initialized connections, so unavailable snapshots preserve the live-query failure through fallback handling.
- [x] Finding 4: read deadline starts before setup and its SQLite handler precedes schema/metadata reads. Snapshot SQL reuses canonical BASE with only `image_recycle.records` qualified as `main.records`, directly using the publication's primary-key index on one immutable connection for page/count/anchor/facets. No ledger copy or index construction; live SQL/default calls and all four exclusion outcomes are unchanged.
- [x] Separate logging commit: publication trigger, visible-image additions/removals versus the retained previous publication, included-image/attempt totals, saved UTC timestamp and elapsed work. Close reports earlier session publications even on unchanged skip, cumulative (not net) additions/removals from this process only, and timestamp provenance. Unreadable baselines produce unknown deltas; comparisons stream inside the existing work budget without image/file scans.
- [x] Recovery review P2: validate the previous same-version publication's canonical query structure and bounded SQLite quick check before unchanged skipping; rebuild unreadable baselines with unknown deltas, including a later comparison read failure. Unsupported versions remain protected, and the existing deadline/size limits still apply.

No new tests or renderer runs. Ruff format/lint runs before pushes; CI and owner acceptance remain separate.

Owner requested a persistent super-slim snapshot warning, suppressed after voluntary dismissal. Source now adds a canonical single-line amber activity status row without timer/countdown, kept separate from active confirmations/editors. One keyed row is updated on retries and removed on live recovery; dismissal survives tab navigation and timestamp updates for that episode, resetting only after live access returns. Offline states 47/48 include it and 49 covers dismissal plus retry; no render/test run. Owner acceptance remains pending for height, coexistence, dismissal/retry/navigation and a later new fallback.

CI run `39007127663` at `82943f4`: lockfile/Ruff/SQLite steps passed; offline pytest reported 22 failed, 590 passed and 3 skipped. All failures traced to snapshot launch configuration assuming every CLI namespace has `no_cache`. Fix uses the existing false default when that optional attribute is absent; no new tests or local pytest run. Remote CI run `39007332944` passed at `baca692`; the later P2 resolution follow-up needs its own CI result.

- [x] P1: force refresh recovery through normal reconciliation, not the first-render sentinel; clear an initial failure-label reference with its layout.
- [x] P1 fixture review: gate the first query before window construction, refuse snapshot fallback, assert no successful render occurred, then allow the real query/recovery path and explicitly deliver deferred deletion before inspecting the replacement label. Existing offline renderer only; not executed and no new test file.
- [x] P2: derive and validate native image/sidecar locations independently of image existence, including surviving metadata inspection.
- [x] P2 source review follow-up: normal read resolution first, existence-independent fallback only on failure, independently unavailable sidecar mapping; preserve strict provenance and reject unvalidated native aliases rather than checking their foreign spelling. One focused source commit, no test additions or local runtime checks.
- [x] Publish a versioned committed-gallery/availability projection after a generation/queue session and clean app close; compare a source-change signature and skip unchanged publications. Clean close also creates a missing/empty snapshot.
- [x] Use one background publication worker, bounded source work, cache coordination, private Derived storage and atomic replacement; retain the previous publication on failure. No per-image publication, raw database copy, directory scan or provider retry.
- [x] Use one immutable snapshot for fallback page/filter/anchor queries; show its timestamp/read-only status, disable stale-authority actions and retry live access. Preserve all original-file leases and provenance checks.
- [x] Extend offline empty/populated recovery and snapshot Grid/Detail fixtures, without rendering. Honor `--no-cache`; Derived cleanup may purge the snapshot.
- [x] Inspect owner log `dtflow-80c45749e36a.log`: close publication reported generic `OSError`. A disposable standard-library Windows CLI probe reproduced read-only `fsync` errno 9 and confirmed writable-descriptor flush succeeds; fix that boundary and log sanitized stage/numeric codes.
- [ ] Owner acceptance: publish after generation/queue/clean close, unchanged skip, initial missing/empty snapshot, retained prior snapshot on failure, cross-system browsing during live SQLite refusal, paging/filter/sidecar display, disabled mutations, return to live state, no-cache/purge and realistic-library timings.

See [snapshot architecture](../architecture/gallery-snapshot.md). The agent ran no app generation or deletion and added no tests; disposable flush evidence does not establish snapshot runtime acceptance.

PR #76, branch `feature/image-removal-modes`, ready for review and intended to be squash-merged after owner smoke. Scope grew during owner smoke on 2026-10-17 from the split Trash/Delete action into Windows/WSL1 shared-state support: cross-OS read aliases (#83), WSL1 export of Windows-recorded originals, native Windows removal of WSL1-recorded originals, raw provider output disposal, confirmed-missing card clearing and presentation, per-runtime export folders and removal/bridge logging (#79 initial scope). The [smoke follow-up checklist](#owner-smoke-follow-ups) maps each owner point to its commits.

**Owner smoke accepted on 2026-10-17.** The owner reported every [Owner acceptance](#owner-acceptance) scenario passed at `6202404` (earlier items at `b10d962` and `e2b4930`). Concurrent use of one shared state from native Windows and WSL1 is an accepted limitation, not supported: SQLite WAL locking does not span the two systems, so the other side's gallery reports the store as busy. The owner declined generation-output staging or automatic recovery, and asked that no new test cases be added; GitHub Actions CI (lockfile, Ruff lint/format, SQLite, offline pytest) is the automated gate and passes on `6202404`. No agent ran real deletion, WSL2 probes, paid generation, GUI renders or inference; WSL2 remains deferred.

## Scope and agreed contract

- One split control: main action is Move to Trash or Delete permanently; the adjacent menu only chooses a mode and never submits work.
- Default Trash. Persist the mode separately for Windows, WSL and native Linux; never persist consent, substitute Permanent or replay uncertain work.
- Disabled Trash leaves the mode menu usable. Every permanent deletion needs a fresh inline irreversible warning/count and explicit Delete consent; its overlay still says permanently delete and no Undo.
- Remove managed original images only; retain metadata, audit history and exports.
- Include Windows-recorded originals from WSL through an explicit mutation boundary, not the gallery's read-only path alias. Native Windows accepts WSL1-recorded managed originals only after complete state/identity/sidecar validation and independent fresh mutation safeguards.
- Cancel between files; mode/scope/navigation changes invalidate pending consent.

## Point-1 review follow-up (#5555750519)

Owner-reported native Windows smoke passed for the initial WSL1 removal eligibility fix. Subsequent source review tightened its boundaries without changing the gallery read alias's authorization: shared state and sidecar primitives now reside in a neutral module; a SQL attempt prefilter avoids Python ownership-key work for unrelated references while full counts remain fresh at execution; SHA checking for image-operation originals remains at execution time under the lease; Windows reparse checks cover both Trash and Delete. Mixed selections, restart and helper-loss acceptance remain open. Confirmed-missing card removal and raw-artifact disposal were added later (points 2 and 7 below).

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

When a permanent-delete attempt first settles as `uncertain` during normal operation, show the existing amber/orange warning overlay once for that newly uncertain outcome. `persistence/image_recycle.py:reconcile()` now returns `ReconcileResult(trash_uncertain, delete_uncertain, withdrawn_restorations)`; the previously combined interrupted counts are separated. **After any successful reconciliation pass** (startup or later `_request_recycle_reconciliation`), warn only for that pass's newly transitioned `delete_pending` → `uncertain` records; do not start warning for Trash rows that previously reconciled silently. Aggregate counts when appropriate. Preexisting `uncertain` rows never re-alert. Do not add a tracking table, timestamp-based alerts or recovery UI.

Reconciliation requires both `recycle_batch_lock` and `original_images_lock`. When unavailable, log the skipped pass and explain in the existing readiness UI that interrupted removal intents cannot be reconciled until shared-lock readiness returns. Do not claim that they were settled. Rows still in `delete_pending` remain ineligible for retry; that is intentional. Only a successful pass can generate newly reconciled warnings. Do not assume reconciliation runs only once per session, and never replay removal. Explain that deletion **may or may not have occurred** and manual inspection is necessary. The durable `uncertain` outcome remains unchanged and blocks automatic replay; dismissal/expiry of the warning does not modify ledger state. A *confirmed* refusal before the disposition boundary, with an unchanged original, remains retryable `failed` as defined by A3. **Known limitation accepted by the owner:** if the app crashes after recording `uncertain` but before showing its warning, that outcome may never generate an overlay; durable evidence and no-replay behavior still protect the original.

### Local-save failure contract (no recovery)

In `services/generation.py`, distinguish errors raised while `saving` after the provider has returned successfully from pre-dispatch capability refusal. A lock/bridge-unavailable error, ordinary save-time lock contention (`OriginalImagesBusyError`, including its normal timeout), or `OSError` from `commit_originals` should produce a distinct `PERSISTENCE` failure such as `local_save_unavailable`, with remote outcome `SUCCEEDED` and attempt state `save_failed`. Keep distinct error types for logging and readiness/UI explanations: unsupported capability is not equivalent to a contended lease. Explain: "The image was generated but could not be saved locally; it may not have been saved or may be incomplete, and generating again may be billed." The saving loop may have written some final files before an error. Keep existing attempt/history evidence and any partial file evidence available from the current workflow; never claim that all files were absent, or that partial files constitute a completed durable save. Do not add staging, retry or provider resubmission. For pre-dispatch refusal, say nothing was submitted and therefore no provider charge was incurred by that refused request. Verify that the UI presents `failure.message` rather than replacing it with a generic error.

### WSL2 release note and refusal contract

Document in `devdocs/releases/unreleased.md` the split action, per-Windows/WSL/native-Linux preference files, ledger schema 2 and the incompatible older-reader/no-rollback consequence after first schema-2 write. Source-run WSL on Windows-hosted state requires the checkout's Windows `.venv/Scripts/python.exe`. WSL2 9p/unverified Windows mappings must fail closed for gallery original loads, generation, export, Trash and permanent deletion instead of relying on earlier Linux-only locking; interoperability with Windows locks has not been verified on real WSL2. Give an explicit workaround: move DTFlow application state to the WSL Linux filesystem (ext4), and defer WSL2 drive-mount support to issue #77 after real WSL2 probes. Mention the accepted possibility of losing remote generation output on local save failure. The refusal UI must explain the ext4 workaround.

### Owner decision — refine the split-action dropdown visually

Polish the existing joined Images action/menu control, not a redesigned component. Its compact dropdown should look like part of the button family: consistent DTFlow dark surface, shared border and rounded corners, typography, hover/focus/pressed states, and tidy alignment. Show exactly two icon-labeled choices: **Trash** and **Delete**, with a standard trash-can for Trash and a clearly legible circled X for Delete on both the menu and primary action. Keep **Delete permanently** in the fresh irreversible confirmation; short control labels do not weaken consent. Indicate the selected mode with a small checkmark and a restrained danger-red accent for permanent deletion. Keep the popup compact and closely aligned with the button; do not add a third action or custom UI framework. Use the canonical Designer form, shared QSS and tokens where appropriate. Selecting a mode only updates the existing environment-specific preference; it **never executes removal**, saves destructive consent or bypasses the required fresh confirmation. Preserve keyboard, DPI, unavailable-Trash and state-change behavior. Extend `tools/ui/render_states.py` with offline menu-open/selected/disabled fixtures; render only if separately requested.

### Preserve safety; defer unmeasured optimizations

Keep `RecycleTarget.mounts` and fresh per-file mapping discovery: the frozen-target comparison uses the snapshot to reject changed mappings. Recompute committed ownership at execution time under the original-file lease; never substitute preflight counts or narrow ownership to only the selected attempt. Process-spawn and normalized-query costs remain unmeasured follow-ups, not defects to optimize in this remediation. No dedicated recovery/reset UI, new dependency, general cross-platform state-owner/SQLite concurrency support or WSL2 implementation is included.

## Source implementation checklist

One authoritative implementation checklist follows the original commit order. An [x] means the source and/or documentation is present, **not** that Windows/WSL runtime deletion has been accepted. The separate [Owner acceptance](#owner-acceptance) checks remain open.

1. [x] **A2/A3 + reconciliation** — helper `phase:"pre"`, native `RefusedBeforeMutation`, no-replay settlement, and separate `reconcile()` counts ([`4802bdb`](https://github.com/djebaz/dtflow/commit/4802bdb0dcab32f787258890f668f095463b4aa1)).
2. [x] **B1 presentation** — separate preflight/confirmed-refusal/uncertain-execution messaging ([`2ea6656`](https://github.com/djebaz/dtflow/commit/2ea665650d031d1e8b69a79e76ec5d4a5b5f44a6)).
3. [x] **A1 readiness** — shared original-file leases, cheap static predispatch check for all generation paths, metadata/settings availability and explicit unsupported bridge state ([`8a01ada`](https://github.com/djebaz/dtflow/commit/8a01ada6367f8909ecc1d398ec72c2c7cca061c5)).
4. [x] **Local-save failures** — `PERSISTENCE`/`local_save_unavailable` for unavailable bridge, contention and disk I/O; preserve `SUCCEEDED` + `save_failed`, possible partial files and re-billing warning, without staging/retry ([`1b84415`](https://github.com/djebaz/dtflow/commit/1b84415ed335a8ed7e3a990764f19a938599d1a8)).
5. [x] **Uncertain-delete alerts** — new permanent-only uncertainty notices on execution/reconciliation, silent old/Trash uncertainties and deferred reconciliation without replay ([`76bf290`](https://github.com/djebaz/dtflow/commit/76bf2905b87a7cf674a8aa35b39f43dda586aa43)).
6. [x] **Split-menu styling** — Qt two-choice menu, icon/checkmark/danger styling and offline fixture definitions (not rendered) ([`a60c25a`](https://github.com/djebaz/dtflow/commit/a60c25afd025b2d5cde0e0e1ab27923af3c2afa1)).
7. [x] **Validation and documentation** — architecture/desktop/release/PR synchronisation and automated CI lint/format/offline pytest evidence; runtime and platform acceptance stay separate.

Review-fix commits [`7e78ff5`](https://github.com/djebaz/dtflow/commit/7e78ff55f88c3f19b622021ec346bcc4bb037ce0), [`c45484a`](https://github.com/djebaz/dtflow/commit/c45484ac1c4c7b9455704d99333cee22958b818a), and [`46b7517`](https://github.com/djebaz/dtflow/commit/46b7517a00051ac41f77322e644021e382d46c1f) close export bridge-loss handling, structured bridge reasons, precise B1 and startup notices, and Ruff formatting. No new special-generation recovery or Windows/WSL compatibility claim was added.

### Owner smoke follow-ups

Points 1–12 come from the owner's 2026-10-17 smoke comments on PR #76. [x] means source and documentation are present, not runtime acceptance.

1. [x] **Native Windows Trash/Delete of WSL1-recorded originals** — deletion-specific resolution with full state/identity/sidecar/digest checks, never the read alias ([`3b73a7a`](https://github.com/djebaz/dtflow/commit/3b73a7a), [`a4ed276`](https://github.com/djebaz/dtflow/commit/a4ed276)). Owner reported the initial native Windows smoke passed.
2. [x] **Delete clears confirmed-missing cards** — durable `absent` outcome, hidden from gallery and export, history and exports kept ([`4123efa`](https://github.com/djebaz/dtflow/commit/4123efa)); whole-attempt-folder deletion accepted only while the managed `outputs` folder is intact and listable ([`cec30e8`](https://github.com/djebaz/dtflow/commit/cec30e8)).
3. [x] **Missing vs unavailable presentation** — **File missing** pictogram in Grid/List/Detail, **Image unavailable** with the reason on hover otherwise ([`f8343d2`](https://github.com/djebaz/dtflow/commit/f8343d2)). Presentation only; Delete re-proves absence.
4. [x] **Per-runtime export folders** — `export_preferences.json` schema 2 ([`fed0840`](https://github.com/djebaz/dtflow/commit/fed0840)).
5. [x] **WSL creation-time note is informational** ([`7d17138`](https://github.com/djebaz/dtflow/commit/7d17138)).
6. [x] **Windows inpaint outputs missing from the WSL grid** — retracted by the owner after retest; no change.
7. [x] **Raw provider output disposed with its final** in both modes ([`c70ac91`](https://github.com/djebaz/dtflow/commit/c70ac91)).
8. [x] **Read-action wording** — no exception class names, actionable busy text ([`f8343d2`](https://github.com/djebaz/dtflow/commit/f8343d2)). Per-item leases for Copy Paths are deferred: unmeasured, and they touch the verification boundary.
9. [x] **Thumbnail lease and verification cost** — owner asked for items 1–3 of the performance review in this PR: one long-lived Windows lease helper per process instead of a process per lease; the ownership-count SQL applies its attempt substring filter before the Python key callback; thumbnails read bytes under the lease and decode outside it, and display reads reuse an image-edit digest already proven in this process for the same path/size/mtime/inode (removal and export always re-hash). Copy Paths batch leasing and per-file removal reference scans stay deferred.
10. [x] **Legacy sidecar and alias limits** — documented as accepted limits in the release notes; no bypass added.
11. [x] **Export decision record and lease scope** — recorded in [image export architecture](../architecture/image-export.md); PR title and body updated.
12. [x] **Polish** — fonts built once per card ([`f8343d2`](https://github.com/djebaz/dtflow/commit/f8343d2)); icon DPI left as owner-accepted.

Issue #79's agreed initial logging scope landed in [`7cfa95b`](https://github.com/djebaz/dtflow/commit/7cfa95b). Issues #78, #80 and #81 are handled outside this PR (PR #85); #77 (WSL2) stays deferred.

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

The completed items below describe the initial implementation at `b935bca`. The source-remediation checklist above records subsequent work, while owner acceptance remains pending.

- [x] Read current removal service, ledger, gallery and inline consent paths.
- [x] Implement mode state and explicit policy change in AGENTS/design/docs.
- [x] Complete focused path/lock probes and record the minimal safe boundary.
- [x] Extend the existing service with guarded permanent mode/distinct outcomes; ledger schema 2 preserves schema-1 records and refuses old-app access after upgrade.
- [x] Wire split UI and invalidate stale consent on mode changes.
- [x] Finish offline previews, docs, release notes and PR body; implementation goes to the same draft PR.

## Probes / acceptance

Restrict agent probes to non-mutating path/handle inspection and disposable-file locks; never delete user images or run paid generation. The initial implementation at `b935bca` had its own Ruff evidence (9 changed Python files formatted and 320 files checked). For the **source-remediation head `46b7517`**, GitHub Actions run [38984516615](https://github.com/djebaz/dtflow/actions/runs/38984516615) completed successfully: lockfile, Ruff lint/format, SQLite check and offline pytest. The preceding run at `c45484a` failed only Ruff formatting; that was fixed by `46b7517`. No real deletion, WSL2/9p verification, native GUI/DPI check, generation/inference or potentially billable operation was performed for the remediation. Automated CI does not establish Windows/WSL runtime acceptance.

### Owner acceptance

- [x] Owner missing Windows `.venv`, unavailable helper and unsupported-mapping checks: app remains open, settings/metadata remain usable, original reads refuse clearly and no generation is submitted (owner-reported at `6202404`).
- [x] Owner readiness-change checks after startup and before operation start, including queue/image-operation dispatch; no silent `flock` fallback or stale readiness authorization (owner-reported at `6202404`).
- [x] Owner native/helper pre-mutation sharing/permission/validation refusal: unchanged file settles `failed`, later files continue, and a fresh user-authorized retry is possible (owner-reported at `6202404`).
- [x] Owner helper timeout/termination or post-disposition uncertainty: retain manual-review/no-replay outcomes, never relabel as a safe retry because the source still exists (owner-reported at `6202404`).
- [x] Owner refusal wording distinguishes zero-mutation preflight from partial execution and unconfirmed/durable outcomes; unsupported WSL2 remains explicitly deferred (owner-reported at `6202404`).
- [x] Owner Windows/WSL split-menu icons, labels, confirmation and Cancel/Escape/mode-change consent invalidation (owner-reported at `b10d962`).
- [x] Owner separate Windows/WSL mode preference acceptance (owner-reported at `6202404`).
- [x] Owner mixed native/Windows-recorded managed-source checks and confirmations (owner-reported at `6202404`).
- [x] Owner WSL1 permanent deletion of final images (single, five and mixed origins) with restart persistence (owner-reported at `e2b4930`).
- [x] Owner partial/failed/uncertain deletion and interruption no-replay (owner-reported at `6202404`).
- [x] Owner two-process Windows/WSL lock exclusion and helper interruption (owner-reported at `6202404`).
- [x] Owner cancellation, changed file/reference/mount and redirect refusals (owner-reported at `6202404`).
- [x] Owner exports/history/metadata untouched and Trash regression checks (owner-reported at `6202404`).
- [x] Owner local-save unavailability, contended `OriginalImagesBusyError` and disk `OSError`: remote `SUCCEEDED` plus `save_failed`, `PERSISTENCE`/`local_save_unavailable`, message allowing partial files and re-billing, UI preserves `failure.message`, no new staging or resubmission (owner-reported at `6202404`).
- [x] Owner normal-operation warning: amber/orange overlay appears once when a permanent-delete attempt newly settles `uncertain`, without triggering another deletion (owner-reported at `6202404`).
- [x] Owner reconciliation warning: after any successful pass, alert only newly transitioned permanent `delete_pending` rows, not Trash `pending` or older `uncertain`; a skipped pass reports that unsettled rows remain blocked and a later successful pass warns once (owner-reported at `6202404`).
- [x] Owner split-menu visual, keyboard, accessibility/DPI and disabled-Trash acceptance; choosing a mode never performs deletion (owner-reported at `6202404`).
- [x] Owner native Windows Trash and Delete of WSL1-recorded originals, plus a Windows-generated control image; unverifiable mappings stay ineligible, not "missing" (owner-reported at `6202404`).
- [x] Owner raw-1.png disposal with its final in both modes, raw absent, raw refused and partial outcomes (owner-reported at `6202404`).
- [x] Owner confirmed-missing card (final deleted, and whole attempt folder deleted) cleared by Delete and absent after restart; an unreachable location is not cleared (owner-reported at `6202404`).
- [x] Owner File missing vs Image unavailable presentation in Grid, List and Detail (owner-reported at `6202404`).
- [x] Owner per-runtime export folders across Windows → WSL → Windows, and green WSL export when the creation-time note is the only notice (owner-reported at `6202404`).
- [x] Owner `-x` session log for a successful removal, a refused preflight and a reconciliation pass, with no paths or prompts (owner-reported at `6202404`).

# Plan: explicit Trash / permanent-delete split action

## Status and working rules

Draft PR #76, branch `feature/image-removal-modes`. Split action, environment preferences, explicit permanent mode and Windows/WSL safeguards are source-present; runtime removal acceptance is pending. Reused the existing workflow rather than adding a parallel deletion framework. No merge, tests, builds or renders by the agent. Owner additionally permits focused, non-destructive WSL CLI probes.

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

- [x] Read current removal service, ledger, gallery and inline consent paths.
- [x] Implement mode state and explicit policy change in AGENTS/design/docs.
- [x] Complete focused path/lock probes and record the minimal safe boundary.
- [x] Extend the existing service with guarded permanent mode/distinct outcomes; ledger schema 2 preserves schema-1 records and refuses old-app access after upgrade.
- [x] Wire split UI and invalidate stale consent on mode changes.
- [x] Finish offline previews, docs, release notes and PR body; implementation goes to the same draft PR.

## Probes / acceptance

Restrict agent probes through wsl.exe to non-mutating capability/path/handle inspection and disposable-file locks; never delete user images or run generation. No tests, builds or renders under fast-implement. Owner additionally requested Ruff lint/format: environment readiness passed; `uv run --frozen ruff check .` passed; Ruff formatted 9 changed Python files and repository-wide `ruff format --check .` passed (320 files). Record these static checks separately from runtime acceptance.

- [ ] Owner Windows/WSL split-menu and separate preference acceptance.
- [ ] Owner mixed native/Windows-recorded managed-source checks and confirmations.
- [ ] Owner permanent deletion, partial/failed/uncertain and restart/no-replay.
- [ ] Owner two-process Windows/WSL lock exclusion and helper interruption.
- [ ] Owner cancellation, changed file/reference/mount and redirect refusals.
- [ ] Owner exports/history/metadata untouched and Trash regression checks.

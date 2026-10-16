# Plan: explicit Trash / permanent-delete split action

## Status and working rules

Draft PR #76, branch `feature/image-removal-modes`. Plan checkpoint only; no
removal code implemented. Reuse the existing workflow rather than adding a
parallel deletion framework. No merge, tests, builds or renders by the agent.
Owner additionally permits focused, non-destructive WSL CLI probes.

## Scope and agreed contract

- One split control: main action is Move to Trash or Delete permanently; the
  adjacent menu only chooses a mode and never submits work.
- Default Trash. Persist the mode separately for Windows, WSL and native Linux;
  never persist consent, substitute Permanent or replay uncertain work.
- Disabled Trash leaves the mode menu usable. Every permanent deletion needs a
  fresh inline irreversible warning/count and explicit Delete permanently consent.
- Remove managed original images only; retain metadata, audit history and exports.
- Include Windows-recorded originals from WSL through an explicit mutation
  boundary, not the gallery's read-only path alias.
- Cancel between files; mode/scope/navigation changes invalidate pending consent.

## Minimal implementation direction

Reuse existing managed-root, bounded preflight, atomic intent and durable outcomes.
Keep Trash and permanent outcomes distinct in the shared removal ledger, and hide
both removed identities without discarding attempt history. Persist mode in
separate environment-specific convenience files; unreadable/newer state fails
closed for saving, not an implicit switch of action.

Keep orchestration, confirmation and ledger settlement in the existing removal
workflow. Add one explicit operation mode and only the platform-specific file/lock
code needed for safety. Do not duplicate the complete service inside a helper.

Before implementing mapped Windows deletion from WSL, use focused probes to
establish path identity and shared-lock behavior. Do not assume Linux `flock`
and Windows `msvcrt` exclude each other. If a Windows-side helper is necessary,
keep it limited to the demonstrated platform boundary; choose its interface
after the probes, not before. Missing support refuses the operation clearly.

Execution must revalidate the exact confirmed managed original, its current
committed references and its mapping while holding the relevant leases. Reject
changed files, redirected paths, hardlinks, ambiguous mounts and custom output
roots. A missing file after an unconfirmed operation is uncertain, not proof of
successful deletion. Never replay pending/uncertain/removed outcomes.

## Ordered implementation steps

1. Inspect existing preflight, eligibility, reference counting, intent claim and
   settlement; identify the smallest reusable changes for an explicit mode.
2. Probe WSL path identity and Windows/WSL lock exclusion using disposable files.
   Resolve the mutation/lock boundary before implementing permanent file removal.
3. Extend the existing service/ledger for distinct permanent outcomes. Keep
   per-file revalidation, durable intent before mutation, and no automatic replay.
4. Add environment-specific mode persistence, then the canonical split control.
   Mode selection alone does no work; all consent remains session-only.
5. Wire explicit irreversible consent, progress/results and stop-between-files
   through the existing controller. Invalidate consent on mode/scope/navigation.
6. Extend offline preview states, synchronize policy/design/docs/release notes,
   push implementation to this same draft PR and hand runtime acceptance to owner.

### Safety gate before permanent-removal code

The exact shared-lock and mutation implementation remains to be established by
source inspection/probes. Do not substitute a raw mapped-path unlink if this
gate cannot be met. Record the chosen small boundary and its evidence here.

## GUI components

- [ ] Canonical Images Designer split-control layout and shared QSS.
- [ ] Mode menu, independent availability and mode persistence.
- [ ] Inline preflight/confirmation/progress/results and cancellation.
- [ ] Offline fixture states for unavailable Trash and permanent consent.
- No provider, model, packaging or dependency changes planned.

## Action items

- [x] Read current removal service, ledger, gallery and inline consent paths.
- [ ] Implement mode state and explicit policy change in AGENTS/design/docs.
- [ ] Complete focused path/lock probes and record the minimal safe boundary.
- [ ] Extend the existing service with guarded permanent mode/distinct outcomes.
- [ ] Wire split UI and invalidate stale consent on mode changes.
- [ ] Update offline previews, docs, release notes and PR body.

## Probes / acceptance

Restrict agent probes through wsl.exe to non-mutating
capability/path/lock inspection and disposable-file locks; never delete user
images or run generation. No tests, builds, renders or broad validation under
fast-implement. Record exact probes performed separately from source presence.

- [ ] Owner Windows/WSL split-menu and separate preference acceptance.
- [ ] Owner mixed native/Windows-recorded managed-source checks and confirmations.
- [ ] Owner permanent deletion, partial/failed/uncertain and restart/no-replay.
- [ ] Owner two-process Windows/WSL lock exclusion and helper interruption.
- [ ] Owner cancellation, changed file/reference/mount and redirect refusals.
- [ ] Owner exports/history/metadata untouched and Trash regression checks.

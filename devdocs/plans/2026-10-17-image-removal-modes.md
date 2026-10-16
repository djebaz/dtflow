# Plan: explicit Trash / permanent-delete split action

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

## Implementation direction

Reuse existing managed-root, bounded preflight, atomic intent and durable outcomes.
Keep Trash and permanent outcomes distinct in the shared removal ledger, and hide
both removed identities without discarding attempt history. Persist mode in
separate environment-specific convenience files; unreadable/newer state fails
closed for saving, not an implicit switch of action.

WSL shared-drive mutations execute in a Windows-side source helper using this
checkout's Windows `.venv/Scripts/python.exe`; no interpreter download or PATH
guess. The helper owns native path validation, Windows locks, ledger settlement
and handle-based deletion. A verified state-root mapping provides only the exact
Linux/Windows state alias. Missing bridge/unsupported mount refuses deletion.
No arbitrary delete-by-path endpoint or permanent fallback is exposed.

Shared-drive original/batch leases from WSL must use the same Windows byte-range
locks as native execution; do not assume flock and msvcrt interoperate. Helper
loss after mutation leaves pending intent for uncertain reconciliation, never retry.
Native Linux uses checked directory-relative unlink under the existing leases;
no symlinks, hardlinks or custom output roots. Cross-platform runtime acceptance
and unsupported native-filesystem races remain explicit owner gates.

## GUI components

- [ ] Canonical Images Designer split-control layout and shared QSS.
- [ ] Mode menu, independent availability and mode persistence.
- [ ] Inline preflight/confirmation/progress/results and cancellation.
- [ ] Offline fixture states for unavailable Trash and permanent consent.
- No provider, model, packaging or dependency changes planned.

## Action items

- [x] Read current removal service, ledger, gallery and inline consent paths.
- [ ] Implement mode state and explicit policy change in AGENTS/design/docs.
- [ ] Implement guarded permanent service and distinct durable outcomes.
- [ ] Implement Windows helper and WSL shared-drive lock/mutation routing.
- [ ] Wire split UI and invalidate stale consent on mode changes.
- [ ] Update offline previews, docs, release notes and PR body.

## Probes / acceptance

Owner permits CLI probes through wsl.exe. Restrict agent probes to non-mutating
capability/path/lock inspection and disposable-file locks; never delete user
images or run generation. No tests, builds, renders or broad validation under
fast-implement. Record exact probes performed separately from source presence.

- [ ] Owner Windows/WSL split-menu and separate preference acceptance.
- [ ] Owner mixed native/Windows-recorded managed-source checks and confirmations.
- [ ] Owner permanent deletion, partial/failed/uncertain and restart/no-replay.
- [ ] Owner two-process Windows/WSL lock exclusion and helper interruption.
- [ ] Owner cancellation, changed file/reference/mount and redirect refusals.
- [ ] Owner exports/history/metadata untouched and Trash regression checks.

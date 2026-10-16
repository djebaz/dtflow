# Saved-image system Trash architecture

## Boundaries

`services/image_recycle.py` owns frozen targets, managed-root eligibility, revalidation, per-image durable checkpoints and partial outcomes. It imports no Qt. The desktop injects `ui/system_trash.py` as the adapter and its non-mutating source eligibility check. `recycle_outputs` requires an explicit adapter; there is no default destructive implementation. `ui/image_recycle_panel.py` owns confirmation/progress and executes preflight and mutation off the GUI thread.

## Qt implementation

The locked and installed Qt version inspected on 2026-10-09 is 6.11.2. The adapter creates one worker-local QFile per original and uses its boolean `moveToTrash()` overload. The static Python overload returns `(bool, str)` and is not used. No Send2Trash dependency or handwritten COM deletion remains. Versioned upstream evidence and API checks are recorded in the [implementation plan](../plans/2026-10-09-qt-system-trash.md).

Qt capability detection is the only platform gate. Managed-original validation remains in the shared service; there is no OS allowlist, filesystem-type allowlist or fixed-drive restriction. Qt determines whether Trash is usable for each file. Its inspected Windows implementation requests recycling and vetoes non-recycling; Linux uses Freedesktop Trash and macOS its system Trash API. Qt may fail on permissions, settings or unusual mounts. Capability detection is not a per-file guarantee. Custom output roots and exported copies remain excluded; failed moves never trigger permanent deletion or automatic retry.

When `QFile.supportsMoveToTrash` is absent or not callable (for example, Qt 6.8.3),
the adapter reports Trash unavailable without probing files. Desktop startup
continues with Delete disabled; direct source checks refuse the operation. This
does not certify older Qt or WSL application support, change dependencies, or add
a permanent-delete fallback.

## Evidence, persistence and concurrency

Success requires Qt success, source absence and a verifiable regular-file receipt at the returned Trash path with matching size/mtime. Qt can report success without a destination path. Missing/inaccessible/mismatched receipt evidence retains strict verification and produces a sanitized receipt uncertainty; the UI says Qt reported success but the destination could not be verified, rather than claiming the operation failed; the service records uncertain if the original disappeared or changed. Neither receipt paths nor raw Qt errors are logged or persisted. No permanent-delete fallback, automatic replay or DTFlow Undo exists. The existing separately versioned `image_recycle.sqlite3` ledger and original-file / batch leases remain unchanged; cache cleanup never owns these durable files. Interrupted records remain uncertain. Refresh does not automatically restore availability when a file reappears at its old path: matching size/mtime is not proof of original content. Recycled records remain hidden, and old heuristic `restored` outcomes are conservatively returned to `recycled` without touching files. Reconciliation still converts interrupted pending intent to uncertain, without replay; its second returned count now reports withdrawn unverified restorations, allowing the gallery to refresh. A future explicit content-verified recovery workflow is required before making recovered artifacts available; none is introduced here and no schema migration is needed. macOS Qt does not provide Finder's automatic Put Back integration; manual recovery must not be advertised as guaranteed restoration.

## Acceptance

Static implementation evidence only. No tests, GUI smoke, actual recycling, inference or billable actions ran for this change. Owner Windows/Debian/macOS trash/restore and interrupted-operation checks remain required; this adapter does not certify the whole DTFlow application on Debian or macOS.

## Review follow-up: bounded preflight

`persistence/gallery.recycle_preflight_batch` reads at most 128 selected records (identity, attempt, ordinal, original and metadata paths only), then grouped committed path-reference counts, in one read-only transaction. Selected attempt IDs constrain discovery; path counts include references irrespective of recycle state. The service keeps all per-file managed-root, redirect, regular-file, ownership and capability checks. Preflight snapshots are advisory: execution still takes a per-image original-file lease and performs fresh record/path-count and filesystem revalidation immediately before mutation. This does not alter Qt receipt verification, durable intent or per-image outcomes. See the [review follow-up plan](../plans/2026-10-09-pr57-review-followup.md); new runtime acceptance remains pending.

## Atomic execution intent
After fresh per-image filesystem/ownership checks, while holding both the operation batch lease and original-file lease, execution calls `persistence/image_recycle.claim_recycle_intent`. The shared record UPSERT has a conditional conflict update permitting only a previous `failed` outcome; a new identity can be inserted. This checks the latest durable outcome and writes `pending` atomically in the existing `BEGIN IMMEDIATE` transaction, without a separate read/write race or full-ledger scan. Pending, uncertain, recycled, historical restored and unknown outcomes are not overwritten. A rejected claim becomes ineligible/manual review (`ledger_requires_review`) and never calls the adapter. Intent commits before the external Trash operation; completion still uses per-image durable checkpoints. No schema, Qt receipt or export-controller changes. Cross-process stale-plan/crash acceptance remains pending; source/static checks are not runtime verification.


## Inline desktop presentation

The desktop freezes preflight scope and presents eligibility/hidden-count/recovery warnings
in the shared inline details surface. Only the existing Delete button becomes red Confirm;
Cancel is separate. Selection/filter/navigation revisions invalidate preflight/consent.
A fresh busy check precedes worker execution; leases, per-file revalidation and atomic durable
intent claims remain unchanged. Trash progress and safe stop live in the shared bottom
activity overlay; results are inline. No new actual Trash/platform acceptance is implied.

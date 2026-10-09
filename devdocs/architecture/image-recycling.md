# Saved-image system Trash architecture

## Boundaries

`services/image_recycle.py` owns frozen targets, managed-root eligibility, revalidation, per-image durable checkpoints and partial outcomes. It imports no Qt. The desktop injects `ui/system_trash.py` as the adapter and its non-mutating source eligibility check. `recycle_outputs` requires an explicit adapter; there is no default destructive implementation. `ui/image_recycle_panel.py` owns confirmation/progress and executes preflight and mutation off the GUI thread.

## Qt implementation

The locked and installed Qt version inspected on 2026-10-09 is 6.11.2. The adapter creates one worker-local QFile per original and uses its boolean `moveToTrash()` overload. The static Python overload returns `(bool, str)` and is not used. No Send2Trash dependency or handwritten COM deletion remains. Versioned upstream evidence and API checks are recorded in the [implementation plan](../plans/2026-10-09-qt-system-trash.md).

Qt capability detection is the only platform gate. Managed-original validation remains in the shared service; there is no OS allowlist, filesystem-type allowlist or fixed-drive restriction. Qt determines whether Trash is usable for each file. Its inspected Windows implementation requests recycling and vetoes non-recycling; Linux uses Freedesktop Trash and macOS its system Trash API. Qt may fail on permissions, settings or unusual mounts. Capability detection is not a per-file guarantee. Custom output roots and exported copies remain excluded; failed moves never trigger permanent deletion or automatic retry.

## Evidence, persistence and concurrency

Success requires Qt success, source absence and a verifiable regular-file receipt at the returned Trash path with matching size/mtime. Qt can report success without a destination path. Missing/inaccessible/mismatched receipt evidence retains strict verification and produces a sanitized receipt uncertainty; the UI says Qt reported success but the destination could not be verified, rather than claiming the operation failed; the service records uncertain if the original disappeared or changed. Neither receipt paths nor raw Qt errors are logged or persisted. No permanent-delete fallback, automatic replay or DTFlow Undo exists. The existing separately versioned `image_recycle.sqlite3` ledger and original-file / batch leases remain unchanged; cache cleanup never owns these durable files. Interrupted records remain uncertain. Explicit Refresh can reconcile an original manually restored to its recorded path, without restoring actionable selection. macOS Qt does not provide Finder's automatic Put Back integration; manual recovery must not be advertised as guaranteed restoration.

## Acceptance

Static implementation evidence only. No tests, GUI smoke, actual recycling, inference or billable actions ran for this change. Owner Windows/Debian/macOS trash/restore and interrupted-operation checks remain required; this adapter does not certify the whole DTFlow application on Debian or macOS.

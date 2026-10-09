# Saved-image system Trash architecture

## Boundaries

`services/image_recycle.py` owns frozen targets, managed-root eligibility, revalidation, per-image durable checkpoints and partial outcomes. It imports no Qt. The desktop injects `ui/system_trash.py` as the adapter and its non-mutating source eligibility check. `recycle_outputs` requires an explicit adapter; there is no default destructive implementation. `ui/image_recycle_panel.py` owns confirmation/progress and executes preflight and mutation off the GUI thread.

## Qt implementation

The locked and installed Qt version inspected on 2026-10-09 is 6.11.2. The adapter creates one worker-local QFile per original and uses its boolean `moveToTrash()` overload. The static Python overload returns `(bool, str)` and is not used. No Send2Trash dependency or handwritten COM deletion remains. Versioned upstream evidence and API checks are recorded in the [implementation plan](../plans/2026-10-09-qt-system-trash.md).

Windows keeps the local fixed-drive restriction. Qt requests recycling and its progress sink rejects non-recycling; failed COM creation does not take a legacy fallback in the inspected implementation. Linux uses Qt's Freedesktop Trash implementation; macOS uses its system Trash API. Initial POSIX eligibility accepts only ext2/ext3/ext4, btrfs, xfs, f2fs, zfs, apfs and hfs filesystem types reported by QStorageInfo. Unknown/network/FUSE filesystems fail closed. This allowlist is a conservative eligibility policy, not certification of every filesystem configuration. Qt may still fail on permissions, settings or unusual mounts. Capability detection is not a per-file guarantee. Custom output roots and exported copies remain excluded.

## Evidence, persistence and concurrency

Success requires Qt success, source absence and a verifiable regular-file receipt at the returned Trash path with matching size/mtime. Missing/inaccessible/mismatched receipt evidence throws a sanitized failure; the service records uncertain if the original disappeared or changed. Neither receipt paths nor raw Qt errors are logged or persisted. No permanent-delete fallback, automatic replay or DTFlow Undo exists. The existing separately versioned `image_recycle.sqlite3` ledger and original-file / batch leases remain unchanged; cache cleanup never owns these durable files. Interrupted records remain uncertain. Explicit Refresh can reconcile an original manually restored to its recorded path, without restoring actionable selection. macOS Qt does not provide Finder's automatic Put Back integration; manual recovery must not be advertised as guaranteed restoration.

## Acceptance

Static implementation evidence only. No tests, GUI smoke, actual recycling, inference or billable actions ran for this change. Owner Windows/Debian/macOS trash/restore and interrupted-operation checks remain required; this adapter does not certify the whole DTFlow application on Debian or macOS.

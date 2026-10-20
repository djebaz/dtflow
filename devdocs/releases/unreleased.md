# Unreleased

The consolidated changes since 4.0.0 are in [5.0.0 release preparation](v5.0.0.md). This file tracks only changes after that scope; open follow-up issues are not implemented release entries.

## Highlights

## Added

## Changed

- **Queues: unknown outcomes no longer block (#132, #120):** an item whose final result never arrives is settled as `unknown` (shown as *no result*, with a note that it most likely failed), releases its reservation and does not block its queue, other queues or the backend; it is still never replayed automatically. The Acknowledge/Override control, the always-visible Uncertainty card and the `blocked` queue status are removed (the card is now an *Outcome* note shown only for no-result and failed items), `queue override` is a hidden no-op, and `queue start` exits `3` instead of `4` when items are unknown. Cancel no longer asks for confirmation. Existing unknown rows become non-blocking without migration. Part of the Queues redesign epic #130; not yet rendered or accepted on Windows.
- **Logs workspace presentation (#108):** the event column header paints separate `COMPONENT` and `EVENT` sort targets in one section, and each event cell shows its component above the event chip; with Follow off, Refresh shows the number of matching arrivals since the shown snapshot (`Refresh (3 new)`, or `Refresh (0 new · incomplete)` when evidence is incomplete even with no matches) through a single bounded background count; empty sessions and filters with no matches show a panel in place of the table; a primary click elsewhere in Logs collapses the details pane without consuming the click, while splitter, scroll bar and row-action presses keep it open. Refresh and Follow carry icons, and the Refresh icon turns while a page read (including a Follow refresh) runs; the session selector has a bounded width with a shorter `#N · time · short id` label and the full id in its tooltip. While Logs is open, no header navigation tab stays selected; closing Logs reselects the previous page's tab. Not yet rendered or accepted on Windows.

## Fixed

- **Logs export (#111):** exporting the current, still-growing session works: the export pins the session as it stood at start, follows rotation renames and leaves later appends out. Unsealed or incomplete sessions export with their evidence state and reasons disclosed instead of being refused. Exports show progress with Cancel in the shared bottom overlay, run one at a time, keep running and report their outcome after leaving Logs, and summarize the result as file name, record count and source state instead of a raw manifest. A reader pass no longer treats appends to the same files as a snapshot change, so busy live sessions no longer read as incomplete for that reason. Export selection now rejects a conservative candidate byte-budget overrun explicitly (no partial success); full pipeline memory budgets remain #106. Not yet accepted on Windows.

## Release audit

- Pending PR: [#111](https://github.com/djebaz/dtflow/pull/111) (implements [issue #108](https://github.com/djebaz/dtflow/issues/108)); this is post-5.0.0-release-preparation source work, not part of the merged [#109](https://github.com/djebaz/dtflow/pull/109) version bump.
- Acceptance still required: native Windows Logs/export/cancellation/fault checks (#107) and end-to-end worker/pending/display budgets (#106). No tag or publication is implied by this note.

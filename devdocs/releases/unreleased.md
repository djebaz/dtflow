# Unreleased

The consolidated changes since 4.0.0 are in [5.0.0 release preparation](v5.0.0.md). This file tracks only changes after that scope; open follow-up issues are not implemented release entries.

## Highlights

## Added

## Changed

- **Logs workspace presentation (#108):** the event column header paints separate `COMPONENT` and `EVENT` sort targets in one section, and each event cell shows its component above the event chip; with Follow off, Refresh shows the number of matching arrivals since the shown snapshot (`Refresh (3 new)`, marked `incomplete` when session evidence is incomplete) through a single bounded background count; empty sessions and filters with no matches show a panel in place of the table; a primary click elsewhere in Logs collapses the details pane without consuming the click, while splitter, scroll bar and row-action presses keep it open. Refresh and Follow carry icons, and the Refresh icon turns while a page read (including a Follow refresh) runs; the session selector has a bounded width with a shorter `#N · time · short id` label and the full id in its tooltip. While Logs is open, no header navigation tab stays selected; closing Logs reselects the previous page's tab. Not yet rendered or accepted on Windows.

## Fixed

## Release audit

- PRs: #109
- Scope: Version bump to 5.0.0 and consolidation of final behavior/PR references.
- Post-5.0.0: Logs presentation follow-up for #108.

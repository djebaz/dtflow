# Unreleased

Changes through 5.5.2 are consolidated in [DTFlow 5.5.2](v5.5.2.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

## Added

## Changed

- Development GUI heartbeat monitoring is application-wide and activity-scoped: visible Images browsing or existing in-window execution/progress work, not idle Logs. Restart resets the baseline; `ui.gui_gap` reports the active workspace/operation instead of stale gallery context. Existing benchmark readers retain historical log support without a duplicate producer. This reduces observation scope/noise, not measured stall duration; #263's actual-stall investigation and #261 remain open.

- Logs initially opens on >=WARNING (WARNING, ERROR and CRITICAL). All severity choices are consistent thresholds (ALL, >=DEBUG, >=INFO, >=WARNING, >=ERROR, >=CRITICAL); selection survives navigation and Follow, and Reset restores >=WARNING. Source/static changes and GitHub CI do not establish native Windows or repeated-stall acceptance (#263).

## Fixed

## Release audit

- PRs: #264
- Scope: Issue #263 scoped heartbeat lifecycle/context and Logs severity thresholds. Actual-stall investigation remains open; no local tests or renders.

# Plan: readable Logs filter dropdowns

## Scope

- Size the Component and Event popup lists to their item text independently of the compact closed controls, bounded by available screen width with non-elided scrollable overflow.
- Hide only the leading `dtflow.` in Component and exact Event display labels; retain canonical values for filtering and expose them in tooltips.
- Preserve event-family grouping, query behavior, forms, styles and log records. Keep this independent of checksum-fix PR #158.

## Action items

- [x] Implement popup sizing and display-only labels/tooltips through the existing Logs controller.
- [x] Add offline Component/Event popup preview definitions using real facet population; native popup compositing uses its actual position rather than assuming the custom popup placement API.
- [x] Synchronize desktop guide, current status and unreleased notes.
- [ ] Commit/push a separate PR; do not merge.

## Evidence and acceptance

The owner screenshot shows Component popup entries elided to unreadable fragments; Event has the same issue. Existing Session popup sizing provides an analogous implementation. Official Qt for Python documentation confirms `QComboBox.view()` exposes the owned popup view and its item sizing APIs. No tests, validation, renders or runtime/DPI checks will be run; source presence is not native Windows acceptance.

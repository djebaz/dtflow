# Plan: Global run status ring

## Goal

Remove the right-panel show/hide button from the persistent DTFlow header and put one compact **run status ring** in its place. The ring shows, from every workspace, whether DTFlow is generating or dispatching a queue and, when trustworthy, the current step. Clicking it navigates to the page that owns the controls.

The ring is a presentation surface over existing services. It is not a second execution state machine and it does not start, pause, resume or cancel anything.

## Decisions

- **Status plus navigation, not control.** Start/Pause/Resume stay on the Queues page. Routing header clicks into queue controls was rejected: Start versus Resume depends on whether the queue ever started (`controls()` offers Resume only after a recorded `started` event); Resume and Pause only post a request that the dispatch owner applies at its next poll; and the header would need its own queue state source because the selected queue and refresh timer live in `QueuePage`. A status ring needs none of that.
- **No single-generation pause.** DTFlow has no backend-neutral pause/resume contract for a single generation, only local stop. The ring must not imply otherwise. Pause/Resume from the header can be added later, only while this window owns dispatch and only through the existing `controls()` rule.
- **No panel toggle at all.** The right inspector follows the layout: shown on Connection, Generate and Queues at normal widths, hidden on Images, hidden below 1100 px. Narrow windows therefore lose manual access to the inspector (LoRAs, assets, evidence); this is an accepted trade-off by the owner and is recorded in `DESIGN.md`.

## Current source evidence

- `src/dtflow/ui/forms/workspace_shell.ui` defines `inspectorToggle` (30×30 checkable button) in the brand row.
- `MainWindow` wires it to responsive right-rail visibility (`_inspector_wanted`, `_inspector_auto`, `_toggle_inspector`, `_responsive_workspace`) and hides it on Images.
- `Activity` (`ui/activity.py`) holds only `generating` and `dispatching` plus listeners.
- `GenerationPage._event` already receives `BackendEvent.completed` / `.total` and drives its own progress.
- `QueuePage.start()` sets `activity.dispatching` while this window dispatches.

## Interaction

| State | Ring | Click |
| --- | --- | --- |
| Idle | dim, static, disabled | none |
| Generating | lit; `completed/total` of the segments when known, otherwise all lit and slowly rotating; step number in the center when known | go to Generate |
| Dispatching a queue from this window | same visual language; number only if dispatcher progress events supply an unambiguous one | go to Queues |

- A queue running in another process is not shown; the Queues page already reports that case ("running, not dispatched here"). State this limit in the docs.
- Failure and completion get no persistent treatment. Messages stay on their pages. A short-lived settled flash is optional and out of scope for the first version.
- Tooltip and accessible name always state the state in words, for example "Generating, step 8 of 30", "Dispatching queue", "Idle".

## Visual spec

- Fixed 30×30 px, positioned slightly left of the former toggle slot, in the brand row. The rest of the header geometry is preserved.
- Eight arc segments with gaps, drawn with `QPainter` using existing design tokens (neutral/muted for idle and unlit segments, the selection/accent color for lit segments). No new semantic color unless a missing one is demonstrated, in which case add it to `tokens.json` and the `DESIGN.md` snapshot together.
- Center step number must stay legible at 30 px; hide it above three digits.
- Rotation (about 120 ms tick) only while running with unknown total; otherwise static. This doubles as the reduced-motion fallback because the state is understandable without animation.
- Visible keyboard focus ring using the shared focus token.

## Implementation

1. **Designer shell.** In `workspace_shell.ui`, delete the whole `inspectorToggle` item and put a fixed 30×30 `runControl` widget in the same slot, positioned as above. Register the custom widget with `QUiLoader` next to `ImagePreview` and `SearchableComboBox` in `main_window.py`.
2. **`ui/run_control.py`.** `RunControl(QAbstractButton)`: paint only. API: `set_state(kind, completed, total)` with `kind` in `none | generation | queue`; updates tooltip and accessible name; emits the standard `clicked`. It reads no persistence, calls no services and owns no state beyond what it paints.
3. **`Activity`.** Keep `generating`, `dispatching`, `set()` and `listen()` unchanged. Add a private progress pair and `set_progress(completed, total)` that notifies the same listeners. Clear progress whenever either boolean turns false. Expose a read-only `progress` property.
4. **Generation progress.** In `GenerationPage._event`, where `event.completed` / `event.total` are already used, also call `activity.set_progress(...)`. Do not compute progress a second way.
5. **Queue progress.** Publish a number only if the dispatcher events already recorded or exposed for this window's dispatch give one unambiguous step. With concurrent items and no clear single step, show activity without a number.
6. **`MainWindow`.** `activity.listen(self._sync_run_control)` pushes `kind` (generating → generation, dispatching → queue, else none) and progress into the widget. On click: Generate if generating, Queues if dispatching. The control stays visible on every workspace, including Images.
7. **Remove the toggle completely.**
   - `workspace_shell.ui`: item with `inspectorToggle` (about line 123).
   - `main_window.py`: the `generation_page.inspectorToggle` alias (about line 180); the `setIcon(panel_icon())` and size-policy retention (about lines 190-198); `_inspector_wanted` / `_inspector_auto` (about line 202); the `toggled` connection (about line 253); `_toggle_inspector` (about lines 298-301); the `setChecked` block in `_responsive_workspace` (about lines 305-310); the toggle visibility lines for Images (about lines 338-340), leaving `rightRail.setVisible(not images and not narrow)`.
   - Drop the `panel_icon` import and function if nothing else uses them.
   - `tests/unit/test_qt_generation.py`: remove or adjust the toggle reference.
   - Finish with `grep -rn "inspectorToggle\|_inspector_" src tests` returning nothing.
8. **Docs.** Update the toggle wording in `DESIGN.md` (shell, Generate layout, narrow-window behavior, Images chrome, the "Don't hide navigation with an optional inspector" note, reserved-space note) and `devdocs/design-system/readme.md`; add the run ring to the design tokens/components notes if applicable; update `current_dev_status.md` and `releases/unreleased.md`. Older dated plans stay as history.

## Acceptance

- No inspector toggle exists in the header, code, tests or current docs.
- The ring is about 30×30 px and does not grow toward logo scale.
- Idle: dim and disabled. Generating: lit with the current step when `completed/total` is reported. Dispatching: lit, number only when unambiguous.
- Click goes to Generate or Queues and never starts, pauses, resumes or cancels anything.
- The ring is visible and coherent on Connection, Generate, Queues and Images.
- The right inspector shows or hides purely by layout (workspace and the 1100 px breakpoint); Images header slots still line up after the toggle's removal.
- No backend, scheduler, persistence or billing behavior changes.

## Non-goals

- Single-generation pause/resume, or cancel from the header.
- Start/Pause/Resume from the header (possible later, see Decisions).
- Showing queue or item names or a text status strip in the header.
- Any manual way to reopen the inspector on narrow windows.
- Changing queue scheduling, concurrency or recovery.

## Validation

Documentation commit only. When implemented, validate only syntax, imports and UI-load for the changed paths unless the owner asks for more. The owner checks the ring at real header scale and the Images header alignment on Windows. No live provider call, inference, upload or other billable action is needed; use fake or offline state where sufficient.

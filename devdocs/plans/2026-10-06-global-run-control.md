# Plan: Global segmented Run control

## Goal

Replace the low-value right-panel show/hide button in the persistent DTFlow header with one compact global execution control. The approved visual direction is the **segmented ring** concept: a small circular/segmented indicator at approximately the current Inspector-toggle size, not a logo-sized control.

The control must remain useful while navigating between Connection, Generate, Queues and Images. It is a presentation/control surface over existing generation and queue services, not a second execution state machine.

## Current source evidence

- The shared header lives in `src/dtflow/ui/forms/workspace_shell.ui`; `inspectorToggle` is currently a 30×30 checkable button in the brand row.
- `MainWindow` owns the shared shell and currently wires `inspectorToggle` to responsive right-rail visibility.
- `Activity` currently exposes only the mutually-exclusive booleans `generating` and `dispatching`.
- Single generation already receives backend progress events with `completed` / `total` and renders them in `GenerationPage`.
- Queue control already uses the shared durable queue path: Start dispatches through `dispatch_queue`; Pause/Resume are posted through `post_queue_control`.
- Queue backend events, including progress detail where supplied, are recorded durably by the dispatcher.
- There is **no backend-neutral single-generation pause/resume contract today**. Single generation supports local stop/cancel, not pause. The new header control must not pretend otherwise.

## Approved interaction

The control stays in the same compact header area, but it is intentionally positioned slightly to the left of the current Inspector toggle rather than occupying its exact former coordinates.

### Queue ready

- Show the segmented ring in a quiet ready state with a **Play** glyph.
- Enabled only when the currently selected queue is actually startable according to the same queue-control rules already used by the Queues page.
- Click invokes the existing queue Start path. It must not duplicate dispatch logic.

### Queue running

- Show the segmented ring as active.
- The center displays the current generation step when trustworthy progress exists, for example `8`.
- Clicking posts the existing **Pause** control for that queue.
- The ring is a status/control affordance, not a second progress authority.

### Queue paused

- Stop active motion.
- Show a **Play/Resume** glyph in the center.
- Clicking posts the existing **Resume** control.

### Single generation running

- Show the same compact segmented visual language and current step when progress is available.
- This state is initially **status-only** because DTFlow has no truthful single-generation pause/resume service contract.
- Do not map a click to local cancel and label it Pause.
- A future backend-neutral pause contract can make this state interactive without redesigning the control.

### Idle / unavailable

- When no runnable queue is selected and no work is active, show a subdued disabled state.
- Do not imply that a queue can be started when the Queues page's own control logic would reject Start.

### Settled / failure feedback

- Completion and failure may use short-lived checked/error treatments, but should not turn the header into a persistent status banner.
- Detailed queue/generation messages remain on their existing pages and in tooltips/accessibility text.

## Visual direction

Use the existing DTFlow design tokens and shared QSS. Do not introduce an isolated mini-theme.

- Keep the control approximately **30×30 px**, matching the current Inspector-toggle footprint.
- Use a segmented ring rather than a generic large media-player button.
- The ring may rotate or advance subtly while work is active; motion must stay restrained.
- The center step number must remain legible at the real header size.
- Ready/paused use the same geometry so the control morphs by state rather than changing footprint.
- Use existing neutral, focus, selection, warning and error colors unless a missing semantic token is demonstrated.
- Preserve keyboard focus and accessible names/tooltips.
- Respect reduced-motion behavior where Qt/platform support permits; the state must remain understandable without animation.

## Architecture

### 1. Replace the Inspector toggle in the canonical Designer shell

Edit `workspace_shell.ui` rather than creating the header control ad hoc in Python.

- Remove `inspectorToggle` and add a stable presentation seam for the run control at the same compact size, positioned slightly farther left in the brand/header row rather than as a pixel-for-pixel replacement.
- Remove user-facing right-panel show/hide behavior.
- Keep the right inspector itself; it remains part of the normal workspace layout.
- Revisit narrow-window behavior separately: responsive layout may still hide the right rail automatically if required, but there is no manual header toggle.

### 2. Add one small handwritten Qt widget for the segmented ring

Create a focused custom widget, for example `RunControl`, whose responsibilities are only:

- render the segmented ring and center glyph/step text;
- expose a clicked signal;
- accept a plain presentation state;
- update tooltip/accessibility text.

It must not read persistence, call queue services, create backends or own generation/queue state.

Register the custom widget with the existing `QUiLoader` path, as already done for handwritten presentation widgets.

### 3. Extend shared activity presentation, not execution ownership

The current `Activity` object is the natural shared in-window notification seam, but it currently carries only two booleans.

Add the minimum backend-neutral presentation state needed by the header, such as:

- owner/kind: none, generation, queue;
- phase: ready, running, paused, settled/error as needed;
- progress `completed` / `total` when trustworthy;
- selected/active queue identity where needed for routing the click;
- change notification through the existing listener mechanism.

Do not move durable queue truth into `Activity`. Queue state remains authoritative in persistence/services; `Activity` is an in-window projection.

### 4. Feed single-generation progress into the shared projection

Reuse the same `BackendEvent` information already handled by `GenerationPage._event`.

- Publish current step/total while generating.
- Clear/settle it when the attempt finishes or is interrupted.
- Do not create a parallel progress calculation with different semantics.

### 5. Feed queue state through the Queue page's existing reads and controls

The Queue page already polls durable queue views and computes allowed controls.

- Publish selected queue ready/running/paused state from the same view/control calculation.
- Reuse `controls(...)` / existing Start/Pause/Resume eligibility rather than reimplementing policy in `MainWindow`.
- Route header clicks back to narrow QueuePage methods that invoke the same `start()` or `_post("pause"/"resume")` paths used by the page buttons.
- If the selected queue changes or becomes unavailable, the header follows that state on the next normal refresh.

For current-step display during queues, use only progress evidence DTFlow already records or exposes. If concurrent queue items make one unambiguous single step impossible, show activity without inventing a number. A later design can represent multiple active items if needed.

### 6. Keep navigation-independent chrome

The run control is part of the persistent brand/header chrome.

- It remains visible across Connection, Generate, Queues and Images.
- Images must no longer hide it merely because the former Inspector toggle was hidden there.
- Clicking it must not force navigation unless a later UX decision explicitly adds that behavior.

### 7. Remove obsolete Inspector-toggle wiring

After the replacement is functional:

- remove `inspectorToggle` aliases, icon setup and toggled wiring;
- remove `_inspector_wanted` / `_inspector_auto` state that existed only for manual visibility;
- simplify `_responsive_workspace()` so right-rail visibility is automatic/layout-driven rather than controlled by the removed button;
- update DESIGN and desktop documentation so they no longer advertise a manual Inspector toggle.

## Implementation sequence

1. Canonical Designer/QSS change: remove the 30×30 Inspector toggle and add the same-size run-control presentation seam slightly farther left in the header; preserve the rest of the header geometry.
2. Implement the segmented-ring widget with static presentation states first.
3. Wire generation progress into shared activity projection.
4. Wire selected queue ready/running/paused projection.
5. Route queue Ready → Start, Running → Pause, Paused → Resume through existing QueuePage behavior.
6. Remove obsolete Inspector-toggle code and reconcile responsive right-rail behavior.
7. Update `DESIGN.md`, desktop guide, current status and unreleased notes as appropriate.
8. Owner visual/runtime acceptance at actual header scale. No live provider inference is required merely to validate the control; use fake/offline state where sufficient.

## Acceptance

- The old right-panel show/hide header button is gone.
- The replacement remains approximately 30×30 px and is not enlarged to logo scale; it sits slightly left of the former Inspector-toggle position rather than exactly replacing its coordinates.
- A startable selected queue shows Play and starts through the existing queue dispatch path.
- A running queue shows the active segmented treatment; click posts Pause through the existing control path.
- A paused queue shows Resume/Play; click posts Resume through the existing control path.
- Generation progress can show the current step in the control when the backend reports trustworthy `completed/total`.
- Single generation does not claim pause/resume until a real service contract exists.
- The control stays visible and coherent while moving between all four workspaces.
- Right-inspector presentation remains available without the removed manual toggle.
- No new backend protocol, scheduler, persistence or billing behavior is introduced.
- No provider call, model inference, upload or other potentially billable action is required for implementation validation.

## Non-goals

- Adding single-generation pause/resume semantics.
- Cancelling work from the compact control.
- Showing queue/item names or a permanent text status strip in the header.
- Replacing the Queues page controls or detailed progress.
- Changing queue scheduling, concurrency or recovery semantics.
- Redesigning the header, navigation or brand.

## Validation policy for this PR

This PR starts with the plan only. Implementation follows in later commits after review.

Per the current working direction, do not add or run tests merely for the planning commit. When implementation begins, validate only the changed UI/source paths needed to establish syntax/import/UI-load correctness unless the owner asks for broader tests. Do not run live generation or other potentially billable actions.

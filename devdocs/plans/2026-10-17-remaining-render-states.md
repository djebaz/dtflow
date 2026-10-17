# Plan: remaining offline desktop render states

Date: 2026-10-17. PR: #87. Baseline: `1443effe561604d4503d069ea04d15dc01c6aaf9`. Status: first small implementation slice is source-present: Connection 01–05. Execution and visual acceptance remain pending; all other catalog additions remain planned.

## Scope

- In: extend `tools/ui/render_states.py` with dedicated offline screenshots of already implemented desktop states that are still absent, using the real forms, shared styles and existing presentation handlers. Complete the batches below in this same PR, updating this plan at each coherent checkpoint.
- Out: new product features, changes to provider protocols or persistence semantics, GUI redesign, new dependencies, packaging, native file-picker screenshots, live credential access, network traffic, real generation, uploads, ONNX execution, real Trash/permanent deletion, tests and local validation/render execution.
- The initial change wrote the plan and documentation links only. The owner subsequently requested the first small implementation slice: Connection 01–05. Checked catalog entries below mean source-present fixtures, not executed screenshots. No local execution/validation is authorized; the owner controls further implementation and CI/manual execution.

## GUI components affected by future fixtures

- [ ] Main window: Connection status/credentials, cache and catalogue/preset administration, shared export-card placement.
- [ ] Panels/widgets: Generate selectors/assets, populated Queues, Images Detail/export/selection, Inpaint presentation.
- [ ] Inline surfaces: confirmations, searchable/weight editors, operation overlays and inspection notices; no new dialogs.
- [ ] Assets: reuse existing icons, forms, QSS and tokens without changing them.
- [ ] Config files: temporary fixture state only; never use the owner's runtime state or settings.
- PyInstaller specifications and runtime dependency metadata are unaffected.

## Baseline and audit reconciliation

The owner-supplied `missing-states.md` describes PR #86 at `ed3eea6`; it is historical input, not the current checklist. Do not copy its global screenshot numbers or its private Downloads path into source/tooling. Current source and the tab-first filenames are authoritative.

| Group | Existing range | Coverage already present; do not add again |
| --- | --- | --- |
| Generate | `generate-01` through `generate-19` | Empty/results/aspects, hover/pressed/zoom/narrow, invalid inputs and incompatible LoRAs, busy/unknown/failed/stopped, expanded prompt, queue-busy presentation, preset-name editor, metadata inspection, cloud receipts/details. |
| Images | `images-01` through `images-52` | Grid/mapped List/Detail, removal modes/menus and permanent consent, Trash consent/progress/outcomes, removal failures/notices, refresh/snapshot recovery, stopped/failed/partial/completed selection, compact toolbar/menus, checked Detail and no selection. |
| Inpaint | `inpaint-01` through `inpaint-20` | Empty/source/mask/views, Clear consent, synthetic analysis/edit progress, capability-probe presentation, fixture result, model-readiness variants and found-person/hover presentation. |
| Queues | `queues-01` through `queues-10` | Empty, created-paused queue, dry-run plan, Cancel/Clear consent, finished/partial/cancelled result rows and dispatch ownership/error outcomes. |
| Shell | `shell-01` through `shell-14` | About, close consent/removal refusal, startup/reconciliation notices, simultaneous rows, notice during confirmation, informational result and basic run-control phases. |
| Connection | None | `shots.py` has a separate Connection screen capture, but that is not a listed workflow state in `render_states.py`. |

The baseline has 115 listed workflow renders. Existing screenshots can contain incidental controls or results; an incidental appearance is not a dedicated reproducible fixture. Conversely, reuse an existing fixture rather than adding another screenshot when it already intentionally shows the same state through the same presentation path.

## Fixture and naming decisions

- Append `<group>-<NN>-<state>.png` names; preserve all existing names and their order within each group. Connection begins at 01; Generate continues at 20, Queues at 11, Images at 53, Inpaint at 21 and Shell at 15. These are reserved proposed names, not files already produced. Reconcile concurrent additions before implementing a batch; do not overwrite another state's name.
- Export is one shared card/controller, not another tab. Put its authoritative operation-result fixtures under Connection; add Images fixtures only for the column/check-scope/navigation composition. Do not introduce an `export-*` group or duplicate the full export outcome matrix on both pages.
- Use real action/editor handlers when safe and local. For long-running outcomes, inject synthetic backend-neutral results at the real GUI settlement handlers or use a fixture runner that records submissions without executing them. Direct appearance fixtures must be labelled as such in source. Never bypass safety by enabling real actions against a real provider or original file.
- Use the existing network guard, fake vault, in-memory cloud transport and temporary directories. Supply synthetic non-secret credential states; never query Windows Credential Manager or read a real environment API key for a render.
- Every fixture establishes its own prerequisites, opens the correct tab and waits only for its known asynchronous fixture work. Restore temporary selections, patched handlers, busy flags, editors, modes and panel placement afterward. A queue fixture must not leave a runnable queue or an uncertain attempt behind for a later state.
- Before a confirmation/editor capture, ensure it is actually armed/mounted; before a checked/disabled/result capture, establish that condition explicitly. Do not assume clicking a disabled button or selecting a Detail image creates consent/check state.
- Capture timed results before expiry; do not add six-second waits per outcome. Keep the existing inactivity stack-dump watchdog and progress reporting. The two-minute CI render-step limit stays unchanged. The intermittent stall after `images-01-grid` is unresolved and remains a separate diagnostic issue, not something this plan claims to fix.

## Action items

- [x] Reconcile the historical audit with the 115-state source baseline and remove already-covered entries from the remaining catalog.
- [x] Define named batches, safe fixture seams and excluded/deferred cases below.
- [x] Implement A1 only: Connection 01–05, using one isolated temporary shell, a private synthetic environment mapping and an in-memory vault. Unused Generate inventory restoration is suppressed within this helper so a Cloud selection cannot consult process-environment credentials. No Check or credential action button is clicked; storage unavailability is injected at the saved-store boundary.
- [ ] Implement A: Connection and the shared export flow.
- [ ] Implement B: Generate editors, inventory/configuration and asset outcomes.
- [ ] Implement C: active/problem Queues and planner composition.
- [ ] Implement D: Images column, Detail and remaining operation/selection composition.
- [ ] Implement E: remaining Inpaint capability, consent and outcome states.
- [ ] Implement F: protected editor and overlay lifetime presentation.
- [ ] Update this plan, tool guide and unreleased notes at each batch checkpoint; record only actually implemented names and separately supplied execution evidence.

## Stall-prevention hints and implementation methods

These are requirements for future fixture implementation and a diagnostic playbook. They have not been executed or shown to eliminate the existing intermittent stall. Keep product behavior unchanged; a renderer workaround must not conceal a real GUI-thread or lifecycle defect.

### 1. Build states without unnecessary work

- Prefer a small immutable inventory/gallery/queue fixture and synthetic operation settlements over generating more fake outputs, requerying persistence or dispatching work for every screenshot. Reuse committed temporary fixture records within a batch, but explicitly reset presentation state between captures.
- Open a local/cloud presentation only after the fixture transport, fake credentials and non-executing submission seams are installed. A backend selector change can schedule inventory or receipt work; intercept that work before changing the selector, not after it starts.
- For collecting/running/stopping/result renders, use the existing controller's presentation/settlement path with bounded fixture values. Do not start a gated operation just to leave it waiting indefinitely. If a gated worker is genuinely needed, retain its future and release/cancel it in guaranteed cleanup.
- Avoid duplicate transitions. For example, current `GalleryPage.show_view("Detail")` already selects/presents an available image; a following `gallery.select(...)` may repeat work. Choose one deliberate selection path, or use `open_detail=False` when appropriate, after reading the current implementation. Removing a redundant call is an optimization candidate, not evidence of the stall's root cause.
- Keep images, metadata strings, model lists and queue tables representative but small. Use existing prepared fixture pixels; do not introduce large repeated image conversions, model hashing or per-row compression merely to obtain a presentation frame.

### 2. Make asynchronous prerequisites explicit

- Wait on the particular operation needed by the state: gallery query completion, selected identity, image/metadata delivery, thumbnail work, or an armed editor. Do not rely on a fixed sleep, a button being enabled, or a global queue being momentarily empty as proof that every required callback has settled.
- Distinguish selection from image readiness. Record the selected identity and the matching image/detail request generation before capturing; an older callback for a previous identity must not satisfy the next state's readiness condition.
- Keep every polling wait bounded and identify its failed prerequisite in the error text. Include state name and sanitized readiness values such as loading flag, pending thumbnail count and selected identity; do not dump owner paths, secrets or raw payloads.
- A deadline inside `wait_for` only runs when control returns to its loop. It cannot rescue a synchronous Qt call or callback that never returns. Do not describe a polling timeout as a process-level watchdog or assume that adding another timeout around the predicate fixes an event-loop hang.
- Do not increase global wait limits as the first response to a failure. Determine whether work is slow, callbacks are stale, an operation is waiting on a lease, or the GUI thread is stuck before changing a wait budget.

### 3. Prevent refresh and callback interference

- For a static layout batch, deliberately disable Images auto-refresh through its existing control before freezing synthetic data, and restore the prior setting in cleanup. Use separate fixtures for auto-refresh/recovery behavior; do not globally suppress polling in states intended to demonstrate it.
- Keep a fixture's query result, selected identity, thumbnail/image source and metadata mutually consistent. Do not patch a query to return one identity while allowing a pending callback from a previous state to replace it.
- Retire or settle only the fixture's own pending operations before reusing a shell. Use existing controller cancellation/revision mechanisms rather than arbitrary flag changes that leave a worker alive or permit stale GUI delivery.
- Dismiss temporary editors/confirmations and hide popup menus before navigation or shell disposal. Preserve real ownership rules: hiding an operation overlay must not masquerade as cancelling its task.
- Keep native objects and deferred callbacks parented to the fixture's live window. Avoid accumulating detached receivers, timers, menus or windows across batches. Destroy a shell only after its runner shutdown protocol has begun; do not manually pump callbacks against already-destroyed widgets.

### 4. Show exactly which step stopped returning

- If a stall recurs, add short flushed `BEGIN`/`END` markers with state name and monotonic elapsed time around the narrow suspect steps, not every event in the application. Capture transitions and image saving separately so a last completed screenshot is not mistaken for the blocking call itself.
- For the known gap, bracket: the settled-gallery wait, `show_view("Detail")`, explicit selection if retained, Additional details expansion, event settling, widget grab, PNG save and progress reporting. The next frame is `images-28-detail-additional`; the last saved frame `images-01-grid` is only the boundary, not proof that Detail selection caused the hang.
- Preserve the repeating 30-second all-thread stack dump, reset only on a saved-state progress event, and keep output unbuffered in CI. Python exceptions should still print before teardown. No exception does not mean no stall: a call that never returns cannot reach the exception handler.
- Add equally bounded phase markers around runner shutdown and window close if stacks point to teardown. A failure during setup must not disappear behind an unrelated cleanup wait.
- Keep the two-minute CI limit. Its current external 300-second abort watchdog cannot provide a pre-timeout trace because the step ends first; rely on the earlier in-process diagnostic rather than increasing either limit to conceal the problem.

### 5. Read the stack before choosing a fix

| Evidence from the next failing CI run | Implementation response to investigate |
| --- | --- |
| GUI thread inside Detail selection/layout or a resize callback | Trace re-entrant signal/layout transitions; remove redundant fixture transitions or correct the specific callback cycle. Do not patch out Detail rendering. |
| GUI thread processing events repeatedly while readiness never settles | Identify the unfinished operation/identity/revision and competing refresh; make the fixture deterministic and use the correct readiness condition. |
| Worker waiting for persistence/cache/original-file ownership | Identify the fixture operation still holding the lease, release/settle it correctly, and avoid starting another unrelated operation before capture. Never delete lock files or weaken production leases. |
| Worker stuck in fake-backend gate or cancellation | Release/cancel that fixture-owned gate/future and let the real settlement callback run; retain the stopped/unknown semantics being demonstrated. |
| GUI thread waiting for worker shutdown | Find the uncooperative task and fix its fixture teardown; do not wait for it on the GUI thread indefinitely or silently abandon a live worker as a successful render. |
| GUI thread in image decode/grab/save | Bound fixture dimensions and inspect the exact image/widget and pending layout state. Do not infer a compression or disk problem from elapsed time alone. |
| No Python stack explains a native Qt wait | Keep the phase markers and report the native-boundary limitation. Native stack inspection is a separate owner-authorized follow-up, not permission to guess a runtime fix. |

### 6. Preserve useful evidence and the time budget

- Add a batch at a time, not the entire catalog in one unreviewable renderer change. Keep the plan's implemented rows synchronized with source and any owner/CI evidence.
- When the owner requests CI execution, compare per-state gaps with the preceding baseline and distinguish setup, rendering and teardown. No timing or speedup claim is made from source inspection alone.
- Do not solve a long batch by dropping states, swallowing failures, clearing busy flags, force-enabling disabled controls or reporting an existing/stale PNG as newly rendered. Keep outputs for the current run separate from historical filenames.
- If CI intermittently passes, retain the diagnostics and record the pass as a non-reproduction, not as a verified fix. If the same state stalls, the next implementation checkpoint must explain the stack and the specific changed variable.
- No local reproduction, test, render, benchmark, lint or other validation command is authorized by this plan. Any such execution remains with the owner or explicitly requested CI activity.

## A. Connection and shared export

Sources: `src/dtflow/ui/main_window.py` (`_selection_changed`, `_present`, `refresh_credentials`, `_checked`, `remove_key`, preset/custom-LoRA/cache handlers), `image_export_panel.py` (`_loaded`, `_set_running`, `_progress`, `cancel`, `_done`) and canonical `main_window.ui`.

Use injected status/credential observations and temporary library/cache summaries. Block the real Check, vault mutation, import/clear/purge and export workers when driving action presentation; do not inspect or clear an actual cache. For a busy state, hold the presentation busy without starting an operation. Populate the real shared export card and deliver synthetic export results; do not replace its controller with a second mock UI.

| Done | Proposed state | Required visible state |
| --- | --- | --- |
| [x] | `connection-01-local-idle` | Local endpoint controls, unchecked connection status and default nav indicator. |
| [x] | `connection-02-cloud-no-key` | Cloud controls, no credential and correctly disabled Remove. |
| [x] | `connection-03-cloud-env-key` | Synthetic environment credential source, without exposing key text. |
| [x] | `connection-04-cloud-saved-key` | Synthetic saved credential source and enabled Remove. |
| [x] | `connection-05-storage-unavailable` | Secure-storage-unavailable status and safe control availability. |
| [ ] | `connection-06-checking` | Checking status/nav indication with Check disabled; no request submitted. |
| [ ] | `connection-07-connected` | Successful status card and nav indicator from a synthetic check result. |
| [ ] | `connection-08-check-failed` | Failed status card and nav indicator from a synthetic refusal. |
| [ ] | `connection-09-remove-key-confirmation` | Mint confirmation on Remove and Cancel in the overlay; no vault write. |
| [ ] | `connection-10-key-save-failed` | Credential-save failure presentation. |
| [ ] | `connection-11-key-remove-failed` | Credential-remove failure presentation. |
| [ ] | `connection-12-replace-presets-confirmation` | Existing-library replacement consent, using a temporary selected JSON path. |
| [ ] | `connection-13-presets-unreadable` | Unreadable preset-library presentation. |
| [ ] | `connection-14-preset-import-failed` | Import failure notice, no library replacement. |
| [ ] | `connection-15-preset-export-failed` | Export failure notice, no destination write. |
| [ ] | `connection-16-clear-custom-loras-confirmation` | Danger confirmation with populated synthetic custom catalogue. |
| [ ] | `connection-17-custom-lora-import-failed` | Invalid custom-LoRA import presentation. |
| [ ] | `connection-18-custom-lora-clear-failed` | Custom catalogue clear failure presentation. |
| [ ] | `connection-19-cache-measuring` | Measuring label with the applicable controls busy. |
| [ ] | `connection-20-cache-measured` | Distinct bounded category counts/sizes from a synthetic usage result. |
| [ ] | `connection-21-cache-unavailable` | Measurement-unavailable label/error. |
| [ ] | `connection-22-cache-clear-confirmation` | Danger confirmation naming selected disposable categories. |
| [ ] | `connection-23-cache-busy` | Busy refusal; no purge worker. |
| [ ] | `connection-24-cache-clearing` | Real cache-operation overlay presentation with a non-executing worker seam. |
| [ ] | `connection-25-cache-cleared` | Successful category-clear result. |
| [ ] | `connection-26-cache-partial` | Partial clear result with retained busy/failed categories if that outcome has distinct presentation; otherwise document reuse of the real warning handler. |
| [ ] | `connection-27-cache-clear-failed` | Clear failure result, without deleting anything. |
| [ ] | `connection-28-export-idle` | Destination, scope combo, privacy/sidecar/routing choices and Start in the Connection card. |
| [ ] | `connection-29-export-collecting` | Inputs disabled, Cancel available and indeterminate collecting presentation. |
| [ ] | `connection-30-export-progress` | Named export overlay with a known completed/total count. |
| [ ] | `connection-31-export-stopping` | Stop-after-current text and disabled Cancel. |
| [ ] | `connection-32-export-success` | Exported/skipped counts, noise/blank advisory routing and enabled Open folder; do not launch it. |
| [ ] | `connection-33-export-partial` | Partial result containing genuine failure/warning evidence, not informational notices alone. |
| [ ] | `connection-34-export-stopped` | Stopped result preserving completed export counts. |
| [ ] | `connection-35-export-failed` | Persistent failure presentation. |
| [ ] | `connection-36-export-not-started` | Invalid/missing destination refusal through the real validation presentation. |
| [ ] | `connection-37-export-no-matches` | Empty-scope result rather than a fabricated success count. |
| [ ] | `connection-38-export-preferences-unavailable` | Saved-preference read failure/defaults message. |
| [ ] | `connection-39-narrow` | Connection below the inspector-collapse breakpoint, with navigation still accessible. |

## B. Generate editors, inventory and assets

Sources: `generation_page.py` (catalogue/inventory, configuration and generation settlement handlers), `search_select.py`, `lora_panel.py` and `inline_feedback.py`. Load small synthetic inventory/catalogue fixtures; do not infer checkpoint/LoRA compatibility. Select fake/local/cloud presentation deliberately, with provider and upload workers intercepted.

| Done | Proposed state | Required visible state |
| --- | --- | --- |
| [ ] | `generate-20-local-controls` | Local-only Include DT+ Cloud control visible and fake Simulation absent. |
| [ ] | `generate-21-checkpoint-editor` | Searchable checkpoint editor, active selection and scrim. |
| [ ] | `generate-22-checkpoint-no-matches` | Search with no matching candidates and the existing custom-identifier affordance. |
| [ ] | `generate-23-custom-checkpoint-editor` | Custom identifier input through the existing chooser, no invented family metadata. |
| [ ] | `generate-24-lora-weight-editor` | Combined searchable LoRA/weight editor, Add/Cancel and scrim. |
| [ ] | `generate-25-lora-weight-hint` | Existing out-of-range catalogue-weight explanation, without clamping saved values. |
| [ ] | `generate-26-lora-not-added` | Real rejected-selection presentation. |
| [ ] | `generate-27-unclassified-models` | Existing unclassified-files informational inspection. |
| [ ] | `generate-28-inventory-refreshing` | Refreshing label and appropriate disabled controls. |
| [ ] | `generate-29-inventory-refresh-failed` | Refresh failure while retaining last-observed counts/timestamp. |
| [ ] | `generate-30-inventory-cache-only` | Restored observation/cache-only provenance, not a claimed live refresh. |
| [ ] | `generate-31-config-loaded` | Loaded configuration and its existing warning/summary presentation. |
| [ ] | `generate-32-config-load-failed` | Configuration refused/unreadable with current form preserved. |
| [ ] | `generate-33-metadata-unavailable` | Missing metadata inspection/result, complementing existing successful inspection. |
| [ ] | `generate-34-partial-result` | Amber partial-generation result from a synthetic settlement, without additional generation. |
| [ ] | `generate-35-asset-preparing` | Preparing status and operation overlay; never call a real upload. |
| [ ] | `generate-36-asset-partial` | Non-confirmed receipt outcome through `_prepared`. Reuse existing successful receipt coverage rather than adding a duplicate. |
| [ ] | `generate-37-asset-failed` | Preparation failure through the real failure presenter. |
| [ ] | `generate-38-asset-registry-unavailable` | Local receipt-list failure. |
| [ ] | `generate-39-asset-reconciling` | Metadata-check status with the worker suppressed. |
| [ ] | `generate-40-asset-receipt-bound` | Existing bound-receipt status for a selected synthetic LoRA. |

## C. Active/problem Queues

Sources: `queue_page.py`, `queue_model.py`, `queue_presenter.py`, `planning_presenter.py`, `run_control.py` and `queue_page.ui`. Use one internally consistent synthetic queue view with real item identities and known counts. Deliver it through the controller's view/presentation path; do not create a competing dispatcher or submit generation to obtain a running state. Existing result overlays do not replace these table/control/Inspector fixtures.

| Done | Proposed state | Required visible state |
| --- | --- | --- |
| [ ] | `queues-11-running` | Local-owned running queue, settled count, active items and Pause availability. |
| [ ] | `queues-12-paused-after-start` | Paused local dispatch with Resume available, distinct from never-started paused. |
| [ ] | `queues-13-foreign-running` | Queue/table owned elsewhere and status-only global run control. |
| [ ] | `queues-14-blocked-unknown` | Unknown item/reservation evidence and enabled Acknowledge/Override action. |
| [ ] | `queues-15-override-confirmation` | Existing fresh reservation acknowledgement consent; no service mutation. |
| [ ] | `queues-16-item-states` | Representative succeeded, failed, unknown, save_failed, cancelled and running row styling in one coherent fixture. |
| [ ] | `queues-17-item-inspection` | Selected failed/unknown item and its recorded attempt/request evidence. |
| [ ] | `queues-18-plan-invalid` | Real planner field marking/error rather than only a successful dry run. |
| [ ] | `queues-19-inpaint-plan` | Inpaint Strengths fields, Edit column and upload/credit disclosure with staged synthetic input references. |
| [ ] | `queues-20-concurrency-refused` | Concurrency control styling and existing not-changed presentation. |
| [ ] | `queues-21-finished-table` | Finished queue summary/table/control availability, not just the result overlay. |
| [ ] | `queues-22-cancelled-table` | Cancelled queue summary/table/control availability. |
| [ ] | `queues-23-narrow` | Queues below the inspector-collapse breakpoint. |

## D. Images composition and remaining Detail/removal states

Sources: `gallery_page.py`, `gallery_detail_preview.py`, `image_recycle_panel.py`, `main_window.py` export-panel mounting and the canonical gallery forms. Reuse the export controller from A and existing selection/removal fixtures. Never submit an original-file mutation merely to obtain its progress/result UI.

| Done | Proposed state | Required visible state |
| --- | --- | --- |
| [ ] | `images-53-export-panel-empty-scope` | Open export column with no checks; toggle stays available and opening submits nothing. |
| [ ] | `images-54-export-panel-checked` | Same card beside the listing, checked scope, no Connection scope combo. |
| [ ] | `images-55-export-panel-running` | In-progress shared card in the Images column with frozen checked scope. |
| [ ] | `images-56-detail-raw-metadata` | Raw JSON panel open with safe synthetic metadata. |
| [ ] | `images-57-detail-fullscreen` | Real viewer focus/fullscreen presentation, without fabricating another desktop window. |
| [ ] | `images-58-detail-file-missing` | Detail missing-original placeholder, distinct from existing Grid/List missing cards. |
| [ ] | `images-59-detail-unavailable` | Detail inaccessible/unverifiable-original placeholder. |
| [ ] | `images-60-detail-edited-image` | Recorded image-operation output with Use settings/Regenerate disabled and truthful tooltips. |
| [ ] | `images-61-detail-no-model` | Missing recorded model/provenance and resulting action availability. |
| [ ] | `images-62-detail-loras` | Multiple recorded LoRAs and truthful unknown status chips. |
| [ ] | `images-63-detail-zoom-native` | Actual Detail zoom/100% presentation, not Generate's existing zoom. |
| [ ] | `images-64-filter-no-matches` | Active search/filter controls and empty-results state. |
| [ ] | `images-65-selection-scanning` | Selection scan in progress with Cancel selection; no identity-query worker needed. |
| [ ] | `images-66-multiple-checked` | Multiple checked cards/rows and partial header indicator, not just one checked Detail. |
| [ ] | `images-67-permanent-delete-progress` | Permanent Delete progress wording/icon rather than relabelling the existing Trash result. |
| [ ] | `images-68-permanent-delete-result` | Synthetic permanent-removal result through the real mode-specific presenter. Reuse outcome severity coverage already present for Trash. |
| [ ] | `images-69-removal-mode-save-failed` | Existing mode-preference-save warning, distinct from initial preference-read failure. |
| [ ] | `images-70-snapshot-list` | List view under read-only snapshot fallback, with the existing persistent status row. |
| [ ] | `images-71-detail-pagination` | Multi-page fixture with meaningful Previous/Next availability and truthful position/count. |

## E. Remaining Inpaint states

Sources: `inpaint_editor.py` (`_render_support`, `_discard_then`, `_task`, `_people_status`, `_edit_job`, `_dispatch`, `_controls`) and generation image-operation composition. Reuse the existing normalized source/mask and synthetic analysis. Do not load model weights, run ONNX, acquire a real permit or submit an edit; a fixture of consent/capability is not proof of backend support.

| Done | Proposed state | Required visible state |
| --- | --- | --- |
| [ ] | `inpaint-21-capability-supported` | Exact synthetic model/backend capability is supported, with effective settings visible. |
| [ ] | `inpaint-22-capability-unsupported` | Explicit unsupported reason and disabled edit action. |
| [ ] | `inpaint-23-capability-unavailable` | Capability check failed/unknown rather than unsupported. |
| [ ] | `inpaint-24-advanced-expanded` | Existing advanced controls expanded with meaningful Grow/Feather/Keep-outside values. |
| [ ] | `inpaint-25-discard-source-confirmation` | Dirty-mask/history source-replacement consent; no source replaced during capture. |
| [ ] | `inpaint-26-discard-mode-confirmation` | Leaving Inpaint with dirty work through the existing mode-change consent. |
| [ ] | `inpaint-27-no-people` | Successful synthetic analysis with no detected people. |
| [ ] | `inpaint-28-person-only` | Person-only availability without clothes/part model support. |
| [ ] | `inpaint-29-analysis-stopping` | Stop requested while a synthetic analysis task is active. |
| [ ] | `inpaint-30-analysis-stopped` | Stopped outcome using existing task presentation. |
| [ ] | `inpaint-31-analysis-failed` | Analysis failure without invoking a model. |
| [ ] | `inpaint-32-disabled-during-dispatch` | Editor controls disabled by queue/generation ownership. |
| [ ] | `inpaint-33-use-result-enabled` | Existing fixture result with Use result as source available; show its discard consent if dirty. |
| [ ] | `inpaint-34-mask-load-failed` | Invalid mask-file outcome via a temporary fixture path, no native picker shown. |
| [ ] | `inpaint-35-mask-save-failed` | Mask-save error presentation with no owner file touched. |

Before adding a separately named edit-dispatch consent image, locate the current consent owner: do not invent an extra confirmation in Inpaint. If an existing inline request/permit surface is reachable in current source, name and add its fixture here; if no such surface exists, record that fact and keep it out of the coverage count. The previous broad audit's wording alone is not implementation evidence.

## F. Protected editor and overlay lifetime

Sources: `inline_feedback.py`, `search_select.py` and `activity_strip.py`. Use the real shared overlay and one of B's existing searchable/weight editors. Existing notice-during-confirmation coverage is not notice-during-input-editor coverage.

| Done | Proposed state | Required visible state |
| --- | --- | --- |
| [ ] | `shell-15-notice-during-editor` | Unrelated labelled notice coexisting with a mounted editor, its scrim, typed text and Add/Cancel intact. |
| [ ] | `shell-16-success-lifetime-paused` | Success draining line paused by overlay hover/focus, distinct from the blue task-progress bar. |
| [ ] | `shell-17-warning-lifetime-paused` | Amber result lifetime paused, preserving warning evidence. |
| [ ] | `shell-18-long-inspection` | Persistent long inspection wrapping/scrolling without covering essential controls or losing dismissal. |

## Deferred or represented without another workflow screenshot

- Native OS file/folder/model picker windows: outside the in-window render contract; inject their selections or failures.
- Exhaustive hover/pressed/disabled combinations of every ordinary widget: component-sheet tooling already has a separate namespace; this work prioritizes application states, not a Cartesian style matrix.
- Every filter/sort/model/LoRA/credential/error-string permutation: use representative distinct layouts/semantics above; do not add one screenshot per internal branch when the same presenter produces the same visible state.
- Destructive execution, real export copying, connection checks, uploads, inference and OS Trash behavior: outside these fixtures. Synthetic UI outcomes must never be described as live acceptance.
- Separate after-Remove-source image if identical to `inpaint-01-empty`, separate successful asset-receipt image if already shown by `generate-12-assets-details`, and duplicated close/About/basic run-control/Trash/selection-settlement captures: retain existing authoritative fixtures.
- Interim generation preview, Apply suggestion, development-mode error detail and other uncertain incidental appearances from the old audit: inspect the baseline frame setup before allocating new names. Add only when current source has a distinct user-visible state that the listed Generate/result fixtures do not already deliberately cover; record the decision here rather than silently treating it as covered.

## Completion and evidence recording

- A catalog row becomes implemented only after the script explicitly saves that named state with its stated prerequisites and safe fixture seam. Source presence, a checked plan row and a generated PNG are separate facts.
- Record any merged/reused/deferred rows and their replacement filename in this plan. A planned number is not a required screenshot-count target.
- Keep progress/state timings and inactivity stack diagnostics enabled. Do not hide a stall with longer CI timeouts, arbitrary sleeps, swallowed exceptions or skipped states. If additional states exhaust the current budget, report timings to the owner before proposing execution/workflow changes.
- Screenshots, when separately run, are offscreen layout evidence only. They do not establish native Windows/DPI, real-provider, ONNX, destructive-operation or packaged acceptance.

## Open questions

- No blocking questions for the scoped fixture batches. Reachability/duplicate checks called out above are implementation discovery tasks, not permission to change product behavior.

## Validation and ownership

- Current A1 slice: source editing/review only. No tests, lint, format checks, syntax checks, rendering, builds, training/inference or provider operations are run by the agent under the requested fast workflow.
- [x] Owner: requested the first small implementation group, scoped to Connection 01–05. Remaining batches still require a separate implementation request.
- [ ] Owner/CI: execute the explicitly requested offline render batches and inspect the saved states/timings, including the known intermittent Detail-transition stall.
- [ ] Owner: manual native Windows/DPI interaction acceptance where needed; no executable build is required by this fixture-only scope.

## Progress log

- 2026-10-17: plan written against `1443eff`, with current baseline reconciliation, append-only state reservations, safe handler/fixture boundaries, duplicate/deferred decisions and owner-only execution gates. Implementation has not started.
- 2026-10-17: added stall-prevention methods, narrow transition markers, readiness/lifecycle guidance and an evidence-to-action triage table. This is documentation only; no stall fix or runtime result is claimed.
- 2026-10-17: implemented A1 (Connection 01–05) in the renderer source. The five additions bring the source-listed total from 115 to 120; no new screenshots were produced locally. One temporary shell is reused only within this credential/layout batch and is shut down before the existing fixtures continue. The source uses real backend-selection/credential presentation and an injected in-memory vault; no connection check, generation, upload or real credential access was performed. No tests, lint, format checks, syntax checks or renders were run.

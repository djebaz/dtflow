# Unreleased

Changes through 5.4.0 are consolidated in [DTFlow 5.4.0](v5.4.0.md). This file tracks subsequent changes, not completion of open acceptance issues.

## Highlights

- **Local DT+ completed-image counter (#221):** Connection shows a compact card with one count per DT+ API key of final images saved on the selected calendar day, and the bottom bar shows an `active/all` pill (active key's count / all keys' count) on every view. The day is local midnight to midnight in a selectable timezone (one entry per whole GMT offset from -12 to +14 with its current local time and largest city; default Europe/Paris with daylight-saving handling), recomputed locally from saved UTC times. Colors are visual guidance from the owner's working 400/500 assumption (green to 400, amber 401-499, red 500+); the counts are an estimate from saved outputs, not a DT+ balance, confirmed quota or remaining allowance, and nothing blocks generation. Each saved cloud attempt records an opaque key identity (execution state schema 7); older attempts are reported as unattributed. See [Connect](../guides/desktop.md#local-dt-completed-image-counter).

- **Parallel Scripts (PR #218):** Optional DTFlow-only `pipeline.runBatch` runs bounded independent text-to-image jobs with shared queue capacity, durable child attempts and ordered per-job outputs. The portable Krea2 mixer exposes 1..10 requested jobs at once (default 4), with its Draw Things sequential fallback retained. Effective concurrency and model/LoRA support remain backend-dependent; this is not unchanged iOS-script parity or provider entitlement.

- **Scripts forms (PR #217, merged):** Native nested/repeated forms and Inspect > Setup > Run Script preloading without a pre-run queue/provider session, version-specific answer suggestions and owned native picker inputs. `image` retains supplied-image selection semantics; `imageField`/`directory` use pickers, with optional script-authored cropping of owned copies. This does not add canvas pixel/mask generation support.

- **Scripts (PR #215):** The managed version library and parser are connected to the native Scripts page. Explicit Start creates one ordinary queue item with durable child attempts, live status/history and per-step results; Queues shows a script summary and child steps, with the last committed step as its summary thumbnail. Short activity labels and reduced History repainting are included. Geometry-only canvas hooks are available for text-to-image scripts; image pixels, masks, source handoff, complete interactive forms and broad configuration/LoRA-mixer compatibility remain later work. Manual Windows/UI smoke for this PR and exact backend/checkpoint acceptance are not established by the skipped UI-render Action.

## Added

- UI render uploads now default to individual images rather than ZIP, including automatic/manual Actions and the uploader CLI. ZIP remains an explicit option; upload concurrency and retry behavior are unchanged.

- Automatic UI render Actions skip draft PRs, including render-label requests, matching CI; marking ready re-evaluates ordinary eligibility. Manual dispatch remains an explicit override.

- Developer UI render selection now uses 30 file-level component rules, small populated page packs and an eight-state shared fallback, with prominent incomplete-coverage warnings instead of automatic exhaustive fallback. The `render-all` label independently triggers exhaustive rendering; summaries separate planned screenshots from fixture setup and list selected packs/states. Automatic sub-file diff analysis is intentionally excluded; owner visual review remains separate. No application generation behavior changes.
- CI and render Actions combine native path filters with an early check inside each existing job: entirely docs-only PRs allocate no runner, and docs-only follow-up pushes skip expensive steps using the previous-to-current head diff. No separate detector runner jobs remain. Resources, forms, fixtures, mappings and dependency/configuration changes remain eligible. Manual runs and explicit render labels override filtering; the label route reuses the render workflow without a dispatcher runner. Shared cancellation is removed to protect useful running/pending work; follow-up detection still consumes short runner time and is not a fresh validation pass.

- Scripts library cards now have a compact top-right Remove button with two distinct clicks to confirm in place, without a confirmation overlay. Removal affects the selected saved version’s library membership only; queued source bytes, run history and outputs are retained.
- The 14 px script Remove control has a padded 22 px click target, including while confirming.

- PR #219: portable Krea2 mixer authoring with cached, paged LoRA discovery, explicit readiness/skipped reasons, single Setup form with direct Run Script, versioned source-scoped preferences and deterministic bounded manual chunks. Plan IDs/global indices stay with child request evidence. Missing files are not downloaded/uploaded, unsupported preset fields are not silently mapped, and original Draw Things behavior remains separate. Source only; owner acceptance remains tracked by #216.

## Changed

- The Images grid position field in the status bar (`101/342`) is a technical benchmark/diagnostic field and now appears only in the explicit `-x/--dev` mode.
- Images grid selection checkboxes retain their appearance with a 56 × 56 px click target, twice their 28 px housing size. List and Detail controls are unchanged.

## Fixed

- PR #219 follow-up: remove the mixer preview wizard; keep one Setup form and Run Script. Unknown LoRA metadata is advisory, inspection warnings and footer text are shorter. Parser resource failures are distinguished from source syntax errors; parsing runs on a reserved native stack without changing the pinned parser or execution limits. Source-only changes, not runtime acceptance.

- PR #218 lifecycle follow-up: batch workers recheck closed intake after reservation/linking awaits, and retain/drain all workers before propagating release failures or batch cancellation. Already admitted work is not retried or cancelled remotely. CI at runtime head `0465f3d` reports 697 passed, 3 skipped with format/lint passed ([run](https://github.com/djebaz/dtflow/actions/runs/41613970580)); UI rendering was skipped, and owner/backend/package acceptance remains separate.

- PR #218: cloud scripts automatically bind existing confirmed LoRA receipts independently of Generate's selection, like queue LoRA sets. Setup reads local records only; actual dispatch still checks the active backend/account. Missing files still need explicit Prepare; no implicit downloads or uploads. Owner smoke of the full asset/account matrix remains separate from CI.

- PR #201 independent-review follow-up: offline terminal/review actions, guarded Start/queue/input previews, fixture syntax checks and in-memory version Save, live/unresolved history cues, detached Inpaint source/mask entry and local form/editor/results polish. Native Photos/Files pickers are supported in the target design, with runtime hook integration deferred. No real script execution, parser, persistence or provider calls are connected.

- PR #201 Scripts desktop preview: Designer-owned static views, visible library actions, wrapped form rows, distinct queued/cancelled evidence and Cancel/waiting presentation, selected-script inspection facts, saved-versus-draft status, Find/Replace and inline dirty-draft protection. The labelled Inspector toggle sits at the right edge of the Scripts toolbar with a right-sidebar SVG, not beside the product logo. Inspector drawer and offline source/mask fixtures remain presentation-only; runtime Start/save/history integration is not included.


## Scripting (PR #199, merged foundation)

- Added opt-in headless execution for locally supplied Draw Things JavaScript scripts with an explicit fake/local/cloud backend, bounded run generations, durable ScriptRun/child attempt links, headless form answers and per-run sandbox files.
- Unsupported changed configuration fields and masks fail before generation; empty model identifiers are rejected. Missing/unreadable script paths report their concrete file error.
- Owner Windows source smoke verified two fake generations and empty-model rejection (2026-11-02). Native UI, Tier B/detection, local/cloud acceptance and packaged execution are not included. No automated tests added or run by owner direction.

## Release audit

- PRs: #193, #199, #201, #215, #217, #218, #219, #227.
- Scope: Version bump 5.4.0 and release-note consolidation; Scripts preview corrections, canonical Designer migration, headless/native queue runtime and full form/Setup integration; bounded parallel scripts with attempts schema 6, ready LoRA binding and lifecycle repairs; portable mixer inventory/preparation preview, independent schema-1 authoring preferences and explicit chunks. Exact Windows artifact, provider compatibility and release publication remain separate gates.

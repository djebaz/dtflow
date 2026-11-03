# Plan: complete script forms (#196)

## Scope
- In: all pinned upstream requestFromUser components, nested ordered answers, repeated live forms, native pickers with owned input snapshots, bounded validation and version-specific answer recall.
- Out: new generation/configuration support, canvas pixel hooks, remote image fetching, script HTML/JS rendering, automatic dispatch/replay, automated tests and test execution. The subsequently requested standalone manual smoke script is included, unexecuted.

## GUI components affected
- Scripts waiting-input region and data-driven native field controls; reuse the existing Designer host and shared runner.
- No theme, packaging, dependency or application geometry changes.

## Action items
- [x] Inspect issue, current bridge, repository contract and pinned upstream descriptors.
- [x] Implement shared descriptor/answer validation and nested section ordering.
- [x] Implement full native controls, safe text/image rendering and explicit file selection.
- [x] Snapshot selected inputs through existing durable input ownership; never expose external picker paths to JS.
- [x] Persist/reload answers for the exact source version and matching form; require explicit submission every time.
- [x] Bind repeated live prompts with stable run/ordinal identity and stale-result protection.
- [x] Review the source diff without executing project code or checks.
- [x] Commit/push and open draft [PR #217](https://github.com/djebaz/dtflow/pull/217).
- [x] Add the subsequently owner-requested, unexecuted manual component smoke script at `tools/scripting/manual_form_smoke.js`; no automated test suite additions.
- [x] Add the owner's simpler alternative at `tools/scripting/manual_form_single.js`: one request containing all 14 component types and slider variants, with no follow-up forms or generation calls. Not executed.

## Decisions and risks
- Base: origin/main e0a738b. Existing script run remains one ordinary queue item; child attempts, dispatch ownership, Stop and close semantics stay in the existing runner.
- Official community scripts use nested section answers (for example userSelection[0][0]); the existing flattened headless assumption must be corrected, not propagated to the desktop.
- Descriptor/answer limits apply before widget construction and before durable answer submission. Display content never loads external URLs or local files supplied by the script.
- Picked image bytes are snapshotted on a worker under the existing inputs layout. Directory selection imports a bounded image set into a per-run owned area, not a permanent user-file library or an arbitrary filesystem capability.
- Saved answers are suggestions only, keyed by source SHA and descriptor shape; picker answers must be reselected. They are never automatic consent or an automatic host reply.
- Owner clarification: preserve upstream `image` as a selectable supplied-image grid with an integer answer; native pickers belong to `imageField` and `directory`, without a custom-image answer extension.
- Styling reference inspected as static source: `C:/Users/dje/Downloads/DTFlow Scripts Mockup (standalone).html`. Reuse wrapped captions, section headings, compact track/value controls and narrow stacking; no visual or 800x680 acceptance is claimed.
- ScriptRun schema 3 adds owned-input capabilities and source/form-keyed answer suggestions, retaining schema 1/2 rows. Older builds reject schema 3. The attempts database is unchanged.
- Limits: 1,000,000-byte ASCII-JSON descriptors, 512 components, depth 12, 64,000-character text values, 256 options, 16 images per selection, 6,000,000-byte answer/host-reply budget, 128 forms and 1024 owned selections per run. Recall retains the latest 128 source/form pairs; picker selections are not restored across runs.
- Native folder selection copies only PNG/JPEG/WebP images (nonrecursive, at most 256 entries/16 images/32 MiB normalized source content). JS sees the confined owned copy, never the selected external directory. UI explicitly discloses this boundary.
- Embedded image sources reject URLs, local paths, SVG and arbitrary HTML; raster preparation/thumbnailing remains in the existing headless image-preparation service on a worker. Markdown uses a read-only native viewer with escaped HTML, disabled link navigation and denied resource loads.
- Issue first-pass policy defers user-facing documentation, tests and canonical preview expansion until owner smoke; this implementation plan records progress without claiming acceptance.

## Validation and delivery
- No automated tests added, modified or run. The owner subsequently requested a custom manual script; it is provided but not executed. No validation, rendering, build, desktop launch, inference or provider calls under the selected fast GUI workflow.
- Owner smoke failure investigated read-only: `logs/dtflow-d4dfdd06ed00.jsonl` records a failed script item at 2026-11-03 01:49:04 UTC. Run `f67359ef3ae34f83ad20c57d91dad232` failed with supplied answer sets exhausted; its ledger records zero supplied answer sets, zero published prompts and zero child attempts. The desktop `QueuePage.start_script` still explicitly passed `answers=[]`, disabling the interactive bridge. Changed that call to `answers=None`; no failed run/queue was modified or replayed. The correction is source-only pending a fresh owner Start after restart.
- [ ] Owner: smoke all component types, nested/multiple forms, changed switches, long text and 800x680 layout, native pickers, Stop/close, stale answer races and version recall.
- [ ] Pre-merge follow-up: canonical Designer/renderer states, documentation and owner-approved coverage as required by #196.
- Draft PR only; do not merge or close #196 on source implementation alone.

## Owner smoke follow-up: Inspect > Setup > Run Script
- Owner reported the smoke was mostly OK, but requested the mockup's single form workspace before launch. This supersedes the earlier no-pre-run-Setup presentation decision: Inspect's main action is Setup; the former Configure tab is Setup; the full actual request form and Run Script action live there.
- [x] Rename the canonical tab/action labels and mount actual forms in Setup, not the running screen.
- [x] Make explicit Setup prepare the same queued script execution. Block generation until the first form is explicitly submitted with Run Script, including scripts that call generation before requesting a form.
- [x] Preserve later dynamic requests in the same Setup workspace, with Continue rather than an additional execution or a guessed aggregate form.
- [x] Keep settings captured for that prepared run, reject conflicting script selection, and preserve existing Stop/close/cancellation ownership.
- [x] Extend offline preview definitions only; do not render, execute scripts, add tests or run checks.
- [x] Commit and push this follow-up to draft PR #217; owner smoke of this new flow remains pending.
- Setup executes bounded JavaScript to discover its real first form; it is explicit consent to preparation, not to generation. A pure script that has no forms or generation may complete during preparation. Arbitrary later forms cannot be discovered together before their earlier answers without changing upstream script semantics.
- Owner-provided startup traceback: the asynchronous history callback reached `_sync_drawer_data` before any inspection initialized `_current_hooks`. Initialize the empty hook tuple in `ScriptsPage.__init__`, before callbacks can run; do not treat an unselected version as an inspected one. No agent reproduction or test run.
- The immutable desktop script request carries `require_start=True`; legacy/CLI requests default to their prior behavior. The host parks generation-first scripts on a display-only persisted prompt before request resolution, child-attempt linking or submission. Answering the first user prompt grants permission only after bounded validation/owned-input resolution; Stop is rechecked before generation admission. No second JS run, new dispatcher or automatic answer replay is introduced.
- Added offline state definitions `scripts_setup_ready`, `scripts_setup_loading`, `scripts_setup_followup` and `scripts_setup_narrow`; no captures produced. The canonical `waitingForm` Designer subtree now lives in Setup's scrollable field host. Existing object names remain stable.

## Owner follow-up: preload without a queued job
- Owner's latest smoke (`dtflow-852f49d4b200.jsonl`, run `0dea3173ba074be1a651fa0dcd5ac511`) completed `manual_form_single.js` with one answered form and zero child attempts. This verifies that earlier smoke's completion only, not the following service/UI changes.
- [x] Start preloading the selected supported version's actual Setup while Inspect remains selected; background loading does not navigate or create a queue.
- [x] Separate window-owned preparation from execution: `services/script_setup.py` parks the original bounded JS child on its existing durable prompt/input ownership, without an attempts queue, backend session or generation service. Inventory lookups use only the already displayed snapshot; missing inventory requests an explicit refresh, never an implicit network call.
- [x] Make Run Script the sole creator of the normal one-item queue. It captures the first answers; after ordinary admission the dispatcher adopts the same owned execution and supplies them. No second JS evaluation, restart/replay, new dispatcher or pre-run queue reservation. Missing/lost/cross-loop preparations fail closed.
- [x] Rebuild a fresh preparation on Back to form or Reload Setup, retaining validated exact-version answer recall. Version changes and window shutdown discard only their own unsubmitted preparations; queued-before-admission cancellation releases the parked context without admitting generation.
- [x] Keep live Setup free of the hard-coded run-#5 warning. That Designer warning belongs to the explicit offline fixture only; genuine unknown outcomes remain in their actual retained run/history and queue status.
- [x] Add Inspect-preloaded and reloaded Setup preview definitions, without rendering or executing them.
- [x] Review source changes and commit/push the refinement to draft PR #217, without executing project code or checks.
- [ ] Owner smoke: Inspect preload, queue count unchanged until Run Script, all field/picker answers, generation-first gating, Back to form, cancel/close, source switching and failed/missing preparation admission.
- Preparation may create an internal ScriptRun/prompt and owned snapshots for input provenance, but no job/queue row, queue activity strip or generation attempt exists until explicit Run Script. Existing ScriptRun elapsed time includes preparation; separate execution-only timing is not established here.
- An independent admission event also guards the generation hook, before request resolution or child linking. Even an answer written outside the Setup UI cannot admit generation before the ordinary queue dispatcher adopts the prepared context. Closing one window cannot cancel another window's preparations.
- No new automated tests, test execution, lint/format/syntax/import checks, UI renders, app launch, provider requests, generation or billable actions by the agent. Current modifications are source-only and need fresh owner acceptance.

## Owner picker smoke follow-up
- Latest owner log `dtflow-d4131931bf47.jsonl` and run `5a79de56750944bb9d44e5ddfa7c776f` show completed single-form execution, one answered prompt and zero generation attempts, with no logged warnings/errors. Both image-field answers were empty, the folder answer was empty, and there were no registered owned inputs. The owner reports that native dialogs open but accepting a selection does not update the field; the log does not establish whether notification or preparation failed.
- Use the native dialog's accepted signal and final selectedFiles list consistently for single, multiple and directory modes. Preserve the worker snapshot boundary and stale-form guards. Show selected basenames/owned-folder confirmation in the field; show preparation failure text plus its detailed tooltip/accessibility description there, rather than an empty field with only a distant form-header error.
- Source-only change; no picker, script, test, validation or provider action executed by the agent. Fresh owner smoke is needed to establish native acceptance and input persistence.

# Plan: Phase 5A desktop generation wiring

**Status:** PR 1 and PR 2 implemented together in one draft pull request at the owner's request; the gate remains planned. Follows the merged Phase 3B cloud asset service (PR #18)
**Milestone:** First usable Qt generation path (part of release 0.3.0 with Phase 5B)
**Authoritative acceptance:** `5A-O1` to `5A-O6` from [the initial plan](initial.md#phase-5-testing-ownership); `5-LG1`/`5-LG2` stay separately authorized
**Related plans:** [Qt UX design](2026-10-04-qt-ux-design.md), [workspace navigation](2026-10-04-qt-workspace-navigation.md), [Phase 3B cloud assets](2026-10-04-phase-3b-cloud-assets.md), [Phase 3 CLI](phase-3-cli.md), [release roadmap](releases.md)

## Intent

Turn the disabled Generate preview into a working single-generation workflow without making Qt a second implementation. The Generate page collects a request, shows progress and outcomes, selects LoRAs with diagnostics, prepares or reuses cloud assets, and saves or loads credential-free configurations, all by calling shared services that the CLI already exercises. Phase 5B (queue controls) is a separate later plan.

## What already exists (audited against `main` after PR #17)

Reuse these; do not rebuild them.

| Already delivered | Where | Consequence for 5A |
| --- | --- | --- |
| Background execution boundary (`4-O2`): owned asyncio thread, results returned on the GUI thread, shutdown-before-start safe | `ui/runner.py`, tested in `test_qt_shell.py` | Use `AsyncRunner.submit` for every service call; add no second runner. Only event coalescing for progress is new. |
| Backend selection, connection check, credential entry, identity display (`4-O1` to `4-O6`) | `ui/main_window.py`, `services/connection.py`, `services/credentials.py` | The Generate page reads the backend and endpoint already chosen on the Connection page; no second selector wiring. |
| Status/failure presentation mapping (`Presentation`, `present_status`, `present_failure`) | `services/connection.py` | Extend the same mapping for generation outcomes instead of a new vocabulary. |
| Form loading, stacked workspace, preview-disable helper, theme, dynamic `state` properties | `ui/main_window.py`, `ui/theme.py`, `dtflow_dark.qss` | Enabling a page means removing its controls from the preview-disable pass one group at a time. |
| Canonical Generate form with every needed object name (`promptEdit`, `modelCombo`, `seedEdit`, `samplerCombo`, `generateButton`, `generationProgressBar`, `previewHost`, `loraList`, `addLoraButton`, `compatibilityStatusLabel`, `inventoryEvidenceLabel`, `effectiveRequestBrowser`, `attemptIdentityLabel`, `configurationCombo`, `saveConfigurationButton`, `loadConfigurationButton`) | `ui/forms/generation_page.ui` | No new Designer work is expected; only small additions (for example an asset action row) if needed. |
| Queue form with its object names | `ui/forms/queue_page.ui` | Reserved for Phase 5B. |
| Every service the page needs, as pure functions or classes with no Qt: request resolution and config import/export (`services/config.py`), inventory, preflight and trigger prefixes (`services/preflight.py`), generation (`services/generation.py`), asset prepare/reconcile/validate (`services/assets.py`), queue (`services/queue.py`) | `services/` | Nothing to implement in these; Qt only calls them. |

Not delivered: any wiring of the Generate or Queue pages (every control is disabled), and the one piece of orchestration that is still CLI-only (below). Of the roadmap's 5A bullets, configuration save/load is the cheapest because `export_config`/`load_config`/`resolve_request` are already pure and need no extraction, so it moves into PR 1.

## Finding that shapes the sequence

The orchestration that turns arguments into a generation currently lives in `cli.py`: open the store, create and open the backend with a deadline, refresh inventory, apply trigger prefixes, run LoRA preflight, bind prepared assets, call `GenerationService.generate`, and close everything in the right order. Qt cannot import that and must not copy it. The first PR therefore extracts it into a Qt-independent service that both the CLI and the desktop call, before any widget is enabled. This is the main risk reducer for the whole phase.

## Entry conditions and preserved contracts

- PR #18 is merged and the branch starts from current `main`.
- Credential precedence is unchanged (`DRAWTHINGS_API_KEY`, then the saved Windows credential); the GUI never stores the local shared secret and the CLI stays environment-only.
- No network, generation, persistence or file I/O on the Qt event loop; work goes through the existing `AsyncRunner`.
- Qt renders immutable view data and emits intent. It imports no gRPC, HTTP, SQLite or provider types.
- `dtflow` CLI and services still import and run without Qt (`4-O6`).
- Upload happens only from an explicit asset action; generation and queue never upload.
- A local stop is never presented as confirmed remote cancellation; unknown outcomes are never replayed.
- Previews are never presented as final artifacts.

## Scope

### In

- A shared, Qt-independent generation session service (see PR 1).
- Enabling the Generate page for fake, local and cloud single txt2img requests: prompt, negative prompt, model, seed, size, steps, guidance, sampler.
- Real progress, preview, final-output, failure and unknown-outcome presentation.
- Bounded LoRA selection with weights, preflight diagnostics and the explicit trigger-prefix option.
- Cloud asset actions: prepare a file (explicit upload), reconcile, and select a known local receipt; clear wording that the list is a local registry, not account inventory.
- Save and load of the versioned credential-free generation configuration (already pure in `services/config.py`; wired in PR 1).
- Local interruption with honest evidence.

### Out

- Queue creation, queue panel and queue controls (Phase 5B).
- Preset browser, gallery and batch planning (Phases 6 and 8).
- Image-to-image, inpainting, controls, workflows.
- Conversion, remote asset listing or deletion, and any custom-LoRA support claim before `3-LG4` evidence.
- Packaging changes beyond confirming Qt resources load (packaged-executable verification belongs to the 0.3.0 gate).

## Pull-request sequence

Each PR starts from current `main`, opens as a draft after its first coherent commit, updates this plan with decisions, and is squash-merged only after its owned checks pass.

### PR 1: Generation session service and basic Generate page (the next PR)

Goal: one shared path from form values to a saved result, with the Generate page wired for plain single generations and credential-free configuration save/load.

1. **Extract the session service** into `src/dtflow/services/session.py` (name open to adjustment):
   - owns backend creation through the composition root, bounded open, inventory refresh, optional trigger prefixes, preflight, prepared-asset binding and `GenerationService.generate`, plus ordered close;
   - exposes small async operations and immutable result/event values (`SessionStatus`, progress event passthrough, `GenerationOutcome` with attempt id, output paths, metadata path, warnings, preflight diagnostics, evidence level);
   - takes the saved-credential provider and state directory as injected values, so the CLI passes none and the GUI passes `SavedCredentials`;
   - decides state-directory ownership scope: the store's ownership lock is held only for the duration of an operation, not for the window's lifetime, so a CLI run next to an open GUI fails clearly or succeeds between operations instead of the GUI blocking it forever. Record the chosen lifetime as a decision; add an explicit "state is busy" outcome.
   - Move the corresponding logic out of `cli.py` so `cli.py` only parses arguments, calls the session service and prints JSON. Existing CLI tests are the regression evidence; do not add a parallel matrix.
2. **Request form mapping** in a presenter module (`src/dtflow/ui/generation_presenter.py`): widget values to the same `resolve_request` overrides the CLI builds, with validation errors mapped back to fields by a stable error-to-field table. No resolver logic in widgets.
3. **Enable the Generate page** for the three backends: Generate/Cancel buttons, progress bar, `previewHost` preview, result list with output path and metadata path, failure and unknown banners using the existing `Presentation` mapping. The backend and endpoint come from the Connection page. LoRA and asset controls stay disabled with preview wording in this PR.
3a. **Configuration save and load** (moved here from the former PR 3): wire `configurationCombo` and the save/load buttons to `export_config`/`load_config` through the presenter. The effective request panel shows `resolution_evidence`, so no UI-only defaults exist. A loaded configuration that names a LoRA does not bind a receipt, and the page says so; prepared references are never exported. Owned check: `5A-O4`.
4. **Model and inventory**: populate `modelCombo` from the session's inventory (refresh is an explicit action and runs on the background loop); show inventory observation time and staleness text as given by the snapshot, never inventing freshness.
5. **Interruption**: Cancel calls the shared local interruption path; the UI shows `local stop requested`, then the recorded evidence (`outcome unknown` for a possibly submitted attempt), never "cancelled on server".

Owned checks: `5A-O1`, `5A-O2`, `5A-O4`, `5A-O6` with a controlled fake session and `pytest-qt` (event-loop responsiveness during a gated generation, field-error mapping, previews never promoted to final, unknown outcome wording). Reuse the fake backend scenarios (`success`, `reject`, `lost`, `terminal_error`) instead of new fixtures. Run the fake-backed desktop flow end to end offline.

### PR 2: LoRA selection and cloud asset presentation

- Enable `loraList` with identifier and weight; call `preflight` through the session service and render match/mismatch/unknown and `prepared` diagnostics without any compatibility logic in Qt; keep the "application unconfirmed" evidence label.
- Asset actions: choose a file and call `prepare` (explicit upload with a confirmation dialog that states bytes may be uploaded and credits are not consumed by upload itself but may be by later generation, without inventing pricing), reconcile a record, pick a confirmed receipt for a selected LoRA. Display `confirmed`, `unknown` (with evidence code), `failed`, and the local-registry disclaimer.
- Writer-lock contention (`assets.lock`) shown as a clear "another DTFlow process is preparing assets" message.
- Owned checks: `5A-O3`, `5A-O5` against the in-memory DT+ fixture from `tests/support/cloud_assets.py`; no real upload.

### Gate: desktop composition and live evidence

- Full offline checks on the final head: `uv lock --check`, `ruff check`, `ruff format --check`, `pytest` (Qt tests on the offscreen platform in the sandbox, real display on Windows).
- Windows visual review of every Generate state by the owner.
- Separately authorized `5-LG1` (local) and `5-LG2` (DT+) workflows, only when the owner explicitly authorizes inference; never run by agents.
- `3-LG4` (custom LoRA upload, reuse and use) must be recorded before the desktop advertises custom cloud LoRA support; it is not needed to start non-billable Qt composition.

## Decisions taken in PR 1

- **Session lifetime and lock scope:** per operation. `services/session.py` exposes `open_session` (async context manager that builds the backend before touching state, opens it under the connect deadline, and closes backend then store in order), `run_generation`, `generate_once`, `refresh_inventory`, `read_attempt` and `bind_prepared`. Generation holds the ownership lock only while it runs; inventory refresh uses `durable=False` and takes no lock. A second owner raises `StateBusyError` (a `PersistenceError` subclass, so CLI exit codes are unchanged) and the page shows "State is busy".
- **Runner:** no new runner. `AsyncRunner` now also reports a cancel requested through the returned future to its callback (not at shutdown). `EventRelay` coalesces progress and previews to the latest of each kind between GUI deliveries and delivers every other event, in sequence order.
- **Cancel:** the button cancels the future; wording then comes from `read_attempt` evidence (`present_interruption`), never from the click.
- **Error-to-field table:** `ui/generation_presenter.py` (`ERROR_FIELDS`, by the leading word of the resolver's message); unlisted messages become a general banner. The presenter is Qt-free; `ui/generation_page.py` is the widget controller.
- **Configuration UX (ambiguity, default chosen):** Save and Load use file dialogs defaulting to `<state>/configs`; `configurationCombo` lists `*.json` found there and selecting an entry loads it. A loaded LoRA is displayed read-only and kept in the page state so it round-trips through save and generation; no receipt is bound.
- **Small Designer additions:** `refreshInventoryButton`, `cancelButton` (labelled "Stop waiting"), `generationStatusLabel`, `resultList`, and an editable `modelCombo`; the preview notice now says only LoRA and cloud asset controls are preview-only.
- **Form defaults:** the form's visible defaults (512x512, 20 steps, guidance 7) are passed as explicit overrides and shown as such in the evidence panel; the empty prompt is rejected by the shared resolver.

## Decisions taken in PR 2 (same pull request)

- **Layout:** `ui/lora_panel.py` owns LoRA selection and the asset list; `ui/generation_page.py` keeps request, progress and configuration. The preview badge is removed because every Generate control is now wired.
- **Adding a LoRA:** a dialog with an editable list (inventory LoRAs after a refresh plus identifiers of known local receipts) and an explicit weight (the inventory default weight when known, else 1.0, only as the dialog's starting value). Dialog bounds are UI-only; the shared request model requires a finite weight.
- **Diagnostics:** an explicit Check button calls `session.check_loras` (fresh inventory, no state lock) and `present_preflight` renders the shared diagnostics; a blank prompt gets a placeholder for the check only because nothing is dispatched. Generation also returns its diagnostics, and a blocked request shows the service's `(identifier: code)` pairs.
- **Trigger prefixes:** an unchecked-by-default checkbox maps to the shared `prefix` option.
- **Assets:** `session.open_assets/prepare_asset/reconcile_asset/list_assets` are shared with the CLI; actions are enabled only when the Connection page selects cloud; Use for LoRA binds by identifier (the LoRA must already be selected, the receipt confirmed) and the binding lives only in the page; `generate_once(asset_ids=...)` re-reads the confirmed receipt through `bind_prepared` and the server re-validates it at dispatch. The confirmation text states that upload may happen and that upload itself does not consume credits while a later generation may; no pricing is claimed.
- **Contention:** `AssetsBusyError` (a `PersistenceError` subclass, same message) is shown as "Assets busy".

## Decisions still open

- None for Phase 5A code; the gate items below remain.

## Risks and mitigations

- **Logic drift between CLI and Qt:** extract first, keep the CLI as a thin caller, and keep the existing CLI tests authoritative.
- **GUI holds the state lock and blocks the CLI:** per-operation ownership and an explicit busy outcome.
- **Frozen UI during inventory or generation:** every service call runs on the background loop; tests assert the event loop keeps processing while a gated call is pending.
- **Preview treated as result:** the view model carries `designation`; only `final` artifacts populate the result list.
- **Misleading cancellation:** wording comes from recorded evidence, never from the button press.
- **Silent upload:** the only upload entry is the confirmed `prepare` dialog.
- **Overstated LoRA support:** diagnostics keep availability, architecture and application evidence separate; custom support is not advertised before `3-LG4`.

## Exit criteria for Phase 5A

- `5A-O1` to `5A-O6` pass through their documented authoritative layers.
- The Generate page produces a durable result with metadata through the same service path as `dtflow generate`, with no duplicated resolver, preflight or persistence logic in `ui/`.
- Upload is reachable only from the explicit prepare action; generation never uploads.
- Cancel and unknown outcomes are presented honestly; local stop is never shown as remote cancellation.
- Saved configurations are credential-free and portable; prepared references are not exported.
- Docs (`README.md`, `initial.md`, runtime layout if lock scope changes, `unreleased.md`) distinguish what is wired from what remains preview-only.
- Live `5-LG1`/`5-LG2` and `3-LG4` are either recorded with exact evidence or explicitly left unverified without support claims.

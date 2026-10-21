# Plan: remember the Prepare folder and select multiple LoRA files

**Date:** 2026-10-22. **Status:** source implemented after owner approval; acceptance remains scoped below. **PR:** [#151](https://github.com/djebaz/dtflow/pull/151) (draft). **Branch:** `feature/prepare-files-picker`.

## Scope

- In: the cloud LoRA **Prepare** picker remembers its last accepted folder across application restarts and permits several supported converted LoRA files in one selection.
- Out: conversion, recursive folder import, drag-and-drop, new file formats, parallel uploads, generation queues, provider protocol changes, automatic retry/resume, new dependencies and packaging changes.
- The initial plan-only checkpoint was committed before implementation. The owner subsequently requested implementation, reported “it works” during smoke testing, requested failure diagnostics, and explicitly authorized `uv run ruff format .`. No tests are added or run; renders, other validation and builds remain user-owned.

## Current source and boundaries

- `src/dtflow/ui/lora_panel.py`: `_choose_asset_file` selects one `.ckpt` file, initially at the state directory. `LoraAssetPanel.prepare` rechecks busy/backend state after the picker and submits `services.session.prepare_asset`; `_prepared` presents the receipt and refreshes/binds the selected LoRA.
- `src/dtflow/services/session.py` and the existing asset services remain authoritative for validation, upload negotiation, durable receipts and uncertain outcomes. Do not copy that logic into Qt.
- Persistence belongs in the Qt-free persistence layer. Reuse `persistence/runtime_environment.py` runtime keys and the atomic/versioned preference patterns already used by `export_preferences.py`; do not extend the concurrency-only settings writer in a way that loses unrelated values.
- Read [cloud integration](../architecture/cloud-integration.md), [runtime layout](../architecture/runtime-layout.md), [desktop asset behavior](../guides/desktop.md) and root [DESIGN.md](../../DESIGN.md) before implementation. Preserve canonical Designer forms, shared styles, the owned runner and bottom activity overlay.

## GUI components affected

- [x] Plan identifies the existing Prepare picker/controller and receipt flow.
- [x] `ui/lora_panel.py`: native multi-file picker, remembered initial directory and one sequential preparation session.
- [x] Qt-free persistence helper: a small versioned `<state>/prepare_preferences.json` containing only a bounded map of runtime keys to last selected directory.
- [x] Existing asset status/tooltip and shared activity strip: plural upload disclosure, item-count progress and one final summary. No new tab or top-level dialog.
- [x] Offline state definitions in `tools/ui/render_states.py` for multi-file progress, partial results and persistence failure; defined only, not executed.
- [x] Documentation and unreleased notes describe source behavior separately from acceptance.
- Assets/tokens, PyInstaller specification and dependencies: no changes planned.

## Decisions

### Remembered directory

- Store the parent folder when the user accepts a nonempty selection, independently of later upload success. Cancelling the picker leaves the preference unchanged.
- Keep separate Windows, WSL and other platform values using the existing runtime-key policy. Never translate paths between runtimes or export/log the preference.
- On the next Prepare action, start at that runtime's remembered accessible folder; if absent, missing or inaccessible, use the current state-directory fallback. Do not scan drives or create the remembered directory.
- Save atomically. Leave unreadable/newer preference files unchanged; present one safe inline warning if the preference cannot be read/saved, while permitting explicit preparation to proceed. Do not report an upload failure merely because convenience-state saving failed.
- This is durable convenience state, not cache: cache cleanup and `--no-cache` must not erase or bypass it. No attempt/queue database schema change.

### Multiple selected files

- Keep the existing supported `.ckpt` filter; allow multiple explicit file selections. Selecting files starts Prepare directly, without a second confirmation. Update the existing disclosure to explain that each selected file may upload; later generation may consume credits.
- Deduplicate repeated selected paths without scanning directories or equating different files by basename; retain picker order and take a snapshot of the accepted selection.
- Revalidate backend/busy/runner state after the picker. Hold one existing busy ownership for the whole sequential session; never briefly re-enable conflicting actions between files.
- Use the existing `prepare_asset` service once per admitted file. Run only one preparation at a time, on the existing worker path; no new generic job queue or parallel uploader.
- Reuse service receipt outcomes without converting unknown/uncertain into success. Continue to the next explicit file after an ordinary per-file failure/unknown result, but never retry that file automatically. Stop admitting remaining files on shutdown or changed backend/credential ownership; report the unstarted count honestly if the UI remains alive.
- Recheck ownership before each new submission. Navigation or dismissing feedback must not silently cancel an admitted upload. Existing service settlement and runner shutdown behavior remain authoritative; do not claim remote cancellation.
- Show count-based progress such as “Preparing 2 of 5”, not synthetic percentages or ETA. Final summary distinguishes confirmed, failed, unresolved and unstarted files; use the shared overlay's existing success/partial/error lifecycle.
- Preserve single-file behavior. Process receipt refresh/binding sequentially so every confirmed selected LoRA is retained and an asynchronous list refresh cannot overwrite the preceding file's pending binding. Do not overwrite an existing selected LoRA's user-chosen weight or silently clamp it.
- Failure diagnostics use the shared producer API at warning level in normal and dev mode: folder preference read/write failure, incomplete per-file preparation and registry unavailability. Only safe reason/outcome codes, item counts and known backend error/remote-outcome codes are included; no filenames, local paths, contents, credentials, signed upload URLs or raw exception text. Logging never triggers retries.

## Action items

- [x] Inspect the current picker, controller, receipt-binding and persistence patterns.
- [x] Commit/push the plan and open draft PR #151 before implementation.
- [x] Owner approves implementation following this plan-only PR checkpoint.
- [x] Implement the narrow runtime-specific folder preference and fallback behavior.
- [x] Replace the single-file picker seam with multi-selection and adapt its offline fixtures without adding tests.
- [x] Implement sequential admission, coherent busy ownership, receipt binding and aggregate feedback without provider/service duplication.
- [x] Extend offline render-state definitions; do not run them. States `generate-43`–`generate-46` cover multi-file start/progress, partial results and folder-preference failure; earlier single-file states remain.
- [x] Update the desktop guide, relevant architecture/status documentation and unreleased entry to describe delivered behavior, not planned behavior. README/AGENTS/DESIGN describe the narrow new behavior and contract.
- [ ] Owner performs acceptance and authorizes any subsequent merge separately. No merge, tag or release is part of this plan-only task.

## Open questions

No blocking questions for planning. Assumption: “Prepare” means converted cloud LoRA file preparation, not image-operation source/mask preparation. Multiple files are uploaded sequentially, not concurrently.

## Validation and acceptance (user-owned; not executed)

Owner report during work on 2026-10-22: “i'm smoke testing , it works”. Recorded as successful owner feedback for the working source checkout before final commit/failure-logging closeout, not an exact packaged executable, all-platform, failure/shutdown matrix or CI claim. No agent-run provider/upload operation, test, render or build occurred. Formatter execution is the only separately authorized local project tool operation (with its mandatory environment-readiness prerequisite).

Executed at owner request: `uv run ruff format .` completed successfully (1 file reformatted, 391 files unchanged) after the required environment readiness step. No local lint, syntax check, pytest, render or build was run. Automatic CI on the pushed implementation is separate evidence and is not claimed by this record.

Owner subsequently reported `test_prepare_adds_and_binds_the_lora_and_adding_it_again_reconciles_and_binds` failing. Source inspection identified an ordering regression: the single-file batch path published the selected LoRA before its final registry refresh/“Receipt bound” presentation. Single-file preparation now defers adding/binding to that existing registry callback, preserving the old observable completion ordering; multi-file preparation retains per-file binding. The existing test was not weakened or changed, and no local pytest was run to verify this correction.

- [ ] User: accept a file, restart, and confirm the picker restores that runtime's folder; cancellation does not change it.
- [ ] User: missing/inaccessible folder and malformed/newer preference fallback, including a save failure that does not stop explicit preparation.
- [ ] User: multiple distinct files, duplicate selection, mixed confirmed/failed/unknown receipts, all-failed and single-file behavior; each admitted file is handled once and bindings/weights remain correct.
- [ ] User: busy/backend/credential changes, navigation and shutdown never admit unintended additional uploads or replay uncertain work.
- [ ] User: Windows native picker/keyboard/DPI and inline feedback. Offline screenshots, if later requested, are layout evidence only.
- [ ] User: separately authorize any real network/upload check; this planning task performs no provider operations, generation, tests, renders, validation or builds.
- Build/executable acceptance: no packaging change planned; only if the owner chooses to build later.

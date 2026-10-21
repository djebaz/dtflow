# Plan: remember the Prepare folder and select multiple LoRA files

**Date:** 2026-10-22. **Status:** plan only; implementation has not started. **Branch:** `feature/prepare-files-picker`.

## Scope

- In: the cloud LoRA **Prepare** picker remembers its last accepted folder across application restarts and permits several supported converted LoRA files in one selection.
- Out: conversion, recursive folder import, drag-and-drop, new file formats, parallel uploads, generation queues, provider protocol changes, automatic retry/resume, new dependencies and packaging changes.
- This PR starts with this plan and release-audit bookkeeping only. Do not implement until the owner requests the next step. Follow the requested fast GUI workflow: do not add tests or run tests, renders, validation or builds; user performs acceptance.

## Current source and boundaries

- `src/dtflow/ui/lora_panel.py`: `_choose_asset_file` selects one `.ckpt` file, initially at the state directory. `LoraAssetPanel.prepare` rechecks busy/backend state after the picker and submits `services.session.prepare_asset`; `_prepared` presents the receipt and refreshes/binds the selected LoRA.
- `src/dtflow/services/session.py` and the existing asset services remain authoritative for validation, upload negotiation, durable receipts and uncertain outcomes. Do not copy that logic into Qt.
- Persistence belongs in the Qt-free persistence layer. Reuse `persistence/runtime_environment.py` runtime keys and the atomic/versioned preference patterns already used by `export_preferences.py`; do not extend the concurrency-only settings writer in a way that loses unrelated values.
- Read [cloud integration](../architecture/cloud-integration.md), [runtime layout](../architecture/runtime-layout.md), [desktop asset behavior](../guides/desktop.md) and root [DESIGN.md](../../DESIGN.md) before implementation. Preserve canonical Designer forms, shared styles, the owned runner and bottom activity overlay.

## GUI components affected

- [x] Plan identifies the existing Prepare picker/controller and receipt flow.
- [ ] `ui/lora_panel.py`: native multi-file picker, remembered initial directory and one sequential preparation session.
- [ ] Qt-free persistence helper: a small versioned `<state>/prepare_preferences.json` containing only a bounded map of runtime keys to last selected directory.
- [ ] Existing asset status/tooltip and shared activity strip: plural upload disclosure, item-count progress and one final summary. No new tab or top-level dialog.
- [ ] Offline state definitions in `tools/ui/render_states.py` for multi-file progress, partial results and persistence failure; define only, do not execute.
- [ ] Documentation and unreleased notes when behavior actually changes.
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
- Any dev diagnostics use the shared producer API with safe counts/outcomes only; no local paths, file contents, credentials or signed upload URLs.

## Action items

- [x] Inspect the current picker, controller, receipt-binding and persistence patterns.
- [ ] Owner approves implementation following this plan-only PR checkpoint.
- [ ] Implement the narrow runtime-specific folder preference and fallback behavior.
- [ ] Replace the single-file picker seam with multi-selection and adapt its offline fixtures without adding tests.
- [ ] Implement sequential admission, coherent busy ownership, receipt binding and aggregate feedback without provider/service duplication.
- [ ] Extend offline render-state definitions; do not run them.
- [ ] Update the desktop guide, relevant architecture/status documentation and unreleased entry to describe delivered behavior, not planned behavior. Update README only if a feature-level description needs it; update AGENTS only if its contract changes.
- [ ] Owner performs acceptance and authorizes any subsequent merge separately. No merge, tag or release is part of this plan-only task.

## Open questions

No blocking questions for planning. Assumption: “Prepare” means converted cloud LoRA file preparation, not image-operation source/mask preparation. Multiple files are uploaded sequentially, not concurrently.

## Validation and acceptance (user-owned; not executed)

- [ ] User: accept a file, restart, and confirm the picker restores that runtime's folder; cancellation does not change it.
- [ ] User: missing/inaccessible folder and malformed/newer preference fallback, including a save failure that does not stop explicit preparation.
- [ ] User: multiple distinct files, duplicate selection, mixed confirmed/failed/unknown receipts, all-failed and single-file behavior; each admitted file is handled once and bindings/weights remain correct.
- [ ] User: busy/backend/credential changes, navigation and shutdown never admit unintended additional uploads or replay uncertain work.
- [ ] User: Windows native picker/keyboard/DPI and inline feedback. Offline screenshots, if later requested, are layout evidence only.
- [ ] User: separately authorize any real network/upload check; this planning task performs no provider operations, generation, tests, renders, validation or builds.
- Build/executable acceptance: no packaging change planned; only if the owner chooses to build later.

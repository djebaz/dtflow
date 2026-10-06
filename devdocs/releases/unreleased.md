# Unreleased

## Added

- Phase 8B–8D daily-use refinement: a durable Draw Things `custom_configs.json` preset library with global Connection-page import/export and conservative supported-field application; Images actions to populate Generate or explicitly regenerate as a fresh lineage-linked request; and measured, confirmed cleanup of disposable-cache categories while preserving durable state and outputs. Only the inventory category holds files today; gallery thumbnails are in memory and nothing writes the thumbnail or derived directories yet. Preset application is limited to `model`, `width`, `height`, `steps`, `guidanceScale`, `seed`, two verified samplers and `all`-mode LoRAs; all other native fields are preserved for re-export only.
- Selects across Connection, Generate, Queues and Images now show a chevron segment with hover, open and disabled states and a styled popup list, so they read as dropdowns instead of text inputs.
- The Queues page has a **Clear queues** button that removes finished, cancelled and paused queue records (items, controls and events) after confirmation. Attempts and saved images are kept, and queues that are running or hold an unresolved outcome are never removed.
- The shared header now uses a compact segmented Run control instead of the manual right-Inspector toggle. It exposes truthful global generation/queue activity, starts a runnable selected queue through the existing dispatch path, pauses/resumes only work owned by this window, shows reported single-generation steps, and shows the running queue's settled-item count (settled of total) in the ring. The right Inspector now follows responsive layout automatically.

## Fixed

- PR #51 retains the last successful display inventory per provider, restores identity-matching observations from memory or the existing disk cache on provider returns, and preserves departing form fields before clearing discovery. Model/LoRA counts, cached/remembered/refreshed provenance and original observation timestamps remain visible without a current-run refresh and alongside refresh errors. Failed requests keep previous choices/selections; discovery restoration no longer silently prunes selected checkpoints/LoRAs. Endpoint/selected-credential changes invalidate mismatched evidence, saving the same key does not erase it, and obsolete async completions are ignored. Generation/preflight live validation, cache filenames/schema and credentials precedence remain unchanged.

## Validation and release audit

- PR #51: the owner reported source smoke tests OK on 2026-10-06 during implementation. No exact source/build ID or scenario list was supplied. Local static checks passed; no agent-run tests/smoke or packaged acceptance is claimed. Automated tests are delegated to GitHub Actions; outcome pending.

- PR #46 implements the Phase 8B–8D scope after the 0.3.5 release boundary. The owner reports the PR CI passed after repairing the post-release merge/formatting issue. Runtime/Windows interaction acceptance and final 0.4.0 packaged-executable acceptance remain separate.
- PR #49 implementation work was pushed without running tests at the owner's request. The owner reported an earlier branch head launched successfully; the latest queue-control fixes still require smoke/CI acceptance. No live provider generation, inference, training, upload or potentially billable action was performed for this work.

- PR #50 repository maintenance: remove the case-only design-system readme.md duplicate, preserving the newer content under canonical README.md. Windows checkouts no longer alternate between apparently modified README paths; existing links remain valid. No runtime behavior changes.

- Cloud LoRA preflight: a LoRA that the DT+ inventory does not list, or lists only as an unclassified file, is now reported as unconfirmed (`availability_unknown` / `architecture_unknown`) instead of blocking dispatch as not available. Local and confirmed-mismatch behavior is unchanged. Whether DT+ then applies the LoRA is not verified; no generation, upload or billable action was run.

- LoRA weights such as 0.6 no longer fail `wire_configuration_mismatch`: the wire check compared a float32-rounded weight exactly, while drawthings-py rounds some values on decode. The check is now tolerant by float32 precision.
- Cloud custom LoRAs: a Prepare that confirms now also adds the LoRA to the Generate list (weight 1.0) and binds its receipt, and adding a LoRA that has a receipt reconciles it (metadata check, no upload) and binds it if confirmed, each in one action. The Reconcile and Use buttons are removed.
- Cloud custom LoRAs: a LoRA bound to a prepared (uploaded) asset is now named by its SHA-256 in the generation request, which Draw Things documents as the name its cloud server downloads uploaded LoRAs by; the file name was silently ignored. Confirmed live by the owner on Windows (Z-Image Turbo, DT+). No generation, upload or billable action was run.

- Cloud custom LoRA fix: generation now sends `override.loras` (`name`, `file`, `prefix`, `version`; the server drops an entry missing any of them) for LoRAs bound to a prepared receipt, with `version` taken from the selected base model's inventory architecture. Previously only the file name reached the server, which accepted the request but showed no LoRA effect. Whether the server now applies the LoRA needs a Windows live check; no generation, upload or billable action was run.

## Release audit

- PRs: #46, #48, #49, #50, #51
- Scope: Phase 8B–8D presets, reuse and cache management; documentation reconciliation; global segmented Run control; Windows-safe canonical design-system README without a case-only tracked duplicate; retained provider inventory and persistent last-observed UI statistics.

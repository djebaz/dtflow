# Unreleased

## Added

- Phase 8B–8D daily-use refinement: a durable Draw Things `custom_configs.json` preset library with global Connection-page import/export and conservative supported-field application; Images actions to populate Generate or explicitly regenerate as a fresh lineage-linked request; and measured, confirmed cleanup of disposable-cache categories while preserving durable state and outputs. Only the inventory category holds files today; gallery thumbnails are in memory and nothing writes the thumbnail or derived directories yet. Preset application is limited to `model`, `width`, `height`, `steps`, `guidanceScale`, `seed`, two verified samplers and `all`-mode LoRAs; all other native fields are preserved for re-export only.
- Selects across Connection, Generate, Queues and Images now show a chevron segment with hover, open and disabled states and a styled popup list, so they read as dropdowns instead of text inputs.
- The shared header now uses a compact segmented Run control instead of the manual right-Inspector toggle. It exposes truthful global generation/queue activity, starts a runnable selected queue through the existing dispatch path, pauses/resumes only work owned by this window, shows reported single-generation steps, and shows the running queue's settled-item count (settled of total) in the ring. The right Inspector now follows responsive layout automatically.

## Validation and release audit

- PR #46 implements the Phase 8B–8D scope after the 0.3.5 release boundary. The owner reports the PR CI passed after repairing the post-release merge/formatting issue. Runtime/Windows interaction acceptance and final 0.4.0 packaged-executable acceptance remain separate.
- PR #49 implementation work was pushed without running tests at the owner's request. The owner reported an earlier branch head launched successfully; the latest queue-control fixes still require smoke/CI acceptance. No live provider generation, inference, training, upload or potentially billable action was performed for this work.

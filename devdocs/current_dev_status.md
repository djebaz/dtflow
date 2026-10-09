# Current development status

Snapshot: 2026-10-06. Phase 8A viewing, the catalogue-driven Generate discovery from PR #40 and the 8B–8D presets, reuse and cache management from PR #46 are implemented on main. The 0.3.0 publication evidence below is retained from the earlier snapshot; PR #31 build/draft workflow status is not re-verified here.
This is coding orientation against [initial.md](plans/initial.md), not a release declaration. Implementation scope comes from source/plan inspection; owner-reported tests and interactive smoke observations are recorded separately. The agent ran no live generation or provider operations.

## Implemented on main

| Initial-plan phase | Implemented scope |
| --- | --- |
| 1 — Connectivity proof | Diagnostic gRPC/direct-cloud feasibility probes and recorded evidence. |
| 2 — Backends | Backend-neutral contract, fake backend, local Draw Things and direct DT+ adapters; packaging investigations. |
| 3 — CLI/services | Generation/configuration, LoRA preflight, cloud LoRA prepare/reuse with durable receipts, durable rolling queues. |
| 4 — Qt shell | Connection selection/status and Windows saved API-key service. |
| 5 — Desktop execution | Generate, LoRA/cloud asset actions (Prepare adds and binds the receipt automatically; custom cloud LoRA use owner-reported working 2026-10-06, see [lora-assets.md](plans/lora-assets.md#referencing-a-prepared-lora-in-a-generation-request)), configuration save/load and queue controls through shared services. |
| 6 — Batch planning | Shared seed/pair/cross planner, bounded expansion and CLI/desktop dry runs. |
| 8A–8D — Gallery/presets | Saved-output browsing, native-format presets with conservative field application, reuse/explicit regeneration and category-specific cache cleanup (inventory snapshots and source image computations use disk cache; gallery thumbnails remain in memory). Owner accepted 8D source smoke after passing CI on 2026-10-09; other phase/packaged gates remain separate. |

Implemented does not mean universal compatibility or remote cancellation. On 2026-10-05 the owner reported the other 0.3.0 release checks complete, identifying the exact portable Qt executable smoke test as the remaining gate; this is owner-reported, not newly assistant-verified. Keep existing detailed source/live evidence rather than rerunning it without need. PR #29 changes the builder to a GUI-first windowed launcher; the owner has now built and exercised its dirty 0.1.0 artifact, including key/form persistence and bounded local/cloud generation/queues. See [the identified GUI smoke](research/windows-gui-smoke-2026-10-05.md) for artifact identity, one unresolved cloud outcome and coverage limits. The owner subsequently built/launched the clean 0.3.0 artifact and confirmed its displayed identity; the agent verified bundled metadata/checksum and downloaded release assets. [0.3.0 is published](https://github.com/djebaz/dtflow/releases/tag/v0.3.0); earlier live coverage/limitations remain separate. See [the build guide](guides/building.md), [0.3.0 notes](releases/v0.3.0.md) and [packaging evidence](research/windows-packaging.md).

## Not yet implemented

| Phase | Remaining scope |
| --- | --- |
| 7 — Advanced scheduling | Priorities, fairness and adaptive concurrency; not a prerequisite for the proposed release milestones. Basic rolling dispatch already exists. |
| 8 — Gallery/presets | 8D source/runtime scope is owner-accepted (2026-10-09); other phase/packaged acceptance remains separate. Planned 8E Images multi-selection/bulk deletion is separate and not automatically a 0.4.0 gate. |
| 9 — Image operations | 9A img2img, 9B existing mask-file inpainting, 9C interactive masks and selected controls/references. |
| 10 — Workflows | 10A versioned workflow files/compiler/execution; 10B visual editor and expanded semantics. |
| 11 — Broader DT+ | Additional verified cloud capabilities and hardening. Token handling, secret safety, subscription-only policy and ambiguous-outcome protection already belong to Phases 2/3; do not rebuild or defer them. |

## Added/refined scope

- Shared persistent Connection/Generate/Queues shell, image-first fit/zoom/pan preview, centralized design tokens and monogram: PRs #25/#26.
- Offline simulation, per-provider form memory, desktop random seed 0 and output/Inspector/concurrency refinements: PR #27, already merged (not an open draft).
- PR #28 added usage guides, a GUI-first README and this short coding snapshot.
- PR #29 adds the desktop-first stamped builder and no-console startup/log diagnostics, with source CLI and generation policies unchanged.

- PR #31 adds an opt-in cached Windows build and draft-only release workflow, including artifact-only same-repository PR-head builds; not yet runner-verified. Owner executable smoke/publication remain explicit.

- PR #33 established the offline Grid/List/Detail presentation and shared Images styling; its fixture launcher remains available for visual development. See [the preview plan](plans/2026-10-05-phase-8a-ui-preview.md).
- PR #34 connects those views to committed saved outputs through shared read-only services and CLI commands. It adds bounded query/paging, asynchronous thumbnails, positive-prompt/model/LoRA/date/dimensions/aspect filters, deterministic sorting, durable viewer state, anchored auto-refresh, real provenance/raw metadata, file actions and missing/corrupt-file handling. The thumbnail control is 25–250% with 100% as the standard size; first-run Images opens Grid and Detail defaults to Fit. The owner reports the full test suite passed after the Qt lifecycle fix and performed interactive smoke testing that drove thumbnail-layout refinements. Packaged/native-DPI acceptance remains separate. See [the viewer plan](plans/2026-10-05-phase-8a-viewer.md).
- PR #40 is merged and implements catalogue-driven Generate discovery: bundled reviewed Prime/Niche/checkpoint, LoRA and Control classifications are resolved by exact identity against the active provider inventory; Generate filters classified checkpoints by All/Image/Video, base family and Prime-by-default/Niche-additive policy, then filters available LoRA candidates by known family/mode compatibility. Checkpoint and LoRA choices use substring-searchable selection dialogs, with explicit custom-identifier escape hatches for unlisted artifacts. Local mode has an explicit `Include DT+ Cloud` bridge option for classified checkpoint candidates omitted from local inventory. User custom-LoRA metadata lives under the durable state catalogue directory rather than packaged resources; Connection provides validated JSON import and confirmed clear actions, and Generate refreshes its LoRA candidates immediately after either change. The owner reports the full test suite passed and CI run 37449328588 passed before the squash merge; no live generation was required for this discovery change. No generation protocol or CLI interaction redesign was introduced. See [the catalogue plan](plans/2026-10-06-catalogue-model-lora-selection.md).

- Follow-up catalogue research classifies 264 of the 514 formerly unknown Niche base-model entries using exact upstream Draw Things metadata and sourced model-family evidence; 250 remain unknown rather than guessed. No provider availability or runtime compatibility is implied. See [the evidence ledger](research/niche-catalogue-classification-2026-10-06.md).

- Catalogue follow-up: user custom LoRA metadata now refines bundled definitions field-by-field instead of hiding their known compatibility, and catalogue views are cached across selector queries until inventory or the durable custom-LoRA file changes.

## Next coding direction

PR #37 refines Phase 8A refresh: identity-based Grid/List/filmstrip reconciliation
preserves retained thumbnails/in-flight work and the selected Detail image state
when outputs are added, removed or reordered. See the
[follow-up plan](plans/2026-10-06-images-incremental-refresh.md). Code presence is
not agent-run acceptance; the owner reports incremental refresh works. The
subsequent Auto-refresh color/hover/focus adjustment was also owner-smoke-tested
successfully after `a9e393a`. No agent-run tests or packaged acceptance are claimed.

PR #49 is merged from `ui/global-run-control` and replaces the obsolete manual Inspector toggle with a compact segmented Run control in the shared brand row. The branch reuses existing queue Start/Pause/Resume paths, keeps single-generation pause unsupported rather than faking it, publishes reported generation progress, and shows the running queue's settled-item count in the ring, so concurrent items still produce a number. Queue work owned by another process is status-only in the compact header, and settled/problem queue selections do not leave persistent header badges. The owner reported that an earlier implementation head launched successfully; the latest queue-control fixes have not been agent-tested, and no agent-run tests or live provider operations are claimed.

The [release roadmap](plans/releases.md) sequences Phase 8 daily-use refinement (proposed 0.4.0), then 9A img2img (0.5.0), then 10A workflows (0.6.0). These are planned milestones, not shipped versions. Future executable acceptance remains separate from the next feature to code; follow [the release guide](guides/releasing.md). The owner selected interim 0.3.8 preparation with [versioned notes](releases/v0.3.8.md); 0.3.5 remains a draft candidate. Owner accepted the later 8D source follow-up as recorded below; other phase and exact final executable acceptance remain separate gates; this version choice does not declare 0.4.0 accepted. Recheck the roadmap and relevant phase plan before choosing work.

## JPEG export follow-up (merged PR #55, 2026-10-09)

PR #55 adds a local-only JPEG export hub in the middle panel of Connection: one
folder, readable filenames, Windows-friendly EXIF, portable generation metadata,
prompt privacy, optional sidecars and cancel/progress. Generate/Images expose only
read-only selection/filter seams; originals and durable references remain untouched.
See the [plan](plans/2026-10-09-jpeg-image-export.md) and
[architecture](architecture/image-export.md). This is branch implementation, not a
release declaration. No tests or smoke tests were added/run under fast-implement;
the owner reports a source smoke export (345 JPGs at the folder root, no subfolders), but remaining scenario/packaged acceptance stays separate. See [smoke evidence](research/jpeg-export-smoke-2026-10-09.md). No inference or billable actions.

The owner-requested [pixel-statistics detector PoC](../tools/images/README.md) now shares its v2 pixel service with optional default-on export routing into noise/blank subfolders, not a Phase 8 quality gate. Its [plan](plans/2026-10-09-noise-detector-poc.md) records owner-requested labelled sample runs and the version-2 spatial rule for two confirmed misses; thresholds are sample-fitted, with independent validation pending and no general accuracy claim.

## Maintenance

Update this short snapshot when implementation scope changes, with the reviewed base revision/date and evidence links. Keep completed, remaining and added scope aligned with `initial.md`; retain detailed validation in plans/research/release notes. If this snapshot disagrees with source or verified evidence, those sources win: refresh the snapshot rather than assuming completion. Do not append a running changelog here.

## Inventory continuity follow-up

PR #51 restores identity-matching provider inventory observations and per-provider form selections; keeps model/LoRA counts, provenance and observation time visible across refresh errors and cache-only launches. Live generation validation is unchanged. Static checks only locally; tests are delegated to GitHub Actions by the owner. The owner reports source smoke tests OK (2026-10-06); exact build/scenarios were not supplied, and packaged acceptance remains separate. See [the plan](plans/2026-10-06-retain-provider-inventory.md).

The [routing follow-up](plans/2026-10-09-export-noise-routing.md) adds the canonical Connection checkbox, saved preference and routed completion counts. Static checks only for this integration; owner GUI/export smoke acceptance remains pending.

Owner reports noise/blank routing works (2026-10-09); no exact packaged build was supplied. The [repeat/date follow-up](plans/2026-10-09-export-repeat-dates.md) adds identity/checksum skip counts and Windows creation/EXIF dates based on recorded successful save time, not exact inference start/end. Owner repeat/date acceptance remains pending; the reference JPEG PoC is retained under tools/images without owner-local defaults.

Owner-requested branch validation: Ruff lint/format passed; full offline pytest rerun 426 passed, 3 gated live skips (109.46s), after updating the existing Pillow import boundary for the two headless image services. Repeat/date GUI smoke and packaged acceptance remain separate; no real inference or billable actions.

The [source-computation follow-up](plans/2026-10-09-image-computation-cache.md) supersedes destination indexes: full-ID filenames and transient folder membership drive early skips; a central Derived cache reuses source-only checksums/detection across destinations, honoring no-cache and cleanup. Legacy filenames retain EXIF fallback. Static implementation checks only; owner speed/cache/recovery acceptance and a fresh suite run remain pending.

## Phase 8D completion / 8E plan (PR #56, 2026-10-09)

Shared nonblocking thread/process cache ownership now coordinates inventory and image-computation I/O with category measurement/purge. Export owns its cache computation/checkpoint interval or continues without persistent cache when ownership is unavailable. Connection refuses cleanup during its active export/generation/dispatch, reports file counts/busy/partial errors, and bounds category traversal while rejecting links/junctions. Actual inventory network refreshes stay outside the file-I/O lease and can write fresh observations after cleanup. No provider/generation behavior changes. Owner accepted the selected 8D source/runtime scope after green CI and successful smoke tests on 2026-10-09 at runtime head bb92217. No new tests or agent smoke under fast-implement; packaged acceptance is not implied.

See the [8D completion plan](plans/2026-10-09-phase-8d-cache-completion.md) and [cache coordination architecture](architecture/cache-management.md). Owner-selected [8E multi-selection/bulk deletion](plans/2026-10-09-phase-8e-image-management.md) is planned only; session-only checked selection, a partial fill up to 10,000 and Recycle Bin-only confirmation are settled, while durable recovery, root eligibility and file concurrency still require engineering design. Original files and user export folders are never disposable cache.

## Phase 8E plan-first implementation work (2026-10-09)

Owner approved always-visible Grid/List selection checkboxes, **Export from Images and checked-image export from Connection** through the shared Connection workflow, with a single-row contextual gallery toolbar and short **Export** / **Delete** button labels (Delete opens the full Recycle Bin confirmation), cross-filter selection with visible hidden counts, partial Select all filtered up to 10,000 checked IDs, and clearly confirmed Windows Recycle Bin-only removal. The [implementation subplan/task list](plans/2026-10-09-phase-8e-implementation.md) tracks selection, UI, shared export, durable availability/recycle service, reconciliation and owner acceptance. [Owner spec/mockups](plans/examples/phase-8e/owner-multiselect-ux-spec.md) are retained with explicit current-contract overrides. PR #57 now contains **unverified source implementation** of session-only checked-ID state, Grid/List checkboxes and Detail toggle, a responsive single-row gallery toolbar and checked-image export handoff to Connection. The illustrative fixture mirrors selection without enabling export or deletion. The current branch also contains durable recycling/availability and original-file coordination. The [Qt system Trash follow-up](plans/2026-10-09-qt-system-trash.md) replaces handwritten Windows COM with an injected Qt 6.11.2 adapter, platform-aware UI and Qt capability-based per-file Trash handling without OS/filesystem allowlists. Source/static evidence only: actual recycling, recovery and Windows/Debian/macOS owner acceptance remain pending; full non-Windows app support is not certified. No agent tests/smoke, actual recycling, live generation or billable actions were performed.

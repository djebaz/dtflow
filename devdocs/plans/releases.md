# DTFlow release roadmap

**Status (2026-10-26):** Planned milestones; no release is created by this document. Releases through [5.3.0](https://github.com/djebaz/dtflow/releases/tag/v5.3.0) are published. The owner-selected [5.4.0 release](../releases/v5.4.0.md) is in preparation from merged main `32e174e`: prompt details/tags, keyboard submission, simpler wording, preview wheel zoom and independent thumbnail zoom. This does not declare provisional roadmap gates complete. Final checks, exact clean Windows executable acceptance and publication remain separate, with existing startup/progress, Logs/platform and focused persistence acceptance gaps still open. Follow the [release guide](../guides/releasing.md); older release plans remain historical records.
**Audience:** Personal use initially; public distribution is a separate later decision.  
**References:** [implementation phases](initial.md), [CLI plan](phase-3-cli.md), [TanqueStudio parity](tanquestudio-parity.md), [pending release notes](../releases/unreleased.md).

## Purpose and ownership

Implementation phases describe engineering work. Releases deliver independently usable, verified behavior. A release can combine phases or ship a bounded part of a phase; finishing every upstream feature is not a release prerequisite.

This document owns proposed release scope and acceptance gates. The initial plan owns engineering sequence, the parity matrix tracks intended features, and versioned release notes record what actually shipped. Keep them consistent in implementation PRs. The version numbers below are targets, not current application versions, dates or promises of backend compatibility.

Release acceptance consumes the authoritative scenario groups and evidence owned by the implementation phases; it does not create a second test suite simply because the same behavior is now exposed through a CLI, Qt surface, planner, workflow or packaged executable. Packaging/live checks prove packaging, deployed compatibility and full composition only. They do not replace deterministic lower-layer tests or require those matrices to be rerun through a later presentation layer.

Phase 2 is an internal foundation milestone rather than a user release: contract, fake, real adapters, protocol fixtures, target-Windows packaging investigation and dependency-distribution compliance review. Preserve source-run development support throughout.

## Proposed usable releases

| Target | Engineering gate | Independently usable behavior | Delivery |
| --- | --- | --- | --- |
| 0.1.0 | Phase 3A | Select backend; check connectivity; discover models; resolve/import generation configuration; select bounded LoRAs with weights/preflight; generate a single request; save final artifacts and reproducibility metadata; inspect failures and attempts | Private tagged source-run CLI |
| 0.2.0 | Phase 3B | Create/list/control durable queues; rolling cloud dispatch with configurable maximum, default four; one tested local lane; Pause/Resume/Cancel; paused restart and honest uncertain outcomes; upload/reuse supported custom cloud LoRAs with durable receipts | Private tagged source-run CLI |
| 0.3.0 | Phase 4 plus 5A/5B | Complete Qt generation and queue paths; secure credential entry/storage; basic configuration save/load; bounded verified LoRA selections, compatibility diagnostics and cloud upload/known-receipt reuse; discreet version/commit identity | First useful portable Windows Qt executable |
| 0.4.0 | Phase 6 plus Phase 8 | Plan bounded batches; manage reusable presets; browse results and provenance; reuse generation settings; inspect and clean disposable cache | Desktop refinement for daily use |
| 0.5.0 | Initial Phase 9A gate | Verified image-to-image path through shared services and CLI/Qt, including explicit input preparation and effective parameters | First image-editing release |
| 0.6.0, provisionally | Initial Phase 10A gate | Validate/inspect/execute supported workflow files with durable provenance and truthful restart limits | First workflow execution release |
| Later 0.x | Phase 9B/9C, 10B or verified Phase 11 slices | Existing-file inpainting, interactive masks, selected controls, visual workflow authoring, or verified cloud/media capabilities | Independently scoped feature releases |
| 1.0.0 | Agreed feature scope plus reliability gate | Stable documented behavior for the workflows the owner actually uses, with verified packaging, recovery and upgrade paths | Stable release; public distribution only if separately chosen |

0.3.0 is the first useful desktop release; 0.4.0 refines repeated daily use. Do not defer the basic Qt queue panel until batch planning or gallery work.

The later numbers are provisional. Insert additional minor releases when a useful image or workflow slice is ready, and renumber later targets accordingly. Use patch releases for fixes within an existing scope; do not force new feature delivery into a fixed patch-number sequence. Prerelease builds can identify incomplete validation, but must disclose what is usable and what remains unverified.

Phase 7 advanced priorities, fairness or adaptive scheduling is not a mandatory barrier for any release. Basic rolling dispatch is already required in 0.2.0.

## Release-specific acceptance

### 0.1.0 — Single-generation CLI

Consume the [Phase 3A authoritative scenario groups](phase-3-cli.md#phase-3a-scenario-groups) and applicable existing Phase 2 evidence. Do not define a second wire/token/codec/persistence matrix at release time. The implemented configuration, CLI, bounded LoRA/preflight and logging scope must pass its mapped acceptance.

Record the corresponding live workflow for every real backend advertised as usable: 3-LG1 for local Draw Things and 3-LG2 for direct DT+. Workflows are small, individually selectable when implemented and separately authorized; diagnostic-probe evidence alone does not validate the release adapter. Retain source/build identity, supported combinations and limits. Source CLI success does not establish executable packaging.

### 0.2.0 — Durable rolling queues

Consume the [Phase 3B authoritative scenario groups](phase-3-cli.md#phase-3b-scenario-groups), including deterministic 10/4 rolling dispatch, controls, uncertainty/restart (override removed by #132), actual dispatcher policies, ownership, asset preparation/receipts/reuse and applicable schema compatibility. No ambiguous attempt becomes eligible through Resume or an upgrade. Existing base attempt/backend evidence is reused only for the same implementation path.

Record separately authorized 3-LG3 small cloud-queue composition and 3-LG4 supported custom LoRA preparation/reuse/use. Live evidence does not replace deterministic reservation, failure, ownership or migration tests. No usable remote list API is established; inspect known receipts without claiming complete account inventory. Conversion/editor and broader asset management remain outside this gate.

Introduce saved-state versioning and the compatibility contract below before declaring the durable release usable. Historical migration fixtures are required only for real supported released schemas that need migration, not invented upgrade versions. Release acceptance consumes the mapped tests and recorded live evidence; it does not create a duplicate suite.

### 0.3.0 — Complete desktop path

Consume the [Phase 4 testing ownership](initial.md#phase-4-testing-ownership) and [Phase 5 testing ownership](initial.md#phase-5-testing-ownership) scenario groups together with the existing Phase 2/3 authoritative evidence. Phase 4/5 tests own Qt shell, credential-platform wiring, presentation and service composition; they do not rerun backend protocol, request-resolution, persistence or scheduler matrices through widgets.

Phase 4 supplies the shell and secure connection setup. Phase 5A supplies the generation form, actual progress, output/error presentation, a bounded verified LoRA scope, shared compatibility diagnostics, cloud upload/known-receipt reuse and basic configuration save/load. Phase 5B supplies queue creation/inspection, item status, concurrency, Pause/Resume/Cancel and restart/uncertainty presentation through existing Phase 3B services.

Basic configuration reuse should already have a shared versioned import/export contract in Phase 3A; the Qt save/load surface belongs here. The richer preset library/browser remains 0.4.0. Saved configuration must not contain credentials.

Exercise the exact portable executable on the owner's Windows 10 / Intel Pentium E6500 machine: startup, native codecs, TLS/certificate resources, Qt plugins, credential storage, per-user paths, logging, build identity and responsive background execution. The focused Windows Credential Manager check establishes the real platform-storage boundary; generic credential precedence remains owned by shared services.

For every real backend advertised in the desktop release, record the corresponding small live desktop-composition workflow: 5-LG1 for local Draw Things and 5-LG2 for direct DT+. These prove Qt → shared services → real backend → durable result → UI presentation. Do not repeat the Phase 3 10/4 queue matrix through Qt; Phase 5B controlled queue-state scenarios prove the desktop surface while Phase 3B remains authoritative for scheduling/restart semantics.

Limit LoRA acceptance to explicitly verified combinations; do not require universal compatibility to release. Document unsupported combinations and unverified conversion/listing/deletion operations. The agreed bounded custom upload/reuse path already passes the Phase 3B CLI gate; Qt reuses it.

### 0.4.0 — Daily-use refinement

Consume the Phase 6 planner and Phase 8 gallery/preset scenario groups from the [implementation plan](initial.md). Validate bounded pair/cross expansion, stable ordering, explicit resolved requests and mismatch/invalid-value handling before enqueueing. Phase 6 (planner, `queue add --plan/--dry` and the Queues page Plan area) is implemented offline. Phase 8A saved-output browsing is now implemented: PR #33 established the Grid/List/Detail presentation and PR #34 connected it to committed outputs with shared read-only query/detail services, CLI inspection, durable viewer state, bounded asynchronous loading and refresh. PR #46 implements preset interchange (8B), saved-output reuse/explicit regeneration (8C) and category-specific disposable-cache management (8D); these remain subject to owner/runtime acceptance and the release validation below. Organisation is separately scoped. Keep the established Phase 3 scheduler and no-replay behavior authoritative rather than rerunning it through every expanded batch.

Validate preset round trips, gallery provenance, metadata reuse, explicit regeneration and cache cleanup through local fixtures/services. Cache cleanup must preserve durable outputs, queue records, credentials and model assets. Basic bounded logging and cache separation remain requirements of earlier releases. No new live generation is required merely because batching, gallery or presets are usable; separately authorize a live run only when a new composition boundary genuinely needs inference evidence.

The separately scoped [JPEG export follow-up](2026-10-09-jpeg-image-export.md)
provides Connection-centered exports, flat when advisory noise/blank sorting is disabled. The default-on sorting follow-up additionally requires owner acceptance of routing, sidecars, persisted choices and false-positive review. Owner acceptance must check Windows
Properties, privacy, collisions, filter snapshots/cancellation and untouched originals
before claiming this feature accepted; it does not require new live generation.

### 0.5.0 — First image-to-image release

Consume the Phase 9A image-preparation, operation-specific backend-mapping and application-composition groups from the [implementation plan](initial.md). Reuse common seed/sampler/auth/TLS/stream/output behavior when the same implementation path executes; 9A tests only the new img2img content and preparation semantics.

Record 9A-LG1 for every backend/model combination actually advertised as supported. Keep the live matrix intentionally bounded: request expressibility does not prove model compatibility, but the release does not need a Cartesian sweep of every available model, resolution or strength value.

### 0.6.0 — First workflow execution release

Consume the Phase 10A compiler/preflight and deterministic execution-engine scenario groups from the [implementation plan](initial.md). Compiler/parser variants stay cheap and parametrized; restart, uncertainty, loop bounds and state isolation remain deterministic offline engine evidence.

If the released workflow step set includes real generation, record one bounded 10A-LG1 workflow per materially distinct advertised backend composition that needs deployed evidence. Live inference does not own loop, restart, no-replay or uncertainty semantics, and a full visual editor is not a prerequisite for this release.

### Later incremental image, workflow and cloud releases

Phase 9B delivers inpainting using an existing mask file with explicit polarity and preparation. Phase 9C adds interactive painting and selected control/reference combinations as separate bounded slices; these need not all ship together. Consume each slice's operation-specific authoritative groups and selected live compatibility evidence without repeating common transport/authentication matrices.

Phase 10B adds visual authoring and additional proven semantics incrementally. Its Qt tests own graph editing, serialization/loading and diagnostic presentation; the Phase 10A compiler/execution engine remains authoritative for workflow semantics.

Phase 11 capabilities follow the same delta rule: focused tests for genuinely new cloud protocol/policy behavior, one application-composition scenario, and a separately named live workflow only when real deployed compatibility is needed. PAYG or any other potentially billable capability requires explicit opt-in and a specifically selected live workload; generic live generation must never silently broaden into paid acceptance work.

Each slice records its supported operations, backend/model combinations, input/output semantics and actual validation evidence. Request expressibility or model inventory alone does not establish compatibility. Preserve all intended TanqueStudio features in the parity matrix, including blocked or conditional items.

Bounded custom cloud LoRA upload/reuse is required in 0.2.0, using a verified converted-file contract; broader asset management, conversion and video/audio releases require additional verified service contracts and metadata semantics. PAYG remains outside the first release scope; adding it later requires a separate explicit policy.

## Saved-state compatibility from 0.2.0

Keep application version, build identity and storage schema versions distinct. Version settings, generation configurations, queue/job/attempt records, cloud asset receipts and, when introduced, presets and workflow files explicitly. Do not use the application version as the sole schema discriminator.

Before opening older durable state for writing, determine whether it is supported. Migrate supported versions through an atomic/transactional path, preserving a recoverable pre-migration copy of affected durable records. Preserve artifact files and references; do not duplicate large outputs unnecessarily or include secrets in ordinary backups.

If migration fails or the schema is unsupported/newer, fail clearly without partially overwriting the original state. Do not silently reset queues, discard settings or submit work. Downgrades must reject unsupported newer state rather than corrupt it.

Migration preserves stable IDs, queue order, completed outputs, cancellation state, unresolved reservations and possible remote execution. Restored queues stay paused. No migration may convert an ambiguous attempt into a never-submitted eligible item.

Validate upgrades using saved fixtures from previously released supported schemas, including unknown outcomes, completed artifacts and failed migration. Each release declares its supported upgrade sources, backup/recovery procedure and any unsupported downgrade path. Successful schema migration never implies remote reattachment.

## Release evidence and identity

For every release, record:

- application version, full source commit, clean/dirty status and dependency lock revision;
- exact shipped scope, verified backend/model combinations and known limitations;
- checks actually performed, environment/target machine and outcomes;
- separate offline, source-run, packaged and deployed-backend evidence;
- saved-state/schema compatibility and upgrade checks;
- artifact filenames/checksums when distributing builds, with matching bundled identity.

Keep manual downloads as the initial update strategy. CLI milestones are tagged source releases and do not require a packaged CLI. Qt build filenames follow the AGENTS.md version/short-commit convention; no Git installation is required at packaged runtime.

At release time, update the authoritative project version through the repository dependency workflow, move shipped entries from unreleased notes into `devdocs/releases/v<version>.md`, and tag the exact accepted release commit. Unshipped entries remain pending. This roadmap must not itself bump the project version, create tags or publish a release.

## 1.0 reliability gate

Agree the supported feature set before declaring 1.0. Require target-Windows executable verification, separate deployed-cloud evidence, responsive Qt execution, bounded resource use, safe output persistence, queue/restart/uncertainty handling, supported upgrade paths and documented limitations.

Full TanqueStudio parity is not the definition of 1.0. Keep outstanding parity work tracked beyond it. Public distribution additionally needs GPL-3.0-only packaging compliance, corresponding-source/notice handling and suitable release documentation; personal acceptance does not establish those conditions.

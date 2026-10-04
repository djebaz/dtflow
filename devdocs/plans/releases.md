# DTFlow release roadmap

**Status:** Planned milestones; no release is created by this document.  
**Audience:** Personal use initially; public distribution is a separate later decision.  
**References:** [implementation phases](initial.md), [CLI plan](phase-3-cli.md), [TanqueStudio parity](tanquestudio-parity.md), [pending release notes](../releases/unreleased.md).

## Purpose and ownership

Implementation phases describe engineering work. Releases deliver independently usable, verified behavior. A release can combine phases or ship a bounded part of a phase; finishing every upstream feature is not a release prerequisite.

This document owns proposed release scope and acceptance gates. The initial plan owns engineering sequence, the parity matrix tracks intended features, and versioned release notes record what actually shipped. Keep them consistent in implementation PRs. The version numbers below are targets, not current application versions, dates or promises of backend compatibility.

Phase 2 is an internal foundation milestone rather than a user release: contract, fake, real adapters, protocol fixtures, target-Windows packaging investigation and dependency-distribution decision. Preserve source-run development support throughout.

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

Pass Phase 3A offline acceptance through actual services/adapters: configuration validation, parameter/wire fidelity, token refresh, selected credential behavior, terminal errors, previews, final artifacts, atomic attempt records and logging. Startup/help/version output must work without Qt. Include structured local model/LoRA discovery, exact LoRA identifiers/weights, match/mismatch/unknown diagnostics and the bounded generation subset under [the LoRA asset plan](lora-assets.md).

For each real backend advertised as usable, record a focused actual end-to-end smoke result separately from fixture coverage. Direct-cloud evidence must exercise the release implementation rather than rely solely on the diagnostic probe. Live generation remains small, separately gated and explicitly authorized; it is not part of ordinary tests.

Retain version/source identity and limitations with the evidence. A working source CLI does not establish executable packaging.

### 0.2.0 — Durable rolling queues

Pass Phase 3B acceptance through CLI commands and shared services. With ten independent items and four slots, controllable out-of-order completion must prove rolling refill before all initial items finish, bounded reservation counts and stable item/result identity.

Cover paused restart, completed output preservation, pre-submission failures, uncertain remote outcomes, explicit override, pending cancellation, running interruption, maximum reduction and exclusive dispatch ownership. No ambiguous attempt may become eligible through queue Resume or an upgrade.

Record focused real-adapter/cloud smoke evidence for the advertised queue path separately from deterministic offline scheduling tests. Fixtures establish application scheduling behavior; they do not prove cloud entitlement, remote cancellation or billing behavior.

Custom cloud LoRA upload/reuse is also a 0.2.0 gate before Qt. Pass source-backed metadata POST/signed PUT/already-existing fixtures, durable receipt/restart behavior, unknown-outcome handling and queued reference fidelity. Record separately authorized deployed upload plus generation evidence for the advertised converted-file subset. No usable remote list API is established; inspect locally known receipts without claiming complete cloud inventory. Conversion/editor and broader asset management are outside this gate.

Introduce saved-state schema versioning and the compatibility contract below before declaring this durable release usable.

### 0.3.0 — Complete desktop path

Phase 4 supplies the shell and secure connection setup. Phase 5A supplies the generation form, actual progress, output/error presentation, a bounded verified LoRA scope, shared compatibility diagnostics, cloud upload/known-receipt reuse and basic configuration save/load. Phase 5B supplies queue creation/inspection, item status, concurrency, Pause/Resume/Cancel and restart/uncertainty presentation through existing Phase 3B services.

Basic configuration reuse should already have a shared versioned import/export contract in Phase 3A; the Qt save/load surface belongs here. The richer preset library/browser remains 0.4.0. Saved configuration must not contain credentials.

Exercise the exact portable executable on the owner's Windows 10 / Intel Pentium E6500 machine: startup, native codecs, TLS/certificate resources, Qt plugins, credential storage, per-user paths, logging, build identity and responsive background execution. Record end-to-end generation/queue evidence for supported backends separately from packaging-only checks.

Limit LoRA acceptance to explicitly verified combinations; do not require universal compatibility to release. Document unsupported combinations and unverified conversion/listing/deletion operations. The agreed bounded custom upload/reuse path already passes the Phase 3B CLI gate; Qt reuses it.

### 0.4.0 — Daily-use refinement

Validate bounded pair/cross expansion, stable ordering, explicit resolved requests and mismatch/invalid-value handling before enqueueing. Keep the established scheduler and no-replay behavior.

Validate preset round trips, gallery provenance, metadata reuse and explicit regeneration. Cache cleanup must preserve durable outputs, queue records, credentials and model assets. Basic bounded logging and cache separation remain requirements of earlier releases.

### Incremental image and workflow releases

Phase 9A delivers verified image-to-image. Phase 9B delivers inpainting using an existing mask file with explicit polarity and preparation. Phase 9C adds interactive painting and selected control/reference combinations as separate bounded slices; these need not all ship together.

Phase 10A delivers a supported versioned workflow-file contract, preflight/plan inspection and execution through shared services. Begin with a bounded step set. Phase 10B adds visual authoring and additional proven execution semantics incrementally. A full visual editor is not a prerequisite for the first workflow-file release.

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

Full TanqueStudio parity is not the definition of 1.0. Keep outstanding parity work tracked beyond it. Public distribution additionally needs the dependency licensing/distribution decision and suitable release documentation; personal acceptance does not establish those conditions.

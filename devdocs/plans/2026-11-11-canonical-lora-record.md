# Canonical LoRA record — clean-start implementation (#370)

Date: 2026-11-11  
Status: **PR #378 canonical foundation; naming integration branch is source implementation, not yet Windows/DT+ accepted**  
Parent: [epic #375](https://github.com/djebaz/dtflow/issues/375)  
Design authority: [#373](https://github.com/djebaz/dtflow/issues/373)  
Naming research: [PR #377](https://github.com/djebaz/dtflow/pull/377)

## Intent and deliberate breaking change

The owner is the only pre-release tester and will manually delete the **entire** disposable `.dtflow` directory before using the new schema when needed. **No legacy `.dtflow` migration, backfill, alias recovery or format compatibility is required.** Never delete or reset state automatically. An existing incompatible `assets.sqlite3` must fail explicitly and leave its bytes intact.

Once created by the new implementation, canonical metadata, confirmed receipts, retained copies, and saved outputs must remain durable and protected against overwrite, truncation, credential leakage and accidental paid work. A fresh-state decision is **not** an excuse to weaken new-data integrity or account scoping.

The baseline includes merged [#371](https://github.com/djebaz/dtflow/pull/371) (exact eight-signature LoRA detection and refusal) and [#376](https://github.com/djebaz/dtflow/pull/376) (effective-item Queue receipt binding). Their behavior remains authoritative; do not create parallel preflight, Queue planning or provider negotiation logic.

## Ownership and schema

Use one canonical `lora_records` table in the existing `assets.sqlite3`, keyed by the **measured `(sha256, size)` pair**. This is a new fresh-state schema; do not migrate old databases. `assets`, `asset_operations` and `asset_key_receipts` keep their distinct purposes: concrete preparations, diagnostics and key/name-scoped remote references. The retained-file index keeps file bytes and source stamps, not an independent editable LoRA identity.

| Canonical field | Meaning and contract |
| --- | --- |
| `sha256`, `size` | Immutable local content identity, verified from bytes, not filename |
| `detected_version`, `detected_source` | File-derived exact Draw Things architecture or Unknown, preserving #371 evidence |
| `declared_version` | Explicit selected exact Draw Things architecture, independent of detection |
| `short_name` | Optional owner-edited portable 1–20-character name, or NULL until selected |
| `managed_identifier` | Nullable accepted technical filename, unique case-insensitively across content records; stable until explicit Re-Prepare |
| `format_code`, `format_source` | Selected `F16` or `Q8P`, and `converter_default`, `filename_hint` or `user` provenance; NULL until selected |
| `trigger_words` | Locally persisted optional text (empty is valid); remote storage is not implied |
| `updated_at_utc` | Local metadata update timestamp |

The original filename/path, generated managed technical filename, immutable hash/size and account-scoped receipt are **distinct** concepts. Existing receipt identifiers remain exact technical identities. Do not duplicate per-key receipts or source-stamp tracking inside `lora_records`; their established tables/index remain the references associated with the content key.

The effective architecture is `declared_version` when set, otherwise `detected_version`, otherwise Unknown. Keep file and user evidence inspectable separately and show conflicts. Never replace the detected value with a user assertion or borrow the selected checkpoint's architecture. Compatibility still compares **exact versions** at shared preflight; an explicit declaration does not prove file compatibility.

## Preparation and shared resolution

1. Measure and retain a LoRA with the existing `AssetService.prepare` path. Persist its detected evidence into the canonical content row in the **same local transaction** that starts the corresponding asset operation. Re-Prepare can update the detection without overwriting user metadata. Keep ordinary upload/reuse/receipt behavior.
2. Expose a local metadata-edit method on the existing `AssetRegistry`, keyed by verified SHA-256 and size, with clear validation and atomic updates. The method can record a user-declared exact version, optional short name, selected format/provenance and trigger text. It never uploads, renames assets, silently resets values or mutates another content hash.
3. One shared, batched **local** metadata resolver enriches prepared selections for Generate, Queues, Workflows and Scripts before existing exact-version preflight and account-scoped receipt verification. Preserve LoRA identifiers, weights, selection order and the [#376](https://github.com/djebaz/dtflow/pull/376) effective-item-first Queue plan. No fallback to filename or broad catalogue `base_model` for exact dispatch version.
4. The content-owned `lora_records.managed_identifier` is the accepted technical basename; `assets.identifier` and `asset_key_receipts.identifier` remain historical registration/account-scoped receipt names. Do not derive canonical naming authority from either receipt table or source-stamp index. A metadata-only edit never changes `managed_identifier`.
5. Catalogue/import/Echo metadata without **measured content identity** remain discoverable evidence, not automatically merged into content records by matching a filename. Keep broad catalogue families strictly for browsing.

## Managed naming and #377 / #373 boundary

For a **newly authorized managed preparation**, the single naming convention is:

```text
ShortName_FamilyTag_lora_format.ckpt
PortraitV2_Kr2_lora_f16.ckpt
```

The generated filename suffix uses lowercase `lora_f16` or `lora_q8p`, while stored format codes remain uppercase (`F16`, `Q8P`). Already accepted technical names and historical receipt identifiers retain their original spelling; this convention applies to newly allocated names.

Store `short_name` and `format_code` separately; `FamilyTag` is a derived shortcode from #373's approved 30-architecture reference, **never a provider version**. Reject invalid short names and unsupported format codes; never infer model architecture from name text. The managed basename is accepted in the same registry write transaction as the explicit Prepare intent, before any DT+ negotiation. A case-insensitive unique constraint refuses the same filename for distinct verified content. Reuse the existing identifier for identical content, preserving its exact spelling. Only explicitly authorized Re-Prepare may replace the accepted identifier; existing historical/account-scoped receipts are never rewritten. Existing original files outside DTFlow storage remain untouched. The signed-upload/content-hash provider contract does not establish remote rename support.

[PR #377](https://github.com/djebaz/dtflow/pull/377) has a **standalone, read-only POC**, not an implemented editor. Its suggested 1–20-character names, meaningful-word preservation, alternatives, confidence/review flags and format/architecture-scoped duplicate suggestions are **advisory only**. The updated #373 decision permits a usable suggestion to prefill the editable field; otherwise leave it blank. Do not recreate a separate naming algorithm in #370. Suggestions and numbered alternatives do not bypass collision checks: different content with the same final managed filename must be blocked pending the owner's edit/authorization.

**The #370/naming integration branch supplies the local naming service, canonical occupied-name snapshot, atomic managed identifier + intent, and optional explicit Prepare metadata handoff.** #373 still owns the actual batch editor, row lifecycle, labels, partial batch authorization, trigger-prefix toggles and optional DT+ metadata sync. The existing desktop file-picker path is not yet the approved batch editor.

## Local managed-copy naming follow-up (#383)

The owner approved service-side automatic first-time naming for **new and previously retained** LoRAs without a separate approval action (2026-11-11). This approval is **local naming only** and does not authorize changes to the still-design-only Prepare editor in PR #382. Existing managed identifiers remain stable; a later explicit Re-Prepare is required to change an accepted identity.

- During an ordinary already-authorized Prepare, the shared naming service proposes a short name, uses only the independently detected or saved user-declared exact architecture, and selects the stored F16/Q8P format or the approved filename hint/default. If all required information is available, the new managed identifier is accepted in the existing Prepare intent transaction before DT+ negotiation. An unknown architecture or unusable short-name proposal is not silently guessed.
- Previously retained files with no accepted managed identifier receive a **first local assignment** on asset/Connection refresh when their canonical content row has a reliable detected architecture or a user declaration. Proposals reuse the canonical occupied-name snapshot, preserve saved short names/formats, and pass the case-insensitive uniqueness check in a local SQLite writer transaction. This one-time assignment is not a migration between schema versions and requires no provider call or Prepare receipt.
- Newly prepared content is copied **once, directly under** the accepted `ShortName_Tag_lora_format.ckpt` filename after local naming, without first creating an original-named managed copy or a hard link. Only previously retained content needs a separate, content-verified local rename. `index.json` records one retained copy per content identity; filename collisions do not overwrite existing files. Existing receipts, account-scoped remote identifiers, original external filenames and source stamps do not change. Accepted names with mismatched physical copy names are aligned the same way.
- Unknown architecture, missing/weak name suggestion, conflicts with protected user edits and unsafe physical collisions remain unresolved for the later editor; no automatic overwrite or remote upload is attempted. Previously accepted names are never reallocated.

This is a deliberate, narrow exception to the rule that metadata-only edits do not rename assets: the automatic backfill assigns **only previously null** managed identifiers, then synchronizes the retained copy's local basename. The runtime Prepare UI and its authorization contract remain unchanged pending PR #382.

## Trigger and provider boundary

Save user trigger words locally. `override.loras[].prefix` transmission and independent Generate/Queue/Workflow/Script prompt-insertion settings remain #373 work; the existing wire override currently transmits an empty prefix. DT+ Cloud Asset metadata-write support is **unverified**. Never invent a field on `/get_byom_urls`, mutate signed PUTs, imply that remote metadata was saved, or block successful file preparation because optional remote sync is absent.

## Implementation and acceptance

- Use fresh-state asset schema 6 (one nullable `managed_identifier` on `lora_records` with `COLLATE NOCASE UNIQUE`); reject older schema versions with an actionable manual-reset message. Preserve no legacy migration/backfill code. Never automatically remove application data.
- Use the one canonical record for file-derived/declared architecture and optional metadata. Existing receipt operations and key-scoped references remain unchanged. A missing/Unknown version still refuses at #371's preflight.
- Adopt shared lookup for all four execution paths via their existing prepared-receipt loading/resolution seams, with no new remote calls or duplicate Queue planner.
- Check source-level invariants: same bytes under two names/keys share editable metadata but keep separate receipts; two distinct byte hashes never merge even if names match; user choice takes precedence while conflicts remain visible; metadata editing never renames, uploads or overwrites assets.
- Preserve all **new-schema** data and outputs, even across failures, and never switch backend, account or billing mode.
- Follow `AGENTS.md`: no development-agent tests or UI renders without explicit authorization. Static checks may be run; eligible GitHub CI begins only when the draft PR becomes Ready for review. Record CI and owner Windows/provider smoke separately.

**Intentional tradeoff:** existing disposable pre-release `.dtflow` state is incompatible and must be manually reset by the owner. This simplifies storage and maintenance while setting a strong persistence contract for all newly created state.

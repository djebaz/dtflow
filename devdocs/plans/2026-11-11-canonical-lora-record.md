# Canonical LoRA content record — #370 implementation design

Date: 2026-11-11  
Status: **Proposed for owner agreement before implementation**  
Workstream: [epic #375](https://github.com/djebaz/dtflow/issues/375); storage/read-path owner: [#370](https://github.com/djebaz/dtflow/issues/370); approved UX/dispatch contract: [#373](https://github.com/djebaz/dtflow/issues/373)

## Current baseline (verified in `main`)

- Merged [PR #371](https://github.com/djebaz/dtflow/pull/371) detects eight exact LoRA architecture signatures, persists `family`/`family_source` in the existing asset receipt, refreshes detection on explicit re-Prepare without a redundant upload, and blocks Unknown prepared versions or confirmed mismatches. The checkpoint's architecture is **not** a fallback.
- Merged [PR #376](https://github.com/djebaz/dtflow/pull/376) fixes Queue Pair/Cross and weight-sweep receipt binding: the **effective per-item LoRA selection** determines which existing receipts are attached. Keep that behavior, selected weights/order and backend/account scope unchanged.
- `persistence/assets.py` schema 4 has `assets`, `asset_operations` and `asset_key_receipts`. An `assets` row belongs to a backend/origin/content identity; remote references remain scoped by key and identifier. `services/lora_store.py` manages retained bytes and original-source stamps in `lora_files/index.json`. `services/catalogue.py` owns bundled/imported discovery entries, which can lack a measured content digest.
- None of these sources is currently a complete canonical record for user-selected architecture, short name, selected format and triggers. Do not mistake broad catalogue `base_model` values for exact provider versions.

## Decision proposed: extend the existing asset database

Use a **single additional `lora_records` table in `assets.sqlite3`**, keyed by immutable measured `(sha256, size)`, rather than a new database or parallel JSON metadata store. Increment the existing registry schema with an ordinary transactional migration. One measured content identity has one editable local LoRA metadata record, regardless of how many original filenames, backend origins or DT+ credentials reference the same bytes.

Fields owned by this record:

| Data | Rule |
| --- | --- |
| Content identity | SHA-256 and measured size; never inferred from a file name |
| `detected_version` and its source | Exact file-derived version from #371, or explicit Unknown; do not overwrite with user input |
| `declared_version` and its source | Optional **user-selected** exact version from #373, stored separately from detection |
| `short_name` | Optional persisted user-entered 1–20-character value; **blank by default** on a new file, never generated from its technical filename |
| `format_code` and provenance | Optional `F16` / `Q8P` selected format and source; the future Prepare editor applies its approved F16 default and Q8P filename hint, without claiming byte-level verification |
| `trigger_words` | Optional persisted local user text; not evidence of remote Cloud Asset storage |
| Known names/aliases | Preserve exact observed source names and managed/remote technical identifiers, without renaming them or taking ownership of another content hash |
| Update provenance | Timestamps for local metadata edits, not provider outcome/credit accounting |

The table owns *user metadata and content evidence*, **not** provider receipts or the physical source index. Keep existing `assets`, `asset_key_receipts`, attempts/history and `LoraStore` storage as specialized references to the canonical content identity. Do not copy credentials, signed URLs, raw prompts or provider-internal attempt details into this metadata row.

## Identity and read path

1. On Prepare, use the existing measured SHA-256/size and detected exact version to create/enrich the content record **before/alongside existing receipt orchestration**. Re-Prepare may refresh file evidence; it must not reset declared architecture, user short name, format choice or triggers.
2. For existing assets, backfill a content record from the existing `assets` rows in an idempotent SQLite migration (or first writer-open enrichment). Preserve all receipt IDs, backend/origin rows, opaque credential buckets, remote IDs, prepared identifiers and timestamps. When old evidence is broad or conflicts, preserve it as provenance rather than silently declaring a new exact version.
3. For retained copies, lazily join `LoraStore` by verified content hash and size to learn original aliases/source stamps; do **not** re-upload, call the provider, rewrite retained files or manufacture missing sources. A deleted local copy must not erase a confirmed historical receipt or its metadata.
4. Catalogue/import and provider inventory entries without a **verified content hash** remain discoverable *unbound candidates*. They may enrich a canonical record only once their identity is proven; name equality alone must not merge two different LoRA contents. Exact provider metadata is distinct from broader catalogue browsing families.
5. Add one shared local resolver (in the existing asset/LoRA service boundary) for `identifier → measured content → effective exact version + provenance → applicable active-account receipt`. Reuse the current scoped receipt validation; do not make metadata lookup itself perform network or paid work. Batch lookups for Queue/Workflow/Script selections so an item does not repeatedly scan every source. No second receipt planner.
6. Effective version for dispatch: **explicit user declaration if present, else confidently detected exact file version, else Unknown**. Return both sources and any conflict for diagnostics/UI. Never relabel a declaration as detected evidence, silently borrow checkpoint version, or treat an unverified architecture as LoRA-supported.
7. Preserve execution ownership: Generate, Queue, Workflow and Script continue storing and submitting **exact technical identifiers and weights**, not short labels. #376's effective-item-first Queue binding stays authoritative. Alias ambiguity must fail clearly instead of binding by guessed name or shifting to another account.

## #373 and provider boundaries

- **#370 implements the durable record, migration and shared read path**. #373 owns the single batch Prepare editor, mandatory user-entered short names, supported-architecture dropdown, display shortcodes, managed-filename preview/collision UX, selected-first picker ordering and four independent trigger-prefix settings.
- The approved managed technical name applies only to a **new, explicitly authorized** preparation. Metadata-only edits of an existing content record never silently rename a prepared remote asset, rewrite saved jobs or trigger an upload. If a candidate filename belongs to different bytes, #373 asks for a new short name (no automatic hash/number suffix).
- Preserve trigger text locally now; the eventual generation override uses the saved value in `override.loras[].prefix` only after verifying nonempty-prefix semantics. Prepare-time DT+ Cloud Asset trigger metadata write is **conditional on a verified API**; missing/failed optional sync never blocks a successful Prepare or causes a user-facing warning. Do not invent a `/get_byom_urls` metadata field.
- The 30 #373 shortcodes are **presentation tokens**, never DT+ `version` values. The selectable architecture subset requires independent evidence of LoRA support.

## Data integrity, migration and checks

- Add schema upgrade with idempotent, transactional backfill; reopening a migrated database must not duplicate content rows. Existing unknown/broad receipts remain usable under the #371 refusal/explicit re-Prepare rules. Retain per-key isolation and ensure data are not overwritten across origins sharing identical content.
- Prove that two names/keys with the same bytes resolve one metadata record but keep their individual identifiers/receipts; two different hashes with the same name remain distinct. Show a file/user architecture disagreement without erasing detection, with explicit user choice effective for dispatch.
- Preserve saved queue templates, Pair/Cross combinations, Queue item weights and receipts (#376), workflows, scripts and image history. A failed local metadata save must leave the previous record intact and cannot affect remote asset state.
- Respect repository `AGENTS.md`: **no development-agent test creation/execution or UI renders** without owner override. Use static code review, eligible GitHub CI once Ready for review and separately reported owner Windows/DT+ smoke. Do not claim provider-side validation from CI or source inspection.

## Implementation sequence after agreement

1. **Persistence:** transactional `assets.sqlite3` schema upgrade, content record CRUD/read, provenance and safe legacy backfill. No UI or provider behavior change in this step.
2. **Shared resolution:** enrich at Prepare and merge local catalogue/inventory evidence only with verified identity; resolve detected vs user-declared versions and active-account receipts through one read path, preserving #376.
3. **Consumer adoption:** replace overlapping name/family/receipt lookup paths incrementally in Generate, Queue, Workflow, Script and relevant UI displays. Keep existing technical identities. Expose metadata fields to #373 without implementing its new editor or provider trigger synchronization here.
4. **Acceptance:** verify original data survival, conflicting aliases, Unknown/manual declaration, per-key receipt separation, unchanged Queue effective selection and no silent paid operations; record CI and owner Windows outcomes separately.

This design is intentionally a **proposal**, not evidence that #370 has been implemented. The owner-approved UX remains in #373; agree the storage/read-path shape before changing production code.

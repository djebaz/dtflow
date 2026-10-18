# Runtime and test evidence layout

The disposable browsing publication is `<state>/cache/derived/gallery_snapshot.sqlite3` (independent snapshot schema 1). It projects committed gallery metadata and removal availability for read-only Windows/WSL fallback, not execution or mutation authority. Session completion and clean close request publication only when changed or missing; Derived cleanup may remove it and `--no-cache` bypasses it. It contains private saved prompts/settings/paths, so never attach it as a public diagnostic log. See [publication, concurrency and pending acceptance](gallery-snapshot.md).

## Ownership and current implementation

Development application state uses `.dtflow/attempts.sqlite3` and final outputs use `.dtflow/outputs/<attempt-id>/`. Output placement is backend-neutral; metadata and durable records identify the backend. Each attempt groups its image(s) and metadata. Outputs are durable artifacts, not disposable cache. The independent durable asset registry is `<state>/assets.sqlite3` (schema version 1, WAL, `synchronous=FULL`) with an exclusive `assets.lock` held only by `assets prepare` and `assets reconcile`; `assets list|show` read without a lock. It holds local receipts and unresolved/unknown preparation operations (checksum, size, backend, API origin, server reference, state, sanitized evidence code, timestamps) and never credentials, credential fingerprints, signed URLs or file contents. It is durable state, not cache: cache cleanup and `-nc` never touch it, newer schemas are refused without reset, and an interrupted `intent`/`transferring` operation becomes explicit `unknown` the next time a writer opens it. A receipt is only a local candidate; every new scope must pass a fresh metadata check before reuse. The state directory's `owner.lock` is held per operation by the shared session service (`services/session.py`): a generation takes it for its duration, a queue dispatch (CLI `queue start` or the desktop Start) takes it for the whole run while queue reads and posted controls take none, inventory refresh and connection checks take none, and the desktop never holds it between operations. A second owner fails with an explicit "state is busy" outcome before anything is dispatched. SQLite manages its WAL/SHM sidecars in the database directory. Since queue schema version 2 the same database also holds queues, items, posted controls and queue events. The schema-3 writer now upgrades v1/v2 request JSON to explicit request v2 under one transaction with existing no-replay reconciliation, first writing a fresh consistent `attempts.pre-v3-from-v<old>-<uuid>.sqlite3` backup beside it. Old output files and metadata remain unchanged; read-only gallery supports DB v1/v2/v3 and queue listing v2/v3. See [image-operation schemas](image-operations.md#request-and-execution-schema-source-implementation) for upgrade/refusal and owner acceptance boundaries.

Explicit live tests retain a separate repository-root `test-runs/<run-id>/`, ignored by Git. A run ID combines UTC time and a random suffix. Local/cloud checks share that root; generation uses its own SQLite state and output root. Ordinary offline tests use temporary directories, never the application root. Live-test evidence is retained until deliberately removed; application cache cleanup does not touch it.

| Current live-run path | Contents |
| --- | --- |
| `test-runs/<run-id>/inventory/local_inventory.json` | Local Echo inventory snapshot |
| `test-runs/<run-id>/inventory/cloud_inventory.json` | DT+ inventory snapshot |
| `test-runs/<run-id>/inventory/lora_inventory.json` | Derived local/cloud LoRA availability evidence |
| `test-runs/<run-id>/state/attempts.sqlite3` | Durable live generation attempts, including unknown outcomes |
| `test-runs/<run-id>/outputs/<attempt-id>/final-1.png` | Validated final output |
| `test-runs/<run-id>/outputs/<attempt-id>/metadata.json` | Reproducibility metadata |
| `test-runs/<run-id>/results.json` | One build/check summary, including failed check outcomes |

Fixed names distinguish the current single local and single cloud connection without another directory level. Inventory snapshots contain endpoint, observed server identity, observation/provenance fields and `cache_version`. Neither a LAN IP nor Echo's server identifier is established as a permanent device/account identity; keep these values inside JSON rather than using them as filename keys. The terminal summary prints the run directory. No API keys, tokens or authentication blobs are written. A forced process termination can leave a partial run without `results.json`; saved artifacts and attempt state remain evidence, not proof that every check completed.

## Planned application cache, logs and packaged paths

The derived LoRA cache writer is implemented. A successful `dtflow models` or desktop Refresh inventory also writes the raw local or cloud snapshot (`persistence/inventory_cache.py`); the desktop restores it on startup and provider returns when no matching in-run observation exists, marked cached, and generation always refreshes live. InventoryDisplayMemory in services/session.py retains at most one last-successful display snapshot per provider, checks the resolved local endpoint or selected cloud credential source/key before reuse, and keeps only a process-salted credential identity digest in memory. Failed refreshes never replace those observations; failed or obsolete completions cannot overwrite the current selectors. The UI preserves model/LoRA counts and original observed-at timestamps alongside loading/error status. These presentation observations are not generation/preflight authorization. No-cache skips persistent reads/writes; retaining the current run's observed presentation history does not write cache files. A read is a miss, never an error, when the file is missing, unreadable, another `cache_version`, another endpoint or, for cloud, another credential source or key (the file holds the source and a salted SHA-256 of the key, never the key). `-nc/--no-cache` (also on `dtflow gui`) disables reads and writes. Retained live checks write all three inventory files under `test-runs/`. Use the same filenames in development application cache, packaged application cache and test/scratch roots when inventory snapshots are saved. Create optional folders only on demand:

| Development path | Ownership |
| --- | --- |
| `.dtflow/cache/inventory/local_inventory.json` | Disposable local inventory cache |
| `.dtflow/cache/inventory/cloud_inventory.json` | Disposable cloud inventory cache |
| `.dtflow/cache/inventory/lora_inventory.json` | Derived local-gRPC + DT+ LoRA availability cache; rebuildable from live evidence |
| `.dtflow/cache/thumbnails/<shard>/<identity-hash>.<fingerprint-hash>.{jpg,webp}` | Regenerable 600 px gallery thumbnail, with a best-effort pruning target of 20,000 entries; fingerprint hashes original size/mtime/file ID, side and selected format; JPEG 85 default, developer WebP 80 override, alpha-channel fallback to WebP |
| `.dtflow/cache/derived/image_analysis.json` | Implemented disposable source-only checksum/advisory pixel-analysis cache, reused across export destinations; bounded/versioned, no prompts, source paths or destination records; included in Derived cleanup and no-cache |
| `.dtflow/cache/derived/<artifact-id>_<transformation-id>.png` | Regenerable derived asset |
| `logs/dtflow-<session>.log` | Rotating application diagnostics, one file set per process; the newest 20 sessions are kept |
| `.dtflow/settings.json` | Versioned, credential-free shared settings (currently the cloud concurrency maximum); durable, not cache |
| `.dtflow/last_session.json` | Versioned, credential-free last desktop session (provider, local endpoint, Generate form incl. prompts, plus each provider's own fields under `forms`: model, seed, size, steps, guidance, sampler, LoRAs, catalogue domain/base-family/Niche preference and bound cloud receipts for fake, local and cloud separately); durable, written on submit, successful connection check and quit; ignored if unreadable or a newer schema; `-nc` and cache cleanup never touch it |
| `.dtflow/export_preferences.json` | Schema-2 durable export folder per runtime (Windows, WSL, platform) and shared prompt/JSON-sidecar/routing preferences; reads schema 1; no credentials; independent of cache and no-cache |
| `.dtflow/presets/custom_configs.json` | Durable Draw Things-compatible named preset library; native configuration fields are preserved for re-export, while DTFlow applies only supported verified mappings; never cache-cleaned |
| `.dtflow/catalogue/custom_loras.json` | Optional user-owned normalized custom-LoRA metadata (exact file, base-model family, modes and optional display/prefix fields); durable, credential-free and independent of bundled catalogue updates; availability still comes from the active provider inventory; `-nc` and cache cleanup never touch it |
| `logs/tests/<run-id>.log` | Optional live-run diagnostics when implemented |
| `tmp/<operation-id>/` | Disposable operation scratch; no predetermined backend categories |

Do not duplicate run summaries under `reports/`, create a second `run.json`, or separate output roots by backend. Packaged Windows defaults to `%LOCALAPPDATA%\DTFlow\` with `attempts.sqlite3`, `outputs/`, `cache/`, `logs/` and `tmp/` beneath that root when those features exist. Development `test-runs/` is not part of packaged application state. Cache/log cleanup preserves durable records, outputs, credentials and model assets.

For the current single-local/single-cloud setup, no endpoint/pair hash is needed in filenames. Temporary snapshots use an operation-owned `inventory/` directory with the same names; do not create a shared scratch inventory that mixes operations. Future cache readers must check connection configuration and freshness, refresh/invalidate on endpoint or credential changes, and never reuse account-dependent BYOM evidence across changed credentials. There is no verified stable cloud account ID in the inspected API contract. Multiple saved connections may later use explicit application connection-profile IDs; do not infer permanent identity from IPs or server identifiers.

Derived LoRA inventory stores `resolved_at_utc` separately from each source's original `observed_at_utc`, backend/server/endpoint observations, refreshed flag and stale diagnostics. Resolution does not make cached evidence fresh. A newer negative `FilesExist` observation reports local absence even if the older Echo snapshot listed it. The application cache writer uses a unique same-directory temporary file before atomic replacement, so simultaneous writers cannot share a staging filename.

## Cache format version

Use an integer `CACHE_VERSION = 2`, written as `cache_version` in inventory JSON. Application inventory persistence owns this constant and the live evidence writer imports it rather than maintaining an independent copy. Increment it when that format changes incompatibly, not on every release or inventory refresh. It is independent of application version, run identity and SQLite schema version. Version 2 adds raw endpoint metadata and replaces the derived inventory's single observation timestamp with resolution time plus source provenance.

Future disposable-cache readers must treat a missing or mismatched version as a cache miss and fetch a fresh inventory. No conversion, legacy folder search or old-tree fallback is required. Retained live snapshots are historical evidence and are not rewritten on a version change. Existing old-layout files are neither read, moved nor deleted.

## Legacy Windows references viewed from WSL 1

The database and metadata retain original paths unchanged. `services/artifact_paths.py`
discovers bounded `/proc/self/mountinfo` snapshots on Microsoft WSL kernels and
recognizes only whole-drive `drvfs` mounts with drive-root sources. No `/mnt/c`
assumption, `wslpath` subprocess, basename search or reverse Linux-to-Windows
translation is used. Multiple mount roots for one drive, redirected components,
traversal, device/UNC/drive-relative paths and missing mappings are refused.
Arbitrary `9p`, bind and network mounts are not inferred as drive mappings.

Gallery models carry optional local access aliases separately from authoritative `path` and `metadata_path`. Queries discover once per worker page; image decodes and metadata reads refresh the foreign-path mapping at the read boundary. No process-global mount cache persists across refreshes. Metadata artifact matching still compares the exact stored path, never the alias. Thumbnail identity keys include the access alias so changing mounts invalidates retained presentation. Copy path(s), Open folder and external viewing use local read paths; provenance inspection continues to show the stored reference. Foreign Windows items disable edit/use/regenerate and export refuses foreign Windows sources before reading/encoding. Trash retains its native-path/root/ownership checks. Explicit permanent mode has a separate reviewed mutation boundary; read aliases alone never authorize destruction.

WSL shared-drive original-file and removal-batch leases now use the narrow source-only native Windows helper because focused probes showed `msvcrt` and Linux `flock`/`lockf` do not exclude each other. Missing checkout Windows `.venv` or unsupported mount/helper support refuses shared access, not a Linux-only lock fallback. Native root filesystems keep POSIX leases. This is not general cross-platform SQLite/generation/state-owner concurrency support; avoid concurrent Windows/WSL generation/state writes. Portable-reference migration and external-root mappings remain separate work.

`image_recycle.sqlite3` schema 2 preserves schema-1 records and adds permanent intent/outcomes without altering attempt metadata/history. Old applications reject the upgraded ledger. Environment-specific `image_removal.<environment>.json` files (schema `dtflow.image-removal`, version 1) contain only mode preferences, not consent/identities; cache cleanup and `--no-cache` do not own them. See [removal architecture](image-recycling.md) for exact eligibility, helper, uncertainty and no-replay boundaries. Source presence and lock-only probes do not establish removal runtime acceptance.

## User-selected image exports

Connection's middle-panel export hub copies committed outputs into one
user-selected folder, independent of `.dtflow/outputs/<attempt-id>/`. JPGs and
optional JSONs use that folder root except the optional advisory noise/blank sorting below. No destination index is written; full output-ID filenames supply transient membership, with EXIF fallback for legacy/renamed files. Export never moves originals or changes
durable references; cache cleanup does not own these user files. See
[image export architecture](image-export.md) for metadata/privacy, bounded snapshots,
collision safety and cancellation; runtime/packaged acceptance remains separate.

Noise/blank export routing is a default-on advisory option: flagged copies and matching sidecars go only into noise/ or blank/ below the chosen export folder; normal copies stay at root. Disable it for flat export. Original attempt outputs remain untouched.

## Disposable-cache coordination

`<state>/cache.lock` is the shared OS-lock coordination file for cache readers/writers and cleanup. It is outside `cache/` categories, not durable generation ownership, and must not be deleted to clear active ownership. Inventory/image-analysis persistence, export computation/checkpoints and bounded category measurement/purge use the common nonblocking lease. No-cache export bypasses persistent cache; busy cache export falls back to uncached work. See [cache-management architecture](cache-management.md) for redirected-path refusal, partial-failure semantics and acceptance boundaries.


### Thumbnail cache review boundary (PR #102)

Thumbnail entries are regenerable private pixel copies, not originals or mutation authority. JPEG 85 is the default; the developer WebP 80 selection changes cache fingerprints, while alpha-channel images retain WebP. Successful replacement removes older same-output entries of either extension; shard pruning is best effort and count-based, not a strict byte quota. Old flat legacy files are outside that pruning traversal.

The general redirected-cache contract above is still mandatory, but the current thumbnail removal helpers have an open parent/shard validation finding: leaf-only checks do not establish a safe parent path. Removal also cannot yet prevent an already queued thumbnail write from republishing after cleanup. Do not claim these are solved or infer acceptance from source presence. See the [pre-merge review and remaining gates](../plans/2026-10-17-images-hybrid-pagination.md#evidence-and-remaining-gates). This documentation update does not change cache, export or removal behavior.

## Image-operation input snapshots (source-only helpers)

The [image-operation architecture](image-operations.md) defines `<state>/inputs/<attempt-id>/` for durable immutable source/mask/blend/transform snapshots. Worker-facing snapshot/restore helpers are called by the dedicated permit-scoped single-shot session, not generic txt2img dispatch. A fresh set contains fixed `source.png`, `image.png`, optional `source_mask.png`, `mask.png`, `blend_mask.png`, and the last-written `manifest.json` commit marker. Partial sets remain failure evidence and cannot be reused or automatically purged. Input schema v1 is independent of execution DB v3 and request v2. Raw/final evidence stays in `outputs/<attempt-id>/`; only explicitly registered final gallery artifacts participate in export/recycle. Inputs/masks/raw evidence are not disposable cache and must never become export/recycle targets by scanning attempt folders. The owned execution store handles its schema-3 migration separately. Dedicated dispatch commits inputs before submission, retains raw-1.png separately and registers final-1.png plus metadata only. No automatic input/raw cleanup is introduced; runtime acceptance remains pending.


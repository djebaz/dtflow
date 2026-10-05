# Runtime and test evidence layout

## Ownership and current implementation

Development application state uses `.dtflow/attempts.sqlite3` and final outputs use
`.dtflow/outputs/<attempt-id>/`. Output placement is backend-neutral; metadata and durable records identify the
backend. Each attempt groups its image(s) and metadata. Outputs are durable artifacts, not disposable cache.
The independent durable asset registry is `<state>/assets.sqlite3` (schema version 1, WAL, `synchronous=FULL`) with an
exclusive `assets.lock` held only by `assets prepare` and `assets reconcile`; `assets list|show` read without a lock.
It holds local receipts and unresolved/unknown preparation operations (checksum, size, backend, API origin, server
reference, state, sanitized evidence code, timestamps) and never credentials, credential fingerprints, signed URLs or
file contents. It is durable state, not cache: cache cleanup and `-nc` never touch it, newer schemas are refused
without reset, and an interrupted `intent`/`transferring` operation becomes explicit `unknown` the next time a writer
opens it. A receipt is only a local candidate; every new scope must pass a fresh metadata check before reuse.
The state directory's `owner.lock` is held per operation by the shared session service (`services/session.py`): a
generation takes it for its duration, a queue dispatch (CLI `queue start` or the desktop Start) takes it for the whole run while queue reads and posted controls take none, inventory refresh and connection checks take none, and the desktop never holds it
between operations. A second owner fails with an explicit "state is busy" outcome before anything is dispatched.
SQLite manages its WAL/SHM sidecars in the database directory. Since the queue schema (version 2) the same database
also holds queues, items, posted controls and queue events; opening a release 0.1.0 (version 1) database writes a
one-time `attempts.pre-v2.sqlite3` copy beside it before migrating.

Explicit live tests retain a separate repository-root `test-runs/<run-id>/`, ignored by Git. A run ID combines
UTC time and a random suffix. Local/cloud checks share that root; generation uses its own SQLite state and output
root. Ordinary offline tests use temporary directories, never the application root. Live-test evidence is retained
until deliberately removed; application cache cleanup does not touch it.

| Current live-run path | Contents |
| --- | --- |
| `test-runs/<run-id>/inventory/local_inventory.json` | Local Echo inventory snapshot |
| `test-runs/<run-id>/inventory/cloud_inventory.json` | DT+ inventory snapshot |
| `test-runs/<run-id>/inventory/lora_inventory.json` | Derived local/cloud LoRA availability evidence |
| `test-runs/<run-id>/state/attempts.sqlite3` | Durable live generation attempts, including unknown outcomes |
| `test-runs/<run-id>/outputs/<attempt-id>/final-1.png` | Validated final output |
| `test-runs/<run-id>/outputs/<attempt-id>/metadata.json` | Reproducibility metadata |
| `test-runs/<run-id>/results.json` | One build/check summary, including failed check outcomes |

Fixed names distinguish the current single local and single cloud connection without another directory level.
Inventory snapshots contain endpoint, observed server identity, observation/provenance fields and `cache_version`.
Neither a LAN IP nor Echo's server identifier is established as a permanent device/account identity; keep these
values inside JSON rather than using them as filename keys. The terminal summary prints the run directory.
No API keys, tokens or authentication blobs are written. A forced process termination can leave a partial run without
`results.json`; saved artifacts and attempt state remain evidence, not proof that every check completed.

## Planned application cache, logs and packaged paths

The derived LoRA cache writer is implemented. A successful `dtflow models` or desktop Refresh inventory also writes the
raw local or cloud snapshot (`persistence/inventory_cache.py`); the desktop reads it once per provider selection as a
prefill of the model and LoRA lists, marked cached, and generation always refreshes live. A read is a miss, never an
error, when the file is missing, unreadable, another `cache_version`, another endpoint or, for cloud, another credential
source or key (the file holds the source and a salted SHA-256 of the key, never the key). `-nc/--no-cache` (also on
`dtflow gui`) disables reads and writes. Retained live checks write all three inventory files under `test-runs/`.
Use the same filenames in development application cache, packaged application cache and test/scratch roots when
inventory snapshots are saved. Create optional folders only on demand:

| Development path | Ownership |
| --- | --- |
| `.dtflow/cache/inventory/local_inventory.json` | Disposable local inventory cache |
| `.dtflow/cache/inventory/cloud_inventory.json` | Disposable cloud inventory cache |
| `.dtflow/cache/inventory/lora_inventory.json` | Derived local-gRPC + DT+ LoRA availability cache; rebuildable from live evidence |
| `.dtflow/cache/thumbnails/<artifact-id>.webp` | Regenerable thumbnail |
| `.dtflow/cache/derived/<artifact-id>_<transformation-id>.png` | Regenerable derived asset |
| `logs/dtflow-<session>.log` | Rotating application diagnostics, one file set per process; the newest 20 sessions are kept |
| `.dtflow/settings.json` | Versioned, credential-free shared settings (currently the cloud concurrency maximum); durable, not cache |
| `.dtflow/last_session.json` | Versioned, credential-free last desktop session (provider, local endpoint, Generate form incl. prompt); durable, written on submit, successful connection check and quit; ignored if unreadable or a newer schema; `-nc` and cache cleanup never touch it |
| `logs/tests/<run-id>.log` | Optional live-run diagnostics when implemented |
| `tmp/<operation-id>/` | Disposable operation scratch; no predetermined backend categories |

Do not duplicate run summaries under `reports/`, create a second `run.json`, or separate output roots by backend.
Packaged Windows defaults to `%LOCALAPPDATA%\DTFlow\` with `attempts.sqlite3`, `outputs/`, `cache/`, `logs/` and
`tmp/` beneath that root when those features exist. Development `test-runs/` is not part of packaged application state.
Cache/log cleanup preserves durable records, outputs, credentials and model assets.

For the current single-local/single-cloud setup, no endpoint/pair hash is needed in filenames. Temporary snapshots
use an operation-owned `inventory/` directory with the same names; do not create a shared scratch inventory that
mixes operations. Future cache readers must check connection configuration and freshness, refresh/invalidate on
endpoint or credential changes, and never reuse account-dependent BYOM evidence across changed credentials.
There is no verified stable cloud account ID in the inspected API contract. Multiple saved connections may later
use explicit application connection-profile IDs; do not infer permanent identity from IPs or server identifiers.

Derived LoRA inventory stores `resolved_at_utc` separately from each source's original `observed_at_utc`,
backend/server/endpoint observations, refreshed flag and stale diagnostics. Resolution does not make cached evidence
fresh. A newer negative `FilesExist` observation reports local absence even if the older Echo snapshot listed it.
The application cache writer uses a unique same-directory temporary file before atomic replacement, so simultaneous
writers cannot share a staging filename.

## Cache format version

Use an integer `CACHE_VERSION = 2`, written as `cache_version` in inventory JSON. Application inventory persistence
owns this constant and the live evidence writer imports it rather than maintaining an independent copy.
Increment it when that format changes incompatibly, not on every release or
inventory refresh. It is independent of application version, run identity and SQLite schema version. Version 2 adds raw endpoint
metadata and replaces the derived inventory's single observation timestamp with resolution time plus source provenance.

Future disposable-cache readers must treat a missing or mismatched version as a cache miss and fetch a fresh
inventory. No conversion, legacy folder search or old-tree fallback is required. Retained live snapshots are historical
evidence and are not rewritten on a version change. Existing old-layout files are neither read, moved nor deleted.

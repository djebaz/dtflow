# Runtime and test evidence layout

## Ownership and current implementation

Development application state uses `.dtflow/attempts.sqlite3` and final outputs use
`.dtflow/outputs/<attempt-id>/`. Output placement is backend-neutral; metadata and durable records identify the
backend. Each attempt groups its image(s) and metadata. Outputs are durable artifacts, not disposable cache.
SQLite manages its WAL/SHM sidecars in the database directory.

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

The derived LoRA cache writer is implemented. Raw local/cloud inventories remain in memory during application
execution; their disk-cache integration is planned. Retained live checks already write all three inventory files.
Use the same filenames in development application cache, packaged application cache and test/scratch roots when
inventory snapshots are saved. Create optional folders only on demand:

| Development path | Ownership |
| --- | --- |
| `.dtflow/cache/inventory/local_inventory.json` | Disposable local inventory cache |
| `.dtflow/cache/inventory/cloud_inventory.json` | Disposable cloud inventory cache |
| `.dtflow/cache/inventory/lora_inventory.json` | Derived local-gRPC + DT+ LoRA availability cache; rebuildable from live evidence |
| `.dtflow/cache/thumbnails/<artifact-id>.webp` | Regenerable thumbnail |
| `.dtflow/cache/derived/<artifact-id>_<transformation-id>.png` | Regenerable derived asset |
| `logs/dtflow.log` | Rotating application diagnostics when shared logging is implemented |
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

## Cache format version

Use an integer `CACHE_VERSION = 1`, written as `cache_version` in inventory JSON. Application inventory persistence
owns this constant and the live evidence writer imports it rather than maintaining an independent copy.
Increment it when that format changes incompatibly, not on every release or
inventory refresh. It is independent of application version, run identity and SQLite schema version.

Future disposable-cache readers must treat a missing or mismatched version as a cache miss and fetch a fresh
inventory. No conversion, legacy folder search or old-tree fallback is required. Retained live snapshots are historical
evidence and are not rewritten on a version change. Existing old-layout files are neither read, moved nor deleted.

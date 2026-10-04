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
| `test-runs/<run-id>/inventory/local_<endpoint-id>.json` | Local Echo inventory snapshot |
| `test-runs/<run-id>/inventory/cloud_<endpoint-id>.json` | DT+ inventory snapshot |
| `test-runs/<run-id>/state/attempts.sqlite3` | Durable live generation attempts, including unknown outcomes |
| `test-runs/<run-id>/outputs/<attempt-id>/final-1.png` | Validated final output |
| `test-runs/<run-id>/outputs/<attempt-id>/metadata.json` | Reproducibility metadata |
| `test-runs/<run-id>/results.json` | One build/check summary, including failed check outcomes |

The endpoint ID is the first 16 hexadecimal characters of SHA-256 of the credential-free endpoint label (`host:port`).
The `local_`/`cloud_` prefix distinguishes backend types without another directory level. Inventory snapshots contain
endpoint, observation/provenance fields and `cache_version`. The terminal summary prints the run directory.
No API keys, tokens or authentication blobs are written. A forced process termination can leave a partial run without
`results.json`; saved artifacts and attempt state remain evidence, not proof that every check completed.

## Planned application cache, logs and packaged paths

Persistent application inventory caching is not implemented yet; adapters currently cache inventory in memory.
When introduced, use these paths, creating optional folders only on demand:

| Development path | Ownership |
| --- | --- |
| `.dtflow/cache/inventory/local_<endpoint-id>.json` | Disposable local inventory cache |
| `.dtflow/cache/inventory/cloud_<endpoint-id>.json` | Disposable cloud inventory cache |
| `.dtflow/cache/thumbnails/<artifact-id>.webp` | Regenerable thumbnail |
| `.dtflow/cache/derived/<artifact-id>_<transformation-id>.png` | Regenerable derived asset |
| `logs/dtflow.log` | Rotating application diagnostics when shared logging is implemented |
| `logs/tests/<run-id>.log` | Optional live-run diagnostics when implemented |
| `tmp/<operation-id>/` | Disposable operation scratch; no predetermined backend categories |

Do not duplicate run summaries under `reports/`, create a second `run.json`, or separate output roots by backend.
Packaged Windows defaults to `%LOCALAPPDATA%\DTFlow\` with `attempts.sqlite3`, `outputs/`, `cache/`, `logs/` and
`tmp/` beneath that root when those features exist. Development `test-runs/` is not part of packaged application state.
Cache/log cleanup preserves durable records, outputs, credentials and model assets.

## Cache format version

Use an integer `CACHE_VERSION = 1`, written as `cache_version` in inventory JSON. The current live evidence writer
owns this constant; the future application cache implementation should own the shared inventory JSON format rather
than introduce an independent copy. Increment it when that format changes incompatibly, not on every release or
inventory refresh. It is independent of application version, run identity and SQLite schema version.

Future disposable-cache readers must treat a missing or mismatched version as a cache miss and fetch a fresh
inventory. No conversion, legacy folder search or old-tree fallback is required. Retained live snapshots are historical
evidence and are not rewritten on a version change. Existing old-layout files are neither read, moved nor deleted.

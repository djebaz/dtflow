# Managed output layout (issue #296)

Status: implemented in the PR for #296; real-backend smoke by the owner pending. Sub-issue of epic #274.

## Problem

Every saved image was `outputs/<attempt id>/final-1.png` with `metadata.json` beside it. A file is meaningless outside DTFlow, and a workflow run that makes 20 images creates 20 folders.

## Decision

New saves use one folder per run and readable names that still carry the attempt id.

```
outputs/<run folder>/<prefix>-<attempt id>-<n>.png        final image n (1-based; gallery ordinal n - 1)
outputs/<run folder>/<prefix>-<attempt id>-raw-1.png      provider evidence of an image operation (output 1 only)
outputs/<run folder>/<prefix>-<attempt id>.json           the attempt's metadata sidecar (one per attempt)
```

- **Run folder** = one queue: `queue-<queue id 8>`, shared by all of its items (plain, workflow and script items). A one-off generation outside a queue gets `YYYYMMDD-HHMMSS-single-<attempt id 8>`. Queue folders carry no timestamp, so an app restart or a later per-image Retry writes into the same folder. A script run outside a queue uses `script-<run id 8>`.
- **Prefix** = readable label plus seed: prompt excerpt for queue items and scripts (`a-red-fox-s42`), node title plus branch for workflows (`inpaint-3-image-1-2-s42`). Lower-case `a-z0-9-` only, label at most 24 characters, prefix at most 48.
- The full 32-character attempt id stays in the file name on purpose: the gallery identity `attempt:ordinal` can still be inferred from a path (Queues thumbnails, the Workflows result thumbnails, the gallery attempt prefilter), ownership checks stay exact, and two attempts in one folder can never collide.
- Windows length: the longest name is about 48 + 1 + 32 + 6 = 87 characters, the folder at most 80, so `outputs\<folder>\<file>` adds at most 170 characters to the state path. This is within the 260 limit for normal state locations.

## Compatibility (nothing existing changes)

- **No stored record changes.** `attempts.artifacts_json` keeps the same shape: image paths, then the metadata path last. Gallery identity is still `attempt:ordinal`; only the recorded path text differs for new runs. No schema bump, no sidecar field added.
- **Old runs keep working.** The legacy layout `outputs/<attempt id>/final-N.png`, `raw-1.png`, `metadata.json` is parsed by the same code (`core/output_layout.py`). No file is renamed or moved; there is no migration.
- **Recycle / Delete and the Windows deletion helper** previously rebuilt the only eligible path from attempt id and ordinal. They now take the folder and file name from the recorded path and accept it only if the name is exactly this attempt's final of this ordinal (`companion_names`), the sidecar and raw names derive from that same prefix, and every existing check (state-relative location, no links or junctions, single link, sidecar proves the record, exactly one gallery reference, fingerprint, lease) still applies. A look-alike name never authorizes anything. The helper request gains a `rel` field; a request without it is treated as legacy.
- **Metadata sidecar lookup** used the file name `metadata.json` (Queues rows, queue step fields, script run output listing). It now recognizes the legacy name or a `<prefix>-<attempt id>.json` sidecar (`is_metadata_name`).
- **#295 final marker** matches Save results to step outputs by full saved path, unchanged.

## Places that build or parse paths

| Area | Change |
| --- | --- |
| `core/output_layout.py` (new) | Single definition: naming, parsing, `identity_from_path`, companion names |
| `services/artifacts.py`, `image_artifacts.py` | Write into the run folder with exclusive-name checks |
| `services/generation.py`, `session.py` | Optional `layout` passed from callers |
| `services/queue.py`, `workflow_item.py`, `scripting/host.py` | Build the run folder and labels |
| `services/image_recycle.py`, `gallery_read_alias.py`, `managed_original_provenance.py`, `persistence/windows_original.py` | Validate by recorded folder/name instead of rebuilt `final-N` |
| `persistence/gallery.py` | Attempt prefilter from either layout |
| `ui/queue_presenter.py`, `ui/workflows_page.py` | Identity from either layout |
| `persistence/queues.py`, `scripting/run_cli.py` | Sidecar recognized by `.json` name |
| `services/cloud_usage.py`, `persistence/cloud_usage.py` | None: counted from stored attempt rows, not paths |

## Not done / for the owner

- No rename of existing files (would be one-way; needs an explicit go).
- Existing images do not get readable names.

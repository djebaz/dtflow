# Image export architecture

## Ownership and scope

`core/export.py` defines immutable options/results. `services/image_export.py` owns copy-only JPEG export and imports no Qt or provider APIs. `persistence/gallery.py` provides a bounded read-only snapshot of committed final output identities. The shared `ui/image_export_panel.py` captures the current scope and runs the service on the existing AsyncRunner; queued progress updates return to Qt. Generate and Images expose identities/query snapshots, not export or backend rules.

Images **Export** toggles a scrollable right-hand column beside the Grid/List/Detail content, without navigating to Connection or starting export. The scope label/dropdown are hidden only in Images, where every newly started export uses checked IDs; Connection retains all scope choices. Opening the panel selects checked-image scope when idle, while an already-running operation keeps its frozen targets. All destination/settings/progress and Start/Cancel/Open controls are the exact same widget instances as Connection's middle-panel export card, canonical in `main_window.ui`. `main_window.py` moves that card between `imageExportConnectionHost` and the gallery's canonical `galleryExportHost`; one controller, preference load and running operation remain authoritative. Leaving Images restores it to Connection; returning to Images remembers whether the column was open. Closing or toggling the panel hides it without cancelling, and an active operation's scope/status are not reset. The separate close button remains available even after gallery checks are cleared. Both hosts scroll independently, and the shared card uses a vertical layout suitable for the column.

The gallery's checked identity set is session-only and distinct from the focused image; hidden filtered checks are included in the frozen export snapshot. The owner reported that the earlier export action worked; this new inline layout/navigation/cancellation behavior still requires owner smoke acceptance. No second exporter or dialog, new dependency, encoding or metadata change is introduced.

## Destination routing and file safety

Normal JPGs and optional same-basename JSON are directly in the user-selected folder. Default-on `ExportOptions.separate_noise_blank` routes flagged copies only into `noise/` or `blank/`; disabling it restores flat export without analysis. Sidecars always follow their image. Category folders are created on demand and must not redirect elsewhere.
Do not introduce attempt/provider/date subfolders. The service rejects destinations
inside application state or the source attempt folder. Originals and their durable
references are read-only. It uses exclusive filename reservations for both image
and sidecar, adding numeric collision suffixes rather than replacing any file.
No destination records are persisted. Each run lists root/noise/blank once, bounded to 10,000 JPEG candidates. New filenames carry `_dtf-<full-attempt-id>-<one-based-output>` followed only by an optional numeric collision suffix. That unambiguous marker and a cheap JPEG start/end-marker check populate an in-memory identity set. Legacy short-ID names or renamed files without the marker use bounded EXIF/mandatory-sidecar identity inspection. This is practical presence detection, not cryptographic integrity or complete sidecar verification for fast-path names. Source outputs are treated as immutable for already-existing-copy skips; changed originals require a new folder if a new copy is wanted. Skipped files/options/dates are never rewritten. Missing copies are exported again. Prior `.dtflow-export-index.json` files are ignored, not deleted or updated.

Recognized identities skip before per-item database lookup, source reads/hashing, analysis or encoding. A fully skipped run does not even load the computation cache. `ExportResult.skipped` is separate from exported/failed and included in processed counts. No persistent destination index or cross-process deduplication lock exists; exclusive reservations still prevent overwrites. Missing destination directories and parents are created before writing; permission errors do not redirect exports.
Caught write/encode errors clean up only the files reserved by that operation.
Successful completed images remain after cancellation; a crash/power loss can leave
an incomplete newly reserved export, but never replaces an original or existing file.

Bulk export captures at most 10,000 identities in one read transaction, then closes
it before conversion. It ignores gallery pagination but retains filters and order.
Non-skipped identities are rechecked against committed evidence. Later arrivals are excluded;
removed/unreadable outputs are item failures. Images are processed sequentially,
with encoded input capped at 64 MiB and decoded dimensions capped at 32 million
pixels. Progress includes failures; cancellation is checked between images.
Async cancellation signals the worker and settles an in-flight write before exit.

## Destination-independent computation cache

`persistence/image_analysis_cache.py` owns `<state>/cache/derived/image_analysis.json`, a flat disposable JSON cache (integer cache_version 1), bounded to 16 MiB and 10,000 source identities. Only source path digest/size/mtime, PNG SHA256 and reviewed advisory assessment fields are stored; no source paths, prompts, credentials, filenames or destinations. Completed-image updates move entries to the end; oldest entries are evicted when bounded capacity is exceeded. Atomic writes checkpoint every 25 newly exported copies and at end/cancel; failures warn and preserve copies. Cache is not success evidence.

New destinations still require PNG reading/decoding and JPEG encoding, but valid source fingerprints reuse the source checksum and detector output instead of hashing/analysing again. Source path-digest/size/mtime changes invalidate both. Detector algorithm changes invalidate assessment only, recomputing it when routing is enabled; checksum can still be reused. Missing/corrupt/version-mismatched caches are misses, never migrations or export failures. Fingerprints are a practical cache heuristic, not protection against modifications preserving size/mtime. Mid-read source size/mtime changes are rejected clearly.

The existing Derived/all-cache cleanup removes this file without touching outputs or exports. `export_outputs(..., no_cache=True)` skips persistent cache reads/writes; MainWindow passes the runtime flag through ImageExportPanel. In-run reuse requires no disk cache. Turning routing off skips analysis and embedded assessment, while allowing checksum reuse. Legacy destination files still require EXIF inspection on each run; no hidden destination history is retained to accelerate them.

## Advisory pixel classification

`services/image_quality.py` owns the shared v2 metrics/classification used by both export and `tools/images/detect_noise.py`; runtime code never imports developer tools. `assess_rgb` analyzes a copy of normalized RGB pixels and changes neither images nor job state. Export analyzes the white-composited source before JPEG encoding, off the GUI thread and inside existing decode limits. The developer CLI normalizes EXIF orientation and alpha before calling the same function. JPEG-based owner-labelled evidence is not automatic acceptance of PNG export routing.

When enabled, portable metadata records the advisory label, reasons, normalized metrics, thumbnail dimensions, algorithm identity and input semantics. When disabled, this assessment is absent. Existing request/effective configuration fields and prompt privacy are unchanged. `ExportResult.noise` and `.blank` count successfully written category exports, included in `.exported`, not `.failed`. Unreadable images remain ordinary per-item export failures, not noise/blank guesses. Labels are heuristic and cannot establish aesthetic or prompt correctness; see [detector limitations](../../tools/images/README.md).

## Encoding and metadata

Pillow is an explicit locked runtime dependency. JPEG quality is 95, 4:4:4.
Transparency is composited onto white; existing color profiles are retained.
Source EXIF/XMP is not blindly copied. JPEG is lossy, unlike the original PNG.

Readable Windows-safe names use the durable saved-at UTC time, a short ASCII prompt
label, request seed, full attempt ID marker and output ordinal. Timestamp and seed
semantics are explicit: saved time is not generation start time, and request/effective
seed is not proof of an independently reported per-image seed. Missing timestamps
use `undated`. Prompt-free export uses `image`, not a derived private prompt label.

Windows XPTitle/XPSubject/XPComment/XPKeywords provide readable fields. When prompts are included, ImageDescription also carries an ASCII-compatible prompt summary capped at 160 characters, matching the PoC; privacy-off omits that tag. Full Unicode prompts remain in XP fields and portable JSON. Unicode
EXIF UserComment contains the versioned portable JSON document; requested and
recorded effective settings remain separate. Explorer summaries are bounded; full
portable JSON is not silently truncated. If it exceeds the JPEG APP1/EXIF size limit,
a mandatory same-basename JSON sidecar holds it and EXIF contains the sidecar name.
The UI reports this fallback, even when optional sidecars were unchecked.

Metadata is a reviewed portable subset, not a raw copy: request settings and LoRAs,
reviewed effective generation fields, saved/output identities, dimensions, source
lineage when recorded, build identity, recorded backend time and source PNG checksum.
Local paths, server/endpoint identities, prepared-asset receipts/remote references,
credentials and arbitrary provider metadata are excluded. Missing/inconsistent
sidecars use explicitly marked durable request evidence instead of invented values.
The prompt checkbox applies equally to filename, visible EXIF, embedded JSON and
sidecars. Camera Make/Model, authorship and copyright are not fabricated.

## Historical export dates

New JPEG EXIF DateTimeOriginal/DateTimeDigitized include UTC offsets and subseconds from the durable successful saved time. This is an explicit generation-completion proxy, not invented generation-start telemetry. Windows creation time is set via [SetFileTime](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfiletime) on the exclusively created export handle after flushing writes, and on written JSON sidecars. Modified time remains export time. Timestamp failures warn and preserve completed copies; unavailable timestamps omit date fields rather than guess. No original/existing/skipped files are timestamped. Non-Windows execution retains EXIF but reports that file creation time was not set.

## Preferences and acceptance

`export_preferences.json` (schema 1) stores destination and privacy/sidecar/routing
choices under the state root. The additive `separate_noise_blank` boolean defaults to true for older schema-1 files; a present non-boolean is rejected. It is durable convenience data, not cache;
`--no-cache` and cache cleanup do not remove it. Unsupported/unreadable preferences
are not overwritten; exports can still proceed with a warning.

See the [implementation plan](../plans/2026-10-09-jpeg-image-export.md) and
[desktop guide](../guides/desktop.md#export-saved-images).
The earlier ignored PoC passed local EXIF and Windows property-API checks; this is
not acceptance of production implementation. No agent tests or smoke tests were run for
this feature under fast-implement. Final source/Windows/packaged acceptance is the
owner's responsibility; no inference or provider calls are needed to validate export.

The owner later reported a source smoke export; see [evidence](../research/jpeg-export-smoke-2026-10-09.md). Remaining scenario/packaged acceptance is separate.

## Cache-cleanup coordination

Export holds the shared nonblocking cache lease across source computation and checkpoints. Cleanup cannot overlap that cache transaction; if the lease is unavailable when export starts, export proceeds without persistent cache and reports the fallback. No-cache export never takes this lease. Connection also refuses cleanup while its export is running. See [cache management](cache-management.md); original PNGs, durable history and exported copies remain outside purge ownership.

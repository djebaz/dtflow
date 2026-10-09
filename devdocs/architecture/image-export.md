# Image export architecture

## Ownership and scope

`core/export.py` defines immutable options/results. `services/image_export.py` owns
copy-only JPEG export and imports no Qt or provider APIs. `persistence/gallery.py`
provides a bounded read-only snapshot of committed final output identities. The
Connection middle-panel `ui/image_export_panel.py` captures the current scope and
runs the service on the existing AsyncRunner; queued progress updates return to Qt.
Generate and Images expose identities/query snapshots, not export or backend rules.
All export controls live in the canonical `main_window.ui` Connection workspace.
Its existing cards are scrollable so the new card does not force a taller window.

## Destination routing and file safety

Normal JPGs and optional same-basename JSON are directly in the user-selected folder. Default-on `ExportOptions.separate_noise_blank` routes flagged copies only into `noise/` or `blank/`; disabling it restores flat export without analysis. Sidecars always follow their image. Category folders are created on demand and must not redirect elsewhere.
Do not introduce attempt/provider/date subfolders. The service rejects destinations
inside application state or the source attempt folder. Originals and their durable
references are read-only. It uses exclusive filename reservations for both image
and sidecar, adding numeric collision suffixes rather than replacing any file.
Caught write/encode errors clean up only the files reserved by that operation.
Successful completed images remain after cancellation; a crash/power loss can leave
an incomplete newly reserved export, but never replaces an original or existing file.

Bulk export captures at most 10,000 identities in one read transaction, then closes
it before conversion. It ignores gallery pagination but retains filters and order.
Each identity is rechecked against committed evidence. Later arrivals are excluded;
removed/unreadable outputs are item failures. Images are processed sequentially,
with encoded input capped at 64 MiB and decoded dimensions capped at 32 million
pixels. Progress includes failures; cancellation is checked between images.
Async cancellation signals the worker and settles an in-flight write before exit.

## Advisory pixel classification

`services/image_quality.py` owns the shared v2 metrics/classification used by both export and `tools/images/detect_noise.py`; runtime code never imports developer tools. `assess_rgb` analyzes a copy of normalized RGB pixels and changes neither images nor job state. Export analyzes the white-composited source before JPEG encoding, off the GUI thread and inside existing decode limits. The developer CLI normalizes EXIF orientation and alpha before calling the same function. JPEG-based owner-labelled evidence is not automatic acceptance of PNG export routing.

When enabled, portable metadata records the advisory label, reasons, normalized metrics, thumbnail dimensions, algorithm identity and input semantics. When disabled, this assessment is absent. Existing request/effective configuration fields and prompt privacy are unchanged. `ExportResult.noise` and `.blank` count successfully written category exports, included in `.exported`, not `.failed`. Unreadable images remain ordinary per-item export failures, not noise/blank guesses. Labels are heuristic and cannot establish aesthetic or prompt correctness; see [detector limitations](../../tools/images/README.md).

## Encoding and metadata

Pillow is an explicit locked runtime dependency. JPEG quality is 95, 4:4:4.
Transparency is composited onto white; existing color profiles are retained.
Source EXIF/XMP is not blindly copied. JPEG is lossy, unlike the original PNG.

Readable Windows-safe names use the durable saved-at UTC time, a short ASCII prompt
label, request seed, short attempt ID and output ordinal. Timestamp and seed
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

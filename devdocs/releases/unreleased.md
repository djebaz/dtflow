# Unreleased

## Highlights

- Export saved images from Connection into a JPEG folder with optional noise/blank sorting with readable names and Windows-friendly properties.

## Added

- Connection middle-panel image export hub for all saved images, the current Images filter across all pages, the selected Images image or the selected Generate PNG output.
- Shared local-only JPEG quality-95 / 4:4:4 export with prompt privacy, rich portable EXIF generation metadata, optional JSON sidecars, collision-safe naming and background progress/cancel. Full portable metadata exceeding EXIF capacity uses a reported mandatory sidecar instead of silent truncation.
- Durable export folder/privacy preferences, independent of cache; original PNGs, metadata and saved history remain untouched.

- Standalone developer-only pixel-statistics detector PoC under tools/images; advisory, provisional noise/blank flags with JSON metrics and a version-2 joint spatial rule for owner-confirmed noise misses, shared with optional export sorting, never job-status changes.

- Default-checked Connection export option to sort advisory detected noise/blank images and matching sidecars only into noise/ or blank/; normal images stay at root. Saved routing choice, shared v2 detector and completion counts; disable for flat export.

- Repeat export skips matching saved-output identities/checksums in root/noise/blank with a distinct skipped count, leaving existing files/options untouched. New Windows JPEGs and JSON sidecars receive the recorded saved-date creation time; EXIF Date taken carries UTC generation-completion-proxy semantics. Missing destination folders and parents are created automatically.
- Retained tools/images/export_jpeg_exif_poc.py reference PoC with required source/output arguments and no owner-local defaults or scratch-module imports; intentionally retains full private metadata and warns against sharing.

- Destination-free repeat skips: new filenames carry full saved-output identities; a transient folder listing skips copies before processing, with legacy/renamed EXIF fallback. Removed persistent export-folder indexes; old user files remain untouched. Central cache/derived/image_analysis.json reuses source checksums and advisory detection across destinations, invalidates fingerprints/algorithm versions, and respects no-cache/Derived cleanup. No prompts, source paths or destination records in the cache.

## Changed

- Connection middle-panel cards scroll within the shared shell to keep the export controls reachable on smaller windows.
- Pillow is declared as a runtime dependency through uv (it was already transitive).

## Fixed

- Restored the PoC-compatible EXIF ImageDescription prompt summary when Include prompts is checked; omit it entirely when unchecked. Newly exported copies gain the field; existing export files are not rewritten.

## Release audit

- PRs: #54, #55
- Scope: Version bump 0.3.8; shipped changes are recorded in v0.3.8.md, pending final owner executable acceptance and publication. PR #55 adds Connection-centered JPEG/EXIF exports with optional default-on advisory noise/blank sorting with portable metadata, privacy and background progress/cancel; initial development deferred tests/smoke under fast-implement; owner later requested full offline pytest (426 passed, 3 skipped) and Ruff (passed), with the existing boundary guard narrowed only for the two Pillow image services; owner reports a source smoke export with 345 JPGs at the folder root and no subfolders; final scenario/packaged acceptance remains with the owner; no inference or billable actions.

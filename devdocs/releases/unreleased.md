# Unreleased

## Highlights

- Export saved images from Connection into a flat JPEG folder with readable names and Windows-friendly properties.

## Added

- Connection middle-panel image export hub for all saved images, the current Images filter across all pages, the selected Images image or the selected Generate PNG output.
- Shared local-only JPEG quality-95 / 4:4:4 export with prompt privacy, rich portable EXIF generation metadata, optional JSON sidecars, collision-safe naming and background progress/cancel. Full portable metadata exceeding EXIF capacity uses a reported mandatory sidecar instead of silent truncation.
- Durable export folder/privacy preferences, independent of cache; original PNGs, metadata and saved history remain untouched.

- Standalone developer-only pixel-statistics detector PoC under tools/images; advisory, uncalibrated noise/blank flags with JSON metrics, no export/job integration.

## Changed

- Connection middle-panel cards scroll within the shared shell to keep the export controls reachable on smaller windows.
- Pillow is declared as a runtime dependency through uv (it was already transitive).

## Fixed

- Restored the PoC-compatible EXIF ImageDescription prompt summary when Include prompts is checked; omit it entirely when unchecked. Newly exported copies gain the field; existing export files are not rewritten.

## Release audit

- PRs: #54, #55
- Scope: Version bump 0.3.8; shipped changes are recorded in v0.3.8.md, pending final owner executable acceptance and publication. PR #55 adds Connection-centered flat JPEG/EXIF exports with portable metadata, privacy and background progress/cancel; no agent tests or smoke tests added/run under fast-implement; owner reports a source smoke export with 345 JPGs at the folder root and no subfolders; final scenario/packaged acceptance remains with the owner; no inference or billable actions.

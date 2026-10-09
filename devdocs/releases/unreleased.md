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

- Images keeps Select all as a standalone action at every width; it becomes Unselect all only when all current filtered results are checked. Unselect all clears hidden checks too; partial 10,000-ID fills retain Select all. Owner reports Select all / Unselect all smoke passed (2026-10-09, follow-up to f3bc285).

- Images List row/header checkboxes now use shared dark/blue styling with explicit checked and partial glyphs rather than native white boxes. Owner reports deletion smoke passed (2026-10-09); owner also reports checkbox styling smoke passed (2026-10-09, follow-up to ceb8e26); remaining recovery/platform/packaged gates are separate.

- Phase 8E system Trash adapter now uses locked Qt 6.11.2 instead of handwritten Windows COM, without a new dependency. Managed-original validation remains; Qt capability detection and per-file move results replace fixed-drive/filesystem/OS restrictions, with platform-aware wording. Qt-reported success with an unverifiable destination remains uncertain and is explicitly distinguished in results. Shared durable checkpoints/coordination remain Qt-free. Owner trash/recovery and platform acceptance remain pending; no agent tests or actual trash operations.

- Connection middle-panel cards scroll within the shared shell to keep the export controls reachable on smaller windows.
- Pillow is declared as a runtime dependency through uv (it was already transitive).

## Fixed

- UI cache measurement retries transient startup contention asynchronously for up to one second, avoiding a stale busy warning during ordinary inventory restoration. Cleanup remains nonblocking and prolonged cache use still reports busy.


- Cache sizes remeasure after inventory refresh, generation settlement, export completion/cancellation and Connection entry; superseded callbacks are ignored so a cleared zero-size display does not stay stale after cache rebuild.


- Short cache-persistence contention now waits up to 0.5 seconds off-thread rather than immediately discarding valid startup inventory evidence; cleanup/measurement/export ownership remain nonblocking. Owner suite reported 425 passed, 3 skipped and one cache-prefill timeout before this fix; the targeted test subsequently passed, and owner source-smoke acceptance plus passing CI at bb92217 are recorded.


- PR #56 completes shared cache-I/O/cleanup coordination across threads/processes, including export computations; busy exports fall back to no persistent cache. Category measurement shows file counts, redirected cache trees are refused, and bounded preflight plus partial-failure feedback preserves durable data and user exports. Owner accepted the selected 8D source/runtime scope on 2026-10-09 after passing CI and smoke tests; packaged acceptance is separate.


- Restored the PoC-compatible EXIF ImageDescription prompt summary when Include prompts is checked; omit it entirely when unchecked. Newly exported copies gain the field; existing export files are not rewritten.

## Planned scope

- Phase 8E PR #57 implements session-only Images multi-selection, a single-row action bar, checked-image export and durable system Trash with original-file coordination. Select all toggles to Unselect all only after full filtered coverage, within the 10,000 combined-check limit. Qt handles per-file Trash capability without permanent-delete fallback or OS/filesystem allowlists. Owner reports deletion, checkbox styling, Select all / Unselect all and earlier export action smoke passed; interrupted recovery and broader platform/packaged acceptance remain separate. Exported copies and audit history stay preserved.

- Images Export no longer redirects to Connection: it toggles a scrollable right-hand column with all shared export controls. One card/controller preserves settings and active progress across navigation; hiding does not cancel. Owner reports column smoke passed for 5772f54; Images now hides the scope dropdown and starts checked-only exports, while Connection retains every scope. This checked-only refinement remains owner-smoke-pending. No agent tests/smoke or provider calls.

- Repeat-export skips now validate committed/non-recycled identities in short batches of at most 128, avoiding a full gallery record and SQLite/lock setup for every skipped image. Actual exports retain per-image original-file leases; no destination index or safety bypass. Owner reports repeat-export speed smoke passed at 314737b (2026-10-09); no measured benchmark or agent runtime check. Mixed/recycled/unavailable/cancel and packaged/platform acceptance remain separate.

## Release audit

- PRs: #54, #55, #56, #57
- Scope: Version bump 0.3.8; shipped changes are recorded in v0.3.8.md, pending final owner executable acceptance and publication. PR #56 implements cache coordination/cleanup safety and adds the planned-only 8E image-management scope; owner cache/source-runtime acceptance recorded 2026-10-09 after passing CI and smoke tests, no new agent tests/smoke under fast-implement. PR #55 adds Connection-centered JPEG/EXIF exports with optional default-on advisory noise/blank sorting with portable metadata, privacy and background progress/cancel; initial development deferred tests/smoke under fast-implement; owner later requested full offline pytest (426 passed, 3 skipped) and Ruff (passed), with the existing boundary guard narrowed only for the two Pillow image services; owner reports a source smoke export with 345 JPGs at the folder root and no subfolders; final scenario/packaged acceptance remains with the owner; no inference or billable actions. PR #57 adds checked-image management and Qt-backed system Trash; Images now toggles the shared export card beside the listing, with new placement/navigation/cancel smoke pending.

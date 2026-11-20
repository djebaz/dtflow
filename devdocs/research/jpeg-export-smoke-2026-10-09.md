# JPEG export owner smoke — 2026-10-09

## Owner report

The owner reported smoke-testing the export feature with destination:
repository-relative `tmp/smoke_test_jpg_export`.
This occurred on the working `feature/jpeg-image-export` branch, before the final
implementation commit. Exact executable/source revision and individual assertions
were not supplied. Do not treat it as packaged acceptance or a full scenario matrix.

## Read-only agent observation

The agent listed that directory after the report: **345 JPG files directly at its
root, zero subfolders, zero JSON sidecars**. This supports the flat-folder output
requirement. The agent did not decode those images, inspect their EXIF, compare
originals, run an export or launch the GUI during implementation.

## Validation boundary

No tests were added or run by the agent. No agent smoke tests were run under
fast-implement. Static syntax/import/Designer XML, Ruff, lock consistency and CLI
help checks are recorded in the [plan](../plans/2026-10-09-jpeg-image-export.md).
The earlier ignored single-image PoC's EXIF/Windows-property checks are separate.
Final tests and remaining Windows/packaged acceptance belong to the owner, including
privacy-off, optional/oversized sidecars, repeated export collisions, single/filtered
scopes, corrupt/missing files and cancel/close boundaries. No inference or potentially
billable provider operation was performed by the agent.

## ImageDescription follow-up smoke

After the ImageDescription fix (`9fa4c31`), the owner reported that the new smoke
"seems ok", using repository-relative `tmp/smoke3`.
The exact runtime/executable revision was not supplied; the local branch was at
`9fa4c31` when the agent inspected the supplied files.

Read-only file/EXIF inspection (no new export, GUI launch or pixel decode) found:

- 345 JPGs directly at the folder root; zero subfolders and zero JSON sidecars.
- All 345 have `prompts_included=true` in portable metadata.
- All 345 contain ImageDescription, matching the bounded ASCII-compatible prompt
  summary in embedded metadata; no unreadable metadata was found.

This supports the checked Include prompts path and restored ImageDescription for
the supplied artifacts. It does not establish unchecked privacy, collision,
sidecar-overflow, cancellation, all scope variants or packaged acceptance. No tests
or agent smoke invocation were performed. The ignored inspection summary is
`tmp/smoke3-exif-inspection.json`.

# JPEG export owner smoke — 2026-10-09

## Owner report

The owner reported smoke-testing the export feature with destination:
`C:/Users/dje/Documents/GitHub/dtflow/tmp/smoke_test_jpg_export`.
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

# PR #85: Windows/WSL source follow-ups

PR #85 targets `main` after PR #76's squash merge (`de948a0`). The narrow feature commit was rebased from `8704625` to `c428bc0`, preserving snapshot guards and consolidated release notes. Source review and CI are separate from owner runtime acceptance; PR #76 smoke acceptance does not accept these new changes.

## Review correction scope

- [x] Desktop preparation: use the existing runner for target metadata/directory-access checks, WSL translation and interop spawning; native Qt hand-off and notices stay on Qt. A missing, wrong-kind or inaccessible export directory refuses clearly, without claiming a visible external window or reading image contents outside the existing original-file boundary.
- [ ] Associated image opening: distinguish Explorer folder navigation from Windows-associated file opening, with discrete executable arguments and no user-controlled command interpolation.
- [ ] Model readiness: perform a bounded read-access check on the existing worker rather than only `is_file()`. Full model SHA verification remains at actual loading, and saved references stay unchanged.
- [ ] Owner source smoke for #78 model alias/readiness, #80 saved-request reuse and #81 Folder/External Viewer/export-folder opening, including missing/inaccessible targets and absent interop/association.

Keep snapshot-backed settings/regeneration/export disabled, foreign Edit fail-closed, shared original-file leases/provenance and fresh dispatch checks unchanged. No new WSL2 or shared execution-database writer support. #79 remains open for broader missing logging coverage.

No new tests, local pytest, renderer runs, provider operations or inference. Offline preview definitions are extended without running them. Run Ruff format/lint before each push; the draft PR uses the manual offline CI workflow. The earlier manual run [39012061169](https://github.com/djebaz/dtflow/actions/runs/39012061169) passed on `c428bc0`, not these later corrections.

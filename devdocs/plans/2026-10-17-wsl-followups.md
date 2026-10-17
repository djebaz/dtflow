# PR #85: Windows/WSL source follow-ups

PR #85 targets `main` after PR #76's squash merge (`de948a0`). The narrow feature commit was rebased from `8704625` to `c428bc0`, preserving snapshot guards and consolidated release notes. Source review and CI are separate from owner runtime acceptance; PR #76 smoke acceptance does not accept these new changes.

## Review correction scope

- [x] Desktop preparation: use the existing runner for target metadata/directory-access checks, WSL translation and interop spawning; native Qt hand-off and notices stay on Qt. A missing, wrong-kind or inaccessible export directory refuses clearly, without claiming a visible external window or reading image contents outside the existing original-file boundary.
- [x] Associated image opening: Explorer is used only for folders. WSL External Viewer invokes PowerShell 7 `Start-Process -FilePath` through a fixed UTF-16LE encoded script and JSON stdin, never interpolating a path into executable code. Translation/association timeouts are bounded on the worker; missing interop/default association reports safely. Windows `pwsh.exe` must be reachable from WSL PATH. Success means hand-off, not a confirmed visible window.
- [x] Model readiness: the existing worker stats each saved model, verifies a regular file and opens/reads at most one byte; this is access readiness, not ONNX/SHA validity or a hard I/O deadline. The editor shows checking while pending; permission/not-found/other failures report a role-specific sanitized status/log. A failed readiness worker cannot mark models ready. Readability is checked again after Models… applies a new selection, including any previously saved optional models that were retained. Full model SHA verification remains at actual loading, and saved references stay unchanged. Preview states 53/55 are defined, not rendered.
- [ ] Owner source smoke for #78 model alias/readiness, #80 saved-request reuse and #81 Folder/External Viewer/export-folder opening, including missing/inaccessible targets and absent interop/association.

Owner report (2026-10-17, WSL context): model loading, Find people and clothes segmentation work. This accepts the happy-path WSL model access/analysis observation, not the later unreadable-file readiness correction, native Windows behavior or new desktop-launch fixes. The runtime log records `commit=unknown`; no exact tested build identity is inferred.

Association design follows Microsoft's [Start-Process file-association contract](https://github.com/MicrosoftDocs/PowerShell-Docs/blob/main/reference/7.6/Microsoft.PowerShell.Management/Start-Process.md) and [UTF-16LE EncodedCommand documentation](https://github.com/MicrosoftDocs/PowerShell-Docs/blob/main/reference/7.6/Microsoft.PowerShell.Core/About/about_Pwsh.md). PowerShell code can be parser-checked without executing the launch; parser success does not establish owner interop acceptance.

Keep snapshot-backed settings/regeneration/export disabled, foreign Edit fail-closed, shared original-file leases/provenance and fresh dispatch checks unchanged. No new WSL2 or shared execution-database writer support. #79 remains open for broader missing logging coverage.

No new tests, local pytest, renderer runs, provider operations or inference. Offline preview definitions are extended without running them. Run Ruff format/lint before each push; the draft PR uses the manual offline CI workflow. The earlier manual run [39012061169](https://github.com/djebaz/dtflow/actions/runs/39012061169) passed on `c428bc0`, not these later corrections.

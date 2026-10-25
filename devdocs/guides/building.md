# Build the Windows desktop executable

The canonical build produces a stamped, single-file **GUI application**. Double-clicking it opens DTFlow directly; no terminal or `gui` subcommand is needed. Source CLI automation remains available through `uv run dtflow` and is not a separate distributed executable.

## Manual GitHub Actions build

After the workflow PR is merged, open **Actions > Build Windows EXE and draft release > Run workflow**:

- Select the workflow ref to build. Leave **release_tag** empty for a build-only Actions artifact.
- To build a PR, select the trusted workflow (normally main), enter its number in **pr** and leave **release_tag** empty. The workflow resolves the same-repository PR branch to its exact head SHA before checkout; it does not build GitHub's synthetic merge commit. The manifest records the PR number and selected source. PR mode is artifact-only: no tag, release creation, attachment or publication. Combining both inputs fails before checkout. Fork PRs are not supported.
- For a future release, supply an existing stable `vX.Y.Z` tag from merged main. Its source version and versioned notes must match. This creates/attaches only to a **draft release**; it never creates a tag or publishes.
- Download the run artifact containing the stamped EXE, `SHA256SUMS.txt` and `build-manifest.json`. The manifest records full clean source identity, checksum, size, toolchain and workflow provenance. Smoke-test the exact download on the target Windows machine before publication.

The build uses Windows 2022 x64, managed Python 3.13.13, uv 0.11.19 and PyInstaller 6.22.3 through the same canonical builder. No dev dependencies, GUI execution, application credentials, live generation or DT+ upload run in this workflow. Actions runner minutes/storage may be charged under the repository's plan.

**Caching:** uv's dependency and managed-Python caches are isolated by OS/architecture/Python and a build-tool suffix. A generated scratch fingerprint hashes the entire `uv.lock` with only DTFlow's own version normalized, allowing version-only bumps to reuse dependencies; the real lockfile is never rewritten and sync remains locked. Cached wheels are retained. `.venv`, PyInstaller analysis, `build/`, `dist/` and stamps are **not** cached; every executable is freshly built with `--clean`. Upload ZIP compression is disabled for the already compressed EXE. This is dependency-cache optimization, not measured PyInstaller speedup. Cold/warm timings and target-machine compatibility remain owner verification.

PR builds have a PR-number cache suffix separate from the normal/release cache, so they cannot populate the dependency namespace used by release builds. Run only reviewed same-repository code; manual builds still execute the selected source's build scripts. Older PRs without the canonical builder/verifier are not supported by this workflow. A clean PR build stamp means an unmodified checkout, not merged/released or runtime-accepted code.

The artifact is retained for 14 days. Draft attachment rechecks the remote tag, refuses published releases or existing asset names, and downloads the three uploaded assets to compare their hashes. A partial upload is an explicit failure requiring manual draft inspection; no clobber, deletion or silent retry is performed. Follow [the release guide](releasing.md).

## Build from PowerShell

Run in the repository root on the target Windows architecture:

```powershell
git status --short
uv sync --locked
uv run --with pyinstaller==6.22.3 python tools/packaging/build_exe.py
```

### Optional one-folder build (startup comparison)

The canonical builder also accepts `--onedir` for a folder-based distribution. The default remains `--onefile`; do not pass PyInstaller's mode flags directly to the builder.

```powershell
uv run --with pyinstaller==6.22.3 python tools/packaging/build_exe.py --onedir
```

This produces a stamped folder under `dist/` containing `dtflow.exe` and its dependencies. Keep the **entire folder** when moving or testing it; do not distribute `dtflow.exe` alone. One-folder mode avoids the per-launch extraction of the one-file bootloader, but is not necessarily smaller on disk. The owner reports that the Windows one-folder build launched correctly and felt faster than the standalone one-file build; **no controlled timings or release acceptance have been recorded**. Compare the exact build identity and startup timings on the same machine before claiming a benchmark.

Both modes can consume substantial disk space in `build/` and `dist/`. A Windows `--onedir` attempt previously reached PyInstaller's COLLECT phase and failed with `OSError: [Errno 28] No space left on device`; the owner subsequently reported a working build. Check available disk space and remove only unneeded generated artifacts before retrying. The manual GitHub Actions workflow and release artifact remain one-file unless explicitly changed in a separate reviewed update.

PyInstaller 6.22.3 is the previously recorded Windows build-tool version; the command requests it temporarily without changing the application lockfile. Record the actual Python, Windows, architecture and build-tool versions with acceptance evidence. Build public releases from a clean checkout. A development build with tracked modifications warns and stamps `dirty`; do not present it as a clean release.

The script prints the output path and source identity only after a successful build. The artifact is:

```text
dist/dtflow-<version>-g<short-sha>-windows-<arch>.exe
```

The version comes from the project metadata, not the proposed roadmap number. Follow [version bump and release](releasing.md) before building the final release; the owner runs the local build or requests the Actions build after the release PR is squash-merged. The builder records the full source commit and clean/dirty status, bundles it, then removes the generated identity module. The runtime does not need Git or a source checkout.

## Launch the exact build

After success, use the filename printed by the builder, or select the newest stamped Windows build:

```powershell
$exe = Get-ChildItem .\dist\dtflow-*-windows-*.exe |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
Start-Process -FilePath $exe.FullName -WindowStyle Hidden
```

Launch options are `-nc/--no-cache`, `-x/--dev`, `-q/--logs DIR`, `-h/--help` and `-v/--version`. Help and version appear in a native dialog when no console is attached; the same build identity is visible in the desktop status area. This is not a packaged generation CLI: do not pass `generate`, `queue`, `test` or `gui` subcommands.

## Packaging inputs and diagnostics

The canonical input is `tools/packaging/launch_gui.py`, delegating to the desktop bootstrap and existing Qt application. Windows uses the windowed bootloader and the DTFlow icon. PyInstaller's PySide6 hooks collect native Qt libraries/plugins. Explicit package data includes canonical Designer forms, QSS/tokens, images and `drawthings-py` trust resources; keyring metadata and the Windows backend are included for secure storage. `--clean` avoids reusing stale analysis/identity inputs.

Application state and outputs default to `%LOCALAPPDATA%\DTFlow\`; logs default to `%LOCALAPPDATA%\DTFlow\logs\`. Those paths are independent of the executable directory and current working directory. `-q/--logs` overrides diagnostics only; `-nc` does not remove durable state or outputs. See [runtime layout](../architecture/runtime-layout.md).

Startup errors are reported without raw exception details. Logging remains the shared rotating, sanitized standard-library setup; a logging failure warns rather than changing generation or retry behavior. No-console third-party stream handles discard incidental stdout/stderr; application diagnostic failures use the explicit native reporter, not those handles.

## Owner Windows acceptance

A successful build is not proof that the executable runs. Record the **exact artifact filename, source identity and checksum** before acceptance. Check:

- Direct launch without a console, including from outside the checkout/with Python and Git off PATH.
- Designer forms, theme, images, Qt platform plugins and fit/zoom/pan workspace.
- About/status identity against the filename; per-user state, outputs and logs.
- Credential Manager access and normal application startup/close, with no secret output.
- Offline fake generation and queue interaction for packaged UI/resources; never silently submit a real workload for a packaging check.

This is a focused packaged-composition check, not a duplicate protocol/scheduler suite. Existing owner-reported source/live checks remain separate evidence. Any additional real generation/upload must be explicitly selected and authorized.

The owner has exercised a dirty GUI development build; [its smoke report](../research/windows-gui-smoke-2026-10-05.md) identifies the artifact and bounded coverage. This is not a substitute for identifying the final clean release artifact.

The [packaging findings](../research/windows-packaging.md) retain earlier console smoke/CLI trials as historical evidence. `launch_dtflow.py` and `dtflow-cli.spec` are not the current GUI build inputs.

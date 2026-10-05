# Build the Windows desktop executable

The canonical build produces a stamped, single-file **GUI application**. Double-clicking it opens DTFlow directly; no terminal or `gui` subcommand is needed. Source CLI automation remains available through `uv run dtflow` and is not a separate distributed executable.

## Build from PowerShell

Run in the repository root on the target Windows architecture:

```powershell
git status --short
uv sync --locked
uv run --with pyinstaller==6.22.3 python tools/packaging/build_exe.py
```

PyInstaller 6.22.3 is the previously recorded Windows build-tool version; the command requests it temporarily without changing the application lockfile. Record the actual Python, Windows, architecture and build-tool versions with acceptance evidence. Build public releases from a clean checkout. A development build with tracked modifications warns and stamps `dirty`; do not present it as a clean release.

The script prints the output path and source identity only after a successful build. The artifact is:

```text
dist/dtflow-<version>-g<short-sha>-windows-<arch>.exe
```

The version comes from the project metadata, not the proposed roadmap number. Preparing a 0.3.0 release is a separate version/release change. The builder records the full source commit and clean/dirty status, bundles it, then removes the generated identity module. The runtime does not need Git or a source checkout.

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

The [packaging findings](../research/windows-packaging.md) retain earlier console smoke/CLI trials as historical evidence. `launch_dtflow.py` and `dtflow-cli.spec` are not the current GUI build inputs.

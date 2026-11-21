# Build the Windows desktop executable

The canonical build produces a stamped, single-file **GUI application**. Double-clicking it opens DTFlow directly; no terminal or `gui` subcommand is needed. Source CLI automation remains available through `uv run dtflow` and is not a separate distributed executable.

## Manual GitHub Actions build

After the workflow PR is merged, open **Actions > Build Windows EXE and draft release > Run workflow**:

- Select the workflow ref to build. Leave **release_tag** empty for a build-only Actions artifact. Use **ffmpeg_provider** = `imageio-mini` (Windows workflow default, owner-smoke-tested), `gyan` (standalone CLI builder default), `tas` (shared DLLs), `unpins` (headless CPU), or `imageio` (legacy FFmpeg with Gyan probe). The selection never changes automatically.
- To build a PR, select the trusted workflow (normally main), enter its number in **pr** and leave **release_tag** empty. The workflow resolves the same-repository PR branch to its exact head SHA before checkout; it does not build GitHub's synthetic merge commit. The manifest records the PR number and selected source. PR mode is artifact-only: no tag, release creation, attachment or publication. Combining both inputs fails before checkout. Fork PRs are not supported.
- For a future release, supply an existing stable `vX.Y.Z` tag from merged main. Its source version and versioned notes must match. This creates/attaches only to a **draft release**; it never creates a tag or publishes.
- Download the **GitHub Actions artifact** from the completed run's **Artifacts** section. It contains the stamped EXE, `SHA256SUMS.txt`, `build-manifest.json` and `FFmpeg-THIRD-PARTY.txt`; release-tag builds also attach these verified files to the draft release. The manifest records full clean source identity, checksum, size, toolchain and workflow provenance. Smoke-test the exact download on the target Windows machine before publication.

The build uses Windows 2022 x64, managed Python 3.13.13, uv 0.11.19 and PyInstaller 6.22.3 through the same canonical builder. No dev dependencies, GUI execution, application credentials, live generation or DT+ upload run in this workflow. Actions runner minutes/storage may be charged under the repository's plan.

**Caching:** uv's dependency and managed-Python caches are isolated by OS/architecture/Python and a build-tool suffix. A generated scratch fingerprint hashes the entire `uv.lock` with only DTFlow's own version normalized, allowing version-only bumps to reuse dependencies; the real lockfile is never rewritten and sync remains locked. Cached wheels are retained. `.venv`, PyInstaller analysis, `build/`, `dist/` and stamps are **not** cached; every executable is freshly built with `--clean`. Upload ZIP compression is disabled for the already compressed EXE. This is dependency-cache optimization, not measured PyInstaller speedup. Cold/warm timings and target-machine compatibility remain owner verification.

PR builds have a PR-number cache suffix separate from the normal/release cache, so they cannot populate the dependency namespace used by release builds. Run only reviewed same-repository code; manual builds still execute the selected source's build scripts. Older PRs without the canonical builder/verifier are not supported by this workflow. **For a PR that changes this workflow itself (such as #533), selecting the workflow from main runs main's older upload/release instructions even if the checkout is the PR head.** To inspect all four new artifacts before merge, explicitly select the **reviewed PR branch as the workflow ref** and specify its PR number, or use the local canonical builder. Do not execute unreviewed workflow YAML. After merge, the normal main workflow produces the full four-file artifact. A clean PR build stamp means an unmodified checkout, not merged/released or runtime-accepted code.

GitHub Actions build artifacts are stored under the workflow run with **24-hour retention**; download the artifact before it expires. They do not require Google Drive OAuth secrets. Release-tag builds download the artifact within the workflow, verify the manifest, executable and license checksums, and attach all four files to a draft GitHub Release. Draft attachment rechecks the remote tag and refuses published releases or existing asset names. A partial release upload is an explicit failure requiring manual draft inspection; no clobber, deletion or silent retry is performed. Follow [the release guide](releasing.md).

## Build from PowerShell

Run in the repository root on the target Windows architecture:

```powershell
git status --short
uv sync --locked --group quantization
uv run --group quantization --with pyinstaller==6.22.3 python tools/packaging/build_exe.py
```

### Optional one-folder build (startup comparison)

The canonical builder also accepts `--onedir` for a folder-based distribution. The default remains `--onefile`; do not pass PyInstaller's mode flags directly to the builder.

```powershell
uv run --group quantization --with pyinstaller==6.22.3 python tools/packaging/build_exe.py --onedir
```

This produces a stamped folder under `dist/` containing `dtflow.exe` and its dependencies. Keep the **entire folder** when moving or testing it; do not distribute `dtflow.exe` alone. One-folder mode avoids the per-launch extraction of the one-file bootloader, but is not necessarily smaller on disk. The owner reports that the Windows one-folder build launched correctly and felt faster than the standalone one-file build; **no controlled timings or release acceptance have been recorded**. Compare the exact build identity and startup timings on the same machine before claiming a benchmark.

Both modes can consume substantial disk space in `build/` and `dist/`. A Windows `--onedir` attempt previously reached PyInstaller's COLLECT phase and failed with `OSError: [Errno 28] No space left on device`; the owner subsequently reported a working build. Check available disk space and remove only unneeded generated artifacts before retrying. The manual GitHub Actions workflow and release artifact remain one-file unless explicitly changed in a separate reviewed update.

PyInstaller 6.22.3 is the previously recorded Windows build-tool version; the command requests it temporarily without changing the application lockfile. Record the actual Python, Windows, architecture and build-tool versions with acceptance evidence. Build public releases from a clean checkout. A development build with tracked modifications warns and stamps `dirty`; do not present it as a clean release.

The q8p dependency needs package data as well as its Python/native modules and distribution metadata: `kmeans1d.core` reads `kmeans1d/version.txt` during import. The canonical builder collects this file, and release verification rejects an archive without it. Dependency groups are a source/build-environment concern; standalone users do not install a quantization group. Identity/data verification still does not replace owner q8p smoke in the exact EXE.

The script prints the output path and source identity only after a successful build. The artifact is:

```text
dist/dtflow-<version>-g<short-sha>-windows-<arch>.exe
```

The version comes from the project metadata, not the proposed roadmap number. Follow [version bump and release](releasing.md) before building the final release; the owner runs the local build or requests the Actions build after the release PR is squash-merged. The builder records the full source commit and clean/dirty status, bundles it, then removes the generated identity module. The runtime does not need Git or a source checkout.

## Bundled FFmpeg (Windows video generation)

**Windows x64 builds have five explicitly selectable, pinned bundle combinations.** The Windows Actions workflow defaults to owner-smoke-tested `imageio-mini`; the standalone Python builder still defaults to Gyan when no provider is specified. Check binary licensing and corresponding-source compliance before any release. None uses UPX.

| Provider | Build flag | Binary layout | Source/version |
| --- | --- | --- | --- |
| **Gyan** (CLI default) | `--ffmpeg-provider gyan` | Static `ffmpeg.exe` and `ffprobe.exe` | [Gyan FFmpeg Essentials 8.1.2](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2); GPLv3 |
| **TAS** (experimental) | `--ffmpeg-provider tas` | `ffmpeg.exe`, `ffprobe.exe`, seven pinned shared FFmpeg DLLs | [TAS-FFMPEG v8.1.2](https://github.com/NevermindNilas/TAS-FFMPEG/releases/tag/v8.1.2); GPL-2.0-or-later |
| **Unpins** (experimental) | `--ffmpeg-provider unpins` | Two copies of one native multicall executable; GPU acceleration excluded | [Unpins FFmpeg v8.1.2-1](https://github.com/unpins/ffmpeg/releases/tag/v8.1.2-1); GPL-3.0-or-later |
| **imageio** (experimental) | `--ffmpeg-provider imageio` | FFmpeg 4.2.2 from Python wheel + Gyan ffprobe 8.1.2 | [imageio-ffmpeg 0.5.1](https://pypi.org/project/imageio-ffmpeg/0.5.1/); source audit pending |
| **imageio-mini** (workflow default; release audit pending) | `--ffmpeg-provider imageio-mini` | Same FFmpeg 4.2.2 + stripped FFprobe 7.1 | [ffmpeg-mini-win64 v1.0.1](https://github.com/nichiken/ffmpeg-mini-win64/releases/tag/v1.0.1), LGPL per publisher; source audit pending |

The selected archive is downloaded **only at build time**, verified with its publisher's exact SHA-256, and cached separately under ignored `tmp/packaging/`. DTFlow never downloads a codec on startup and never changes global `PATH`, cloud/backend settings or payment mode. Frozen DTFlow exclusively executes the binaries in its private `ffmpeg/` directory; source development still uses tools on `PATH`. A worker checks executable launch and the required `libx264`/AAC encoders before video submission. Existing timeouts, stderr draining and hidden Windows subprocesses are preserved.

**Pinned archives**

- Gyan: `ffmpeg-8.1.2-essentials_build.zip`, SHA-256 `db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`.
- TAS: `tas-ffmpeg-8.1.2-win64.zip`, SHA-256 `1d570273dc8be2b3f386467657591a22045d058a830b1eb1eaa881dafb689bfb`. TAS's complete corresponding-source archive is published [here](https://github.com/NevermindNilas/TAS-FFMPEG/releases/tag/v8.1.2) (SHA-256 `74605032486afbaaf6a489420eb4eea870c20604aace2b993e850a3e7d338ebd`).
- Unpins: `ffmpeg-8.1.2-1-x86_64-windows.exe.zst`, SHA-256 `abb78672e21721e7eadd84450616bd954f6b067d9355a27ef5d2b80f65a6f842` (upstream release digest). Extracting this **.zst** file requires **7-Zip 24.03+** at build time, already included on the GitHub-hosted `windows-2022` image. Local Windows builds need `7z.exe` on `PATH` or installed under `%ProgramFiles%\7-Zip\`; 7-Zip is not included in the delivered EXE.
- imageio: `imageio_ffmpeg-0.5.1-py3-none-win_amd64.whl`, SHA-256 `1521e79e253bedbdd36a547e0cbd94a025ba0b558e17f08fea687d805a0e4698` (PyPI **64-bit** wheel). Its FFmpeg 4.2.2 is extracted from the wheel; `ffprobe.exe` comes from the independently pinned Gyan 8.1.2 ZIP.
- imageio-mini: the **same wheel and hash** for FFmpeg 4.2.2 plus `ffmpeg-mini-win64.zip` (release v1.0.1), SHA-256 `f921d628254e824975ff9a60462cc1d48a5e627ed62c9a893c32ca36c55531cb`, **4,177,521 bytes compressed**. Only `ffprobe.exe`, the upstream LGPL text and README are extracted from the minimal archive; its `ffmpeg.exe` is deliberately unused. This build does **not** download the large Gyan archive or add a Python runtime dependency. The probe release's exact upstream FFmpeg 7.1 source revision is not pinned in its build script, so publication requires additional source verification.

PyInstaller stores binaries together under `ffmpeg/`, including TAS's DLLs in the same directory as its executables. Unpins uses one multicall binary whose program mode depends on its invoked filename; the builder stages byte-identical `ffmpeg.exe` and `ffprobe.exe` copies, so existing source/runtime command paths remain unchanged. The imageio variant packages FFmpeg 4.2.2 and Gyan ffprobe 8.1.2; imageio-mini keeps the same encoder but uses the minimal FFprobe 7.1. There is no runtime SDK integration. All selected tools are packaged separately in one-file mode; do **not** assume the compressed download equals the packed payload size. For one-folder builds this is under `_internal/ffmpeg/`; one-file builds extract the contents into PyInstaller's private runtime directory. Binary and license file SHA-256 values, selected provider, source URL, and build size are recorded in `build-manifest.json`. The release verifier checks **all** selected binaries and license files against staging. A human-readable `FFmpeg-THIRD-PARTY.txt` accompanies the EXE; the legal material is also embedded under `licenses/FFmpeg/`.

**Selecting an experimental provider locally**

```powershell
uv run --group quantization --with pyinstaller==6.22.3 python tools/packaging/build_exe.py --ffmpeg-provider imageio-mini
# Optional one-folder comparison:
uv run --group quantization --with pyinstaller==6.22.3 python tools/packaging/build_exe.py --ffmpeg-provider imageio-mini --onedir
# Previous imageio + Gyan ffprobe comparison:
uv run --group quantization --with pyinstaller==6.22.3 python tools/packaging/build_exe.py --ffmpeg-provider imageio
# Prior experimental shared-DLL comparison:
uv run --group quantization --with pyinstaller==6.22.3 python tools/packaging/build_exe.py --ffmpeg-provider tas
```

For GitHub Actions, select the reviewed `feat/529-bundle-ffmpeg-windows` workflow ref, set `pr=533`, `ffmpeg_provider=imageio-mini`, and leave `release_tag` empty. If GitHub's web form is still showing the inputs from main, dispatch the reviewed branch explicitly:

```powershell
gh workflow run build-and-release.yml --ref feat/529-bundle-ffmpeg-windows -f pr=533 -f ffmpeg_provider=imageio-mini
```

The workflow **must** come from the feature branch until merged: main does not expose this provider input. This builds an artifact only, without publishing. imageio-mini is stamped `dtflow-<version>-g<sha>-windows-x64-ffmpeg-imageio-mini.exe`; the earlier imageio build uses `-ffmpeg-imageio.exe`, Unpins `-ffmpeg-unpins.exe`, TAS `-ffmpeg-tas.exe`, and Gyan the ordinary filename. Compare builds from the **same source commit** if you want strict A/B size and startup evidence.

**Licensing:** Gyan is GPLv3. TAS is GPL-2.0-or-later and publishes a corresponding-source archive, which DTFlow does not attach automatically. Unpins declares GPL-3.0-or-later, and publishes pinned Nix build recipes; a complete corresponding-source bundle has **not** been independently verified for its release. The imageio wheel is a separate upstream FFmpeg 4.2.2 executable, and its Python wrapper license does not establish source compliance for the compiled FFmpeg. Both imageio combinations are **mixed-version** FFmpeg/ffprobe bundles; the minimal FFprobe's publisher declares LGPL and includes a license text, but complete corresponding source and exact build revisions are not verified. Both need a legal/source audit. DTFlow is GPL-3.0-only. Before publicly distributing any build, satisfy the chosen FFmpeg build's full GPL corresponding-source and notice obligations. Provenance links are not certification. None of the choices changes DTFlow's encoder, backend or billing policy.

**Measured size:** owner-requested Actions builds produced a **189,480,625-byte Gyan EXE** (head `d04f91a`, run `44878521527`) and a **182,853,916-byte TAS EXE** (head `fb88392`, run `44881166356`): about **6.6 MB / 3.5%** smaller for TAS, not a controlled same-commit test. Unpins' **34,537,283-byte .zst download** is not the expected EXE saving: its multicall bytes are copied twice. A successful Unpins build produced a **201,977,106-byte EXE** (head `e3ab45e`, run `44882530391`), larger than both Gyan and TAS. The imageio + Gyan FFprobe build succeeded on `6f7524d` at **174,997,706 bytes** ([run 44883546323](https://github.com/djebaz/dtflow/actions/runs/44883546323)), the smallest so far. The **imageio-mini** EXE built on head `9c5caf2` at **140,140,874 bytes** ([run 44884296087](https://github.com/djebaz/dtflow/actions/runs/44884296087)), versus 189,480,625 bytes for the earlier Gyan EXE (~26% smaller, different code heads). The owner reported successful video generation using this exact frozen Windows executable. One-folder smoke, detailed probe/audio and Qt playback checks, startup timing and corresponding-source audit are not separately verified. Rebuild the updated PR head before comparing new artifacts.

### Windows owner video packaging smoke

1. Run the canonical one-file build (or the manual Actions workflow with the PR head). Record the exact executable name, SHA-256, `ffmpeg` manifest and `size_bytes`. Check the external `FFmpeg-THIRD-PARTY.txt` and its checksum. For `--onedir`, retain the full folder and confirm `_internal\\ffmpeg\\ffmpeg.exe`, `_internal\\ffmpeg\\ffprobe.exe` and `_internal\\licenses\\FFmpeg` are present.
2. Launch **the exact packaged artifact** with external FFmpeg absent from `PATH`, or from a clean user/machine. Generate a small explicitly authorized LTX-2.3 video with audio; inspect the saved MP4, PNG poster, sidecar and the actual frame count/fps/audio rate recorded by ffprobe. This may consume DT+ credits.
3. Exercise **in-app** Play/Pause/seek/mute/close and **external-player** opening. These checks confirm QtMultimedia plugin availability in the *frozen* executable; source-run playback alone cannot. Also check that a blocked or missing binary fails clearly before submission, without spending credits. Test any fallback only in a disposable artifact/copy, never by editing a release binary.
4. Separately record one-file versus one-folder file size and measured startup time on the same Windows machine, as well as the earlier baseline without FFmpeg. If a vendor binary or Qt plugin fails, keep the PR unaccepted and report the exact file/build identity and error, without silently switching encoder/backend or billing mode.

## Launch the exact build

After success, use the filename printed by the builder, or select the newest stamped Windows build:

```powershell
$exe = Get-ChildItem .\dist\dtflow-*-windows-*.exe |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
Start-Process -FilePath $exe.FullName -WindowStyle Hidden
```

Launch options are `-nc/--no-cache`, `-x/--dev`, `-time/--time` for additional generation timing, `-perf/--perf` for performance measurements (requires `-x` too), `-q/--logs DIR`, `-h/--help` and `-v/--version`. Help and version appear in a native dialog when no console is attached; the same build identity is visible in the desktop status area. This is not a packaged generation CLI: do not pass `generate`, `queue`, `test` or `gui` subcommands.

## Packaging inputs and diagnostics

The canonical input is `tools/packaging/launch_gui.py`, delegating to the desktop bootstrap and existing Qt application. Windows uses the windowed bootloader and the DTFlow icon. PyInstaller's PySide6 hooks collect native Qt libraries/plugins. Explicit package data includes canonical Designer forms, QSS/tokens, images and `drawthings-py` trust resources; the q8p action bundles `kmeans1d` (native module and distribution metadata) from the existing locked `quantization` group. The builder rejects an environment without this group rather than silently producing an unusable q8p action; keyring metadata and the Windows backend are included for secure storage. `--clean` avoids reusing stale analysis/identity inputs.

Application state and outputs default to `%LOCALAPPDATA%\DTFlow\`; logs default to `%LOCALAPPDATA%\DTFlow\logs\`. Those paths are independent of the executable directory and current working directory. `-q/--logs` overrides diagnostics only; `-nc` does not remove durable state or outputs. See [runtime layout](../architecture/runtime-layout.md).

Startup errors are reported without raw exception details. Logging remains the shared rotating, sanitized standard-library setup; a logging failure warns rather than changing generation or retry behavior. No-console third-party stream handles discard incidental stdout/stderr; application diagnostic failures use the explicit native reporter, not those handles.

## Owner Windows acceptance

A successful build is not proof that the executable runs. Record the **exact artifact filename, source identity and checksum** before acceptance. Check:

- Direct launch without a console, including from outside the checkout/with Python and Git off PATH.
- Designer forms, theme, images, Qt platform plugins and fit/zoom/pan workspace.
- About/status identity against the filename; per-user state, outputs and logs.
- Credential Manager access and normal application startup/close, with no secret output.
- Offline fake generation and queue interaction for packaged UI/resources; never silently submit a real workload for a packaging check.
- Local q8p conversion from an F16 CKPT using multiple CPU workers in the **exact** packaged EXE. Check source preservation, separate output, size, progress, Cancel behavior and no cloud upload. This CPU-path smoke does not establish DT+ acceptance or rendered LoRA quality.

This is a focused packaged-composition check, not a duplicate protocol/scheduler suite. Existing owner-reported source/live checks remain separate evidence. Any additional real generation/upload must be explicitly selected and authorized.

The owner has exercised a dirty GUI development build; [its smoke report](../research/windows-gui-smoke-2026-10-05.md) identifies the artifact and bounded coverage. This is not a substitute for identifying the final clean release artifact.

The [packaging findings](../research/windows-packaging.md) retain earlier console smoke/CLI trials as historical evidence. `launch_dtflow.py` and `dtflow-cli.spec` are not the current GUI build inputs.

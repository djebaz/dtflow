# Windows packaging investigation

**Status:** Source-run checks (W1-W8), PyInstaller builds, the frozen smoke and CLI trials, a restricted-`PATH` run and the offline fake CLI passed on the target machine (owner-reported, 2026-10-04); nothing flagged the executables. Nuitka (W11) and an installer (W15) are not run, and Qt rows (W14) are blocked. A row is a finding only when it cites evidence.  
**Target:** The owner's Windows 10 / Intel Pentium E6500 machine. Linux or cloud results are not acceptance for this machine.  
**Scope:** Phase 2D investigation (see [Phase 2](../plans/phase-2-backends.md#phase-2d--packaging-licensing-and-feature-inventory)). It decides nothing about the final packaging tool or installer.  
**Safety:** Every check here is non-generative and non-billable. No credentials are needed or printed. No generation request, model download or authenticated cloud call is made.

## What this investigation answers

1. Does the locked runtime stack load and work on the target CPU (native wheels, NumPy 2.3.5 override, fpzip, Pillow)?
2. Does the bundled SQLite support the planned durable store (WAL, full synchronous commits)?
3. Do TLS trust resources work (system store, the CA file bundled in `drawthings-py`)?
4. Can a frozen executable be built, start without Python or Git installed, and report the same environment?
5. Are per-user paths writable (`%LOCALAPPDATA%`)?
6. What are the practical differences between a portable executable and an installer?

Qt resources, native Qt plugins and Credential Manager storage are out of scope until Qt and the credential service exist; they stay in the findings table as Blocked.

## Preconditions

- A checkout of the commit under test, clean (`git status` shows no changes); record `git rev-parse HEAD`.
- `uv sync --locked` completed from that checkout, with no ad-hoc `pip install` changes.
- Run from the repository root in PowerShell. Paste raw output; do not edit it.

## Procedure

### P1 — Capture the environment

```powershell
git rev-parse HEAD
git status --short
uv --version
uv run python --version
(Get-CimInstance Win32_OperatingSystem).Caption
(Get-CimInstance Win32_OperatingSystem).Version
(Get-CimInstance Win32_Processor).Name
```

### P2 — Source-run smoke (locked environment)

```powershell
uv sync --locked
uv run python tools/packaging/smoke.py
$LASTEXITCODE
```

`tools/packaging/smoke.py` prints a JSON list of checks and exits non-zero if any fails. It reports Python and platform details, package versions, NumPy CPU features, an fpzip compress/decompress round trip, a Pillow PNG round trip, the SQLite version with WAL and synchronous FULL on a temporary database, TLS trust resources (default verify paths, system CA count, existence of the CA file bundled in `drawthings-py`, OpenSSL version) and a write test under `%LOCALAPPDATA%`. It makes no network connection.

### P3 — Optional TLS handshake to the cloud host (no request, no credentials)

Skip this if you do not want any outbound connection. It performs only a TLS handshake and prints the certificate subject.

```powershell
uv run python -c "import socket, ssl; h='compute.drawthings.ai'; c=ssl.create_default_context(); s=c.wrap_socket(socket.create_connection((h,443),timeout=10), server_hostname=h); print(s.version(), s.getpeercert()['subject']); s.close()"
```

Record whether system trust alone validates the host. The `drawthings-py` client uses its own bundled CA file and disables hostname checking for its default channel; DTFlow's plan is a per-instance channel with injected trust, so this check informs that design and does not validate the library's behavior.

### P4 — Build a frozen smoke executable (tool trials)

These commands trial candidate tools against the same smoke script. Tool options are untested starting points; record what works. `uv run --with` installs the tool ephemerally and does not change `pyproject.toml` or `uv.lock`. Build output goes to ignored `build/` and `dist/`.

```powershell
# Candidate 1: PyInstaller (one-file). The CA file bundled in drawthings-py is a data resource.
uv run --with pyinstaller pyinstaller --onefile --name dtflow-smoke --collect-data drawthings_py tools/packaging/smoke.py
.\dist\dtflow-smoke.exe
$LASTEXITCODE

# Candidate 2: Nuitka (requires a supported C compiler on Windows; record what it needs).
uv run --with nuitka python -m nuitka --onefile --output-filename=dtflow-smoke-nuitka.exe tools/packaging/smoke.py
```

For each built executable record: size, build time, startup time (`Measure-Command { .\dist\dtflow-smoke.exe }`), exit code and the JSON output. Then copy it to a folder on a path with no Python or Git in `PATH` (or a second clean user account) and run it again to show it needs no repository or interpreter. Note any antivirus or SmartScreen warnings.

### P5 — Compare outputs

The packaged output must agree with the source-run output on package versions, CPU features, fpzip, Pillow, SQLite and TLS checks. Record differences, especially `frozen: true` and any missing resource. A frozen build reports `imported; metadata unavailable` for packages whose metadata is not bundled; that is expected unless `--copy-metadata` is passed for them, and it is not a failure.

### P6 — Source-run `dtflow` CLI, offline fake backend

The `dtflow` entry point now exists with an explicit offline fake backend. These steps make no network connection, load no model and submit no generation request. They use a temporary state directory, so no repository files change. Option spellings are those printed by `uv run dtflow <command> --help`; check them if the CLI has changed.

```powershell
uv run dtflow --version
uv run dtflow --help
$state = Join-Path $env:TEMP "dtflow-pkg-state"
uv run dtflow check --state $state --backend fake
uv run dtflow models --state $state --backend fake --refresh
uv run dtflow generate --state $state --backend fake --prompt "Offline fixture" --seed 0
$LASTEXITCODE
uv run dtflow attempts --state $state
Get-ChildItem -Recurse $state | Select-Object FullName, Length
Remove-Item -Recurse -Force $state
```

Record the version line, exit codes, the saved PNG and metadata file names and sizes, and that the PNG opens. The CLI refuses SQLite older than 3.51.3 (WAL-reset fix); the smoke result for W5 reported 3.53.1 on the target machine. On other machines a failure with that message is the runtime check working, not a packaging defect (observed on a Linux Python bundling SQLite 3.45.1).

### P7 — Frozen `dtflow` CLI trial

`tools/packaging/launch_dtflow.py` is a two-line launcher that exists only for this trial. `--copy-metadata dtflow` lets the version lookup find package metadata inside the executable; without it the version is reported as unknown. `--collect-data drawthings_py` bundles the library's resource files; since Phase 3A the CLI imports the real adapters at startup, and without it every command fails with `No module named 'drawthings_py.resources'` (observed on the target machine, 2026-10-04). Source commit and dirty status are `unknown` until a later build step stamps them, so that is expected here.

```powershell
uv run --with pyinstaller pyinstaller --onefile --name dtflow-cli --paths src --copy-metadata dtflow --collect-data drawthings_py tools/packaging/launch_dtflow.py
.\dist\dtflow-cli.exe --version
$state2 = Join-Path $env:TEMP "dtflow-pkg-state-frozen"
.\dist\dtflow-cli.exe generate --state $state2 --backend fake --prompt "Offline fixture" --seed 0
$LASTEXITCODE
.\dist\dtflow-cli.exe attempts --state $state2
Remove-Item -Recurse -Force $state2
```

Compare with P6: same exit codes, a readable PNG and metadata, and the same attempt record shape. Run the executable again from a folder with no Python or Git on `PATH`.

## Findings table

Statuses: **Not run** / **Pass** / **Partial** / **Fail** / **Blocked**. Partial means part of the row's evidence is still missing; the note says which part. Fill one row per executed check with date, environment (commit, Python, OS, CPU) and evidence (pasted output or its location). Never mark a row Pass without evidence.

| ID | Check | Status | Date | Environment / build | Evidence / notes |
| --- | --- | --- | --- | --- | --- |
| W1 | Source-run smoke passes (P2) | Pass | 2026-10-04 | commit `7c0d7fc` (clean), Python 3.13.13, Windows 10 Pro 10.0.19045, Pentium E6500, uv 0.11.19; script as of `7c0d7fc` | All 8 checks `ok`, exit code 0 (owner-reported output) |
| W2 | NumPy 2.3.5 loads; CPU features recorded | Pass | 2026-10-04 | same as W1 | NumPy 2.3.5 imported; reported CPU features: MMX, SSE, SSE2, SSE3, SSSE3 only (observation, not an explanation of the NumPy 2.4+ failure recorded elsewhere) |
| W3 | fpzip round trip | Pass | 2026-10-04 | same as W1 | fpzip 1.2.5; arrays equal, shape `[1, 64, 64, 1]` |
| W4 | Pillow PNG round trip | Pass | 2026-10-04 | same as W1 | Pillow 12.3.0; 64 x 64 PNG, 185 bytes |
| W5 | SQLite version; WAL and synchronous FULL work | Pass | 2026-10-04 | same as W1 | SQLite 3.53.1; journal mode `wal` on a temporary database. Shows WAL works; does not test power-loss durability |
| W6 | TLS: system trust resources and bundled CA present | Pass | 2026-10-04 | same as W1 | 84 system CA certificates; `drawthings-py` bundled CA file exists; OpenSSL 3.5.6; default verify file path `null` (Windows uses the system store) |
| W7 | TLS handshake to cloud host with system trust (optional, P3) | Pass | 2026-10-04 | same as W1 | TLSv1.3 to `compute.drawthings.ai`; certificate subject `commonName=compute.drawthings.ai`. Tests Python `ssl` with the system store, not `drawthings-py`'s bundled-CA channel (hostname checking disabled there) |
| W8 | `%LOCALAPPDATA%` writable | Pass | 2026-10-04 | same as W1 | Created and removed a file under the user's `AppData\Local` |
| W9 | PyInstaller one-file executable builds | Pass | 2026-10-04 | PyInstaller 6.22.3 via `uv run --with`; commit `1685c6ca8039f3d8759eab5cae35df32babf9115`; Python 3.13.13; Windows 10 Pro 10.0.19045; Pentium E6500 | Both builds exited 0. `dtflow-smoke.exe` 36,711,047 bytes, 88 s build; `dtflow-cli.exe` 10,020,468 bytes, 28.2 s build |
| W10 | PyInstaller executable runs on a clean path without Python or Git | Pass | 2026-10-04 | child `cmd` with `PATH` limited to `System32` and the Windows directory | From a copy in `tmp\packaging-2d\clean`: the frozen smoke run reported `frozen: true` and all checks `ok`; `dtflow-cli --version` and a fake `generate` succeeded; all exit 0. Limits: same machine and user; Python and Git remain installed but off `PATH`; the copy sits inside the repository tree; not a second machine or account |
| W11 | Nuitka executable builds (compiler requirement noted) | Not run | | | |
| W12 | Frozen output matches source-run output | Pass | 2026-10-04 | as W9 | All 8 checks `ok` in both runs; identical CPU features, fpzip and Pillow results, SQLite 3.53.1 in WAL mode, 84 system CAs and bundled CA present (so `--collect-data drawthings_py` worked); frozen run reports `frozen: true`. Accepted differences in `package_versions`: `pillow`, `fpzip`, `flatbuffers`, `grpclib` and `drawthings-py` report `imported; metadata unavailable` (metadata not bundled), while numpy, betterproto and tqdm report versions. `strictyaml`'s module `__version__` reads 1.6.2 although its distribution metadata reads 1.7.3 in the source run, so a module `__version__` is not reliable version evidence |
| W13 | Executable size, startup time, antivirus/SmartScreen behavior recorded | Pass | 2026-10-04 | as W9 | Smoke 36.7 MB, one run 7.1 s (one-file extraction plus the checks); CLI 10.0 MB. Owner reported that nothing (antivirus or SmartScreen) flagged either executable. The CLI's own startup time was not timed separately |
| W14 | Qt plugins, Credential Manager | Blocked | | | Qt and the credential service do not exist yet |
| W15 | Installer path (any tool) | Not run | | | Optional; see comparison below |
| W16 | Source-run `dtflow --version` and `--help` (P6) | Pass | 2026-10-04 | as W9 | `--version` printed `DTFlow 0.0.0 (revision unknown; source status unknown)`, exit 0; `--help` printed usage for `generate`, `check`, `models`, `attempts`. The `--help` exit code was not echoed |
| W17 | Source-run fake `check`, `models`, `generate` (seed 0) and `attempts` complete; PNG and metadata saved and readable (P6) | Pass | 2026-10-04 | as W9 | All four commands exited 0 with the expected JSON. The attempt record shows `state: succeeded`, `submission_stage: terminal_observed`, seed 0, 128 x 128, 4 steps, `euler_a`. State directory: `attempts.sqlite3` (16,384 bytes), `owner.lock`, `final-1.png` (404 bytes), `metadata.json` (1,506 bytes). The owner confirmed the saved PNG opens. SQLite 3.53.1 satisfies the 3.51.3 requirement |
| W18 | PyInstaller `dtflow` launcher builds and `--version` runs (P7) | Pass | 2026-10-04 | as W9 | Build exit 0; frozen `--version` printed the same line as the source run, so `--copy-metadata dtflow` supplied the version. Commit and status are `unknown` until a build step stamps them, as expected |
| W19 | Frozen fake `generate` and `attempts` match source-run behavior (P7) | Pass | 2026-10-04 | as W9 | Frozen `generate` and `attempts` exited 0; the attempt record has the same state, stages and request fields as the source run; same file set and sizes (404 and 1,506 bytes; `attempts.sqlite3` 16,384). Differences are only IDs, timestamps and paths. The clean-path `generate` also succeeded. File contents were compared by size, not byte for byte |
| W20 | Frozen Phase 3A CLI (`--collect-data drawthings_py`): fake `generate`, `attempts`, `settings -j`, invalid input and per-user logs (P7) | Pass | 2026-10-04 | PR #10 head; PyInstaller 6.22.3; Python 3.13.13; Windows 10 Pro 10.0.19045 | Without `--collect-data drawthings_py` every command failed with `No module named 'drawthings_py.resources'`; with it `--version` printed `DTFlow 0.0.0 (revision unknown; source status unknown)`, fake `generate` exited 0 with the attempt `succeeded`, `settings -j 2` persisted 2, `-w 300` printed `Invalid request: width_px must be a multiple of 64.` and four `dtflow-<session>.log` files appeared under `%LOCALAPPDATA%\DTFlow\logs`. Owner-reported output; the invalid-input exit code, the absence of an attempt for it and the log contents were not shown; clean-`PATH` rerun not done; real-backend commands not run frozen |

### Recorded results

Owner-reported Windows output, pasted from PowerShell on 2026-10-04 and condensed here; the Windows username in paths is redacted. Rows W1-W8 above rely on it. Nothing in this section was run by the assistant.

- Environment: commit `7c0d7fca562e8226ad4fa95ce39e4bae13e832c4` (`7c0d7fc`), clean working tree; uv 0.11.19; Python 3.13.13 (MSC v.1944, AMD64); Windows 10 Pro 10.0.19045; CPU `Pentium(R) Dual-Core E6500 @ 2.93GHz`.
- `uv sync --locked` removed `pytest-asyncio` from the environment, because this branch is based on the pre-Phase-2A lock. This is expected and unrelated to packaging.
- Smoke script output: all 8 checks `ok`, exit code 0. Package versions: numpy 2.3.5, pillow 12.3.0, fpzip 1.2.5, flatbuffers 25.12.19, grpclib 0.4.9, betterproto 2.0.0b7, drawthings-py 0.4.0, strictyaml 1.7.3, tqdm 4.70.1.
- TLS handshake (P3): `TLSv1.3`, certificate subject `commonName=compute.drawthings.ai`, validated by the system trust store.

The source-run rows apply to the smoke script as of commit `7c0d7fc`. Later changes to the script, made so a frozen build can import every module, keep the source-run checks logically identical; the frozen rows (W9-W13) have not been run.

### Recorded results: packaging trials (second run)

Owner-reported PowerShell 7.6.6 console output from 2026-10-04, saved under the ignored `tmp/packaging-2d/` directory; the Windows username in paths is redacted. Nothing here was run by the assistant. The first attempt's transcript omitted native command output; the console output used here includes it.

- Tree under test: `git rev-parse HEAD` printed `1685c6ca8039f3d8759eab5cae35df32babf9115` after a fast-forward pull from `7c0d7fc`. `git status --short` printed nothing before and after all trials, so no build output landed outside `tmp/`.
- `uv sync --locked` installed `pytest-asyncio` and removed `pytest-qt`, matching the lock.
- Source-run CLI: `--version`, `check`, `models --refresh`, `generate --seed 0` and `attempts` all exited 0 (fake backend, offline).
- Frozen builds: PyInstaller 6.22.3 produced `dtflow-smoke.exe` and `dtflow-cli.exe` (exit 0). The frozen smoke script passed all 8 checks with `frozen: true`; the frozen CLI reproduced the source-run version line, generation and attempt record.
- Clean-path run: smoke, `--version` and `generate` all succeeded from a copy with `PATH` reduced to the Windows directories.
- Observation: a one-file executable does not bundle package metadata for most dependencies unless a hook or flag supplies it. Reading `__version__` as a fallback can disagree with the installed distribution (`strictyaml` 1.6.2 versus 1.7.3), so frozen version evidence needs `--copy-metadata` for the packages that matter.
- Follow-up answers from the owner: nothing flagged either executable, and the saved PNG opened.
- Not run: Nuitka (W11) and an installer (W15).

### PyInstaller warning files reviewed

The owner supplied `warn-dtflow-cli.txt` and `warn-dtflow-smoke.txt` from the build directory. They list modules PyInstaller could not find; most are expected on Windows and none was needed by the successful runs. Reviewed by the assistant; nothing was changed.

- **POSIX-only and platform modules** (`pwd`, `grp`, `posix`, `fcntl`, `termios`, `resource`, `_posixsubprocess`, `_scproxy`, `readline`, `vms_lib`, `java`): not applicable on Windows. `fcntl` is named for `dtflow.persistence.store`; that module takes the POSIX branch only when `os.name` is not `nt` and uses `msvcrt.locking` on Windows, which matches the one-byte `owner.lock` file seen in the state directory.
- **Compatibility noise**: `collections.abc` (reported as missing but present), old `collections.*` names and `StringIO`/`cStringIO`/`copy_reg`/`ordereddict` from `strictyaml.ruamel` Python 2 branches, `six.moves` aliases, and a long list of `numpy._core.*` attribute names. The frozen smoke run exercised NumPy and passed.
- **Optional dependencies not installed**: `imageio_ffmpeg` (a `drawthings-py` video-export extra, imported lazily), `certifi` (an optional fallback in `grpclib.client`), `google.protobuf` and `google.rpc` (`grpclib`'s protobuf codec, unused because `betterproto` is used), `betterproto_rust_codec`, `_ruamel_yaml`, plus `pandas`, `matplotlib`, `IPython`, `ipywidgets`, `olefile`, `defusedxml`, `psutil`, `threadpoolctl`, `win32pdh`. Video export and any code path that relies on `certifi` or protobuf would not work in this build; none of those paths is part of the current smoke or fake CLI.
- **Size difference**: the CLI warning file contains no `numpy`, `PIL`, `drawthings_py`, `grpclib` or `strictyaml` entries, because the Phase 2A fake CLI does not import them; its executable is 10.0 MB. The smoke executable pulls those packages and `setuptools` and is 36.7 MB. Expect the CLI build to grow toward the larger size when the real adapters are added.
- **Follow-ups for packaging work**: decide whether `grpclib`'s TLS fallback needs `certifi` or the system store, whether video export will ship, and whether `--copy-metadata` is needed for dependencies whose versions are reported. Re-run the frozen trials when real adapters land.

## Portable executable versus installer

Evidence is not collected yet; these are criteria, not conclusions.

| Criterion | Portable executable | Installer |
| --- | --- | --- |
| Meets the stated first-release goal (portable Qt executable) | Yes, by definition | Retained as an option if required |
| Per-user data location (`%LOCALAPPDATA%\DTFlow\`) | Same requirement | Same requirement |
| Startup cost on the Pentium E6500 | Measure in P4 (one-file extraction cost) | Usually avoids per-launch extraction |
| Native codec and resource bundling | Must be proven (W9–W12) | Same underlying requirement |
| Antivirus and SmartScreen exposure | Measure | Measure |
| Update path | Manual downloads (the initial strategy) | Manual initially; installer may ease side-by-side versions |
| Build identity in filename | `dtflow-<version>-g<short-sha>-<platform>-<arch>.<ext>` | Same convention |

## Not covered here

- A decision on the final packaging tool or installer technology.
- Qt packaging, which belongs with the Qt milestone.
- GPL-3.0-only corresponding-source, license-text and third-party notice handling for a build that bundles `drawthings-py`; see [decision 0001](../decisions/0001-drawthings-py-license.md).
- Any claim about application behavior beyond the smoke script, which is not DTFlow application code.

# Windows packaging investigation

**Status:** Source-run checks W1-W8 passed on the target machine (owner-reported, 2026-10-04); frozen-build and installer rows are not run. A row is a finding only when it cites evidence.  
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

The packaged output must agree with the source-run output on package versions, CPU features, fpzip, Pillow, SQLite and TLS checks. Record differences, especially `frozen: true` and any missing resource.

## Findings table

Statuses: **Not run** / **Pass** / **Fail** / **Blocked**. Fill one row per executed check with date, environment (commit, Python, OS, CPU) and evidence (pasted output or its location). Never mark a row Pass without evidence.

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
| W9 | PyInstaller one-file executable builds | Not run | | | |
| W10 | PyInstaller executable runs on a clean path without Python or Git | Not run | | | |
| W11 | Nuitka executable builds (compiler requirement noted) | Not run | | | |
| W12 | Frozen output matches source-run output | Not run | | | |
| W13 | Executable size, startup time, antivirus/SmartScreen behavior recorded | Not run | | | |
| W14 | Qt plugins, Credential Manager | Blocked | | | Qt and the credential service do not exist yet |
| W15 | Installer path (any tool) | Not run | | | Optional; see comparison below |

### Recorded results

Owner-reported Windows output, pasted from PowerShell on 2026-10-04 and condensed here; the Windows username in paths is redacted. Rows W1-W8 above rely on it. Nothing in this section was run by the assistant.

- Environment: commit `7c0d7fca562e8226ad4fa95ce39e4bae13e832c4` (`7c0d7fc`), clean working tree; uv 0.11.19; Python 3.13.13 (MSC v.1944, AMD64); Windows 10 Pro 10.0.19045; CPU `Pentium(R) Dual-Core E6500 @ 2.93GHz`.
- `uv sync --locked` removed `pytest-asyncio` from the environment, because this branch is based on the pre-Phase-2A lock. This is expected and unrelated to packaging.
- Smoke script output: all 8 checks `ok`, exit code 0. Package versions: numpy 2.3.5, pillow 12.3.0, fpzip 1.2.5, flatbuffers 25.12.19, grpclib 0.4.9, betterproto 2.0.0b7, drawthings-py 0.4.0, strictyaml 1.7.3, tqdm 4.70.1.
- TLS handshake (P3): `TLSv1.3`, certificate subject `commonName=compute.drawthings.ai`, validated by the system trust store.

The source-run rows apply to the smoke script as of commit `7c0d7fc`. Later changes to the script, made so a frozen build can import every module, keep the source-run checks logically identical; the frozen rows (W9-W13) have not been run.

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
- Whether bundling `drawthings-py` is permissible; see [decision 0001](../decisions/0001-drawthings-py-license.md).
- Any claim about application behavior beyond the smoke script, which is not DTFlow application code.

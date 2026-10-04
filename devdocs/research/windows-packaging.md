# Windows packaging investigation

**Status:** Checklist ready; findings table is filled only from results the owner reports. Nothing below is a finding until a row says otherwise.  
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
| W1 | Source-run smoke passes (P2) | Not run | | | |
| W2 | NumPy 2.3.5 loads; CPU features recorded | Not run | | | |
| W3 | fpzip round trip | Not run | | | |
| W4 | Pillow PNG round trip | Not run | | | |
| W5 | SQLite version; WAL and synchronous FULL work | Not run | | | |
| W6 | TLS: system trust resources and bundled CA present | Not run | | | |
| W7 | TLS handshake to cloud host with system trust (optional, P3) | Not run | | | |
| W8 | `%LOCALAPPDATA%` writable | Not run | | | |
| W9 | PyInstaller one-file executable builds | Not run | | | |
| W10 | PyInstaller executable runs on a clean path without Python or Git | Not run | | | |
| W11 | Nuitka executable builds (compiler requirement noted) | Not run | | | |
| W12 | Frozen output matches source-run output | Not run | | | |
| W13 | Executable size, startup time, antivirus/SmartScreen behavior recorded | Not run | | | |
| W14 | Qt plugins, Credential Manager | Blocked | | | Qt and the credential service do not exist yet |
| W15 | Installer path (any tool) | Not run | | | Optional; see comparison below |

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

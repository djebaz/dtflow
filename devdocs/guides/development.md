# Development tools and conventions

See [AGENTS.md](../../AGENTS.md) for repository rules, [current development status](../current_dev_status.md) for implementation orientation, and [testing](testing.md) for validation commands, and [version bump and release](releasing.md) for the uv recipe and owner build handoff.

## Design principles

These are implementation requirements, not a claim that every feature exists today.

- **Reproducible.** Record resolved prompts, seeds, model and LoRA identifiers, effective parameters and output references.
- **Honest about outcomes.** Require successful terminal status, valid final outputs and durable saving before reporting application success. Record ambiguous remote attempts without silently replaying them; keep previews distinct.
- **Explicit selection.** Do not fall back to another backend or credential after failure. Initial cloud generation does not opt into PAYG; subscription quota may still be consumed.
- **Separated layers.** Keep UI, services, backend-neutral core, persistence and provider adapters separate. Provider types stay inside the integration boundary.
- **Offline by default.** Ordinary tests must not submit real or potentially billable generation.

## Environment and compatibility evidence

Run project tools through `uv run` so they use the project environment. The lockfile records resolved dependencies. The current compatibility path pins NumPy 2.3.5 for the tested legacy Windows machine; see the [recorded dependency findings](../research/drawthings-grpc-proof.md#observed-compatibility-evidence). Source execution does not establish packaged Windows compatibility.

## Agent environment setup

The requirement is a usable environment for the checkout. Custom environment
startup or a preconfigured image may already provide the prerequisites; proof
that the setup script ran is not required.

In every agent shell, including the first, apply these settings before running
uv or Python:

```bash
export UV_LINK_MODE=copy
export QT_QPA_PLATFORM=offscreen
export UV_SYSTEM_CERTS=true
export UV_PYTHON_PREFERENCE=only-managed
```

From the target checkout root, check the selected Python version, SQLite,
development dependencies and headless Qt initialization:

```bash
uv run --frozen python -c 'import pathlib, sqlite3, sys; import dtflow, pytest, pytestqt; from PySide6.QtWidgets import QApplication; expected = pathlib.Path(".python-version").read_text().strip(); actual = ".".join(map(str, sys.version_info[:len(expected.split("."))])); assert actual == expected, (actual, expected); app = QApplication([]); print("Environment ready:", sys.version.split()[0], "SQLite", sqlite3.sqlite_version)'
```

`uv run --frozen` synchronizes the project environment from the committed lockfile
as needed. A successful check is sufficient to proceed with the requested
command, such as `uv run --frozen pytest`; do not rerun setup merely because this
is a new agent session. This smoke check establishes basic test-environment
readiness, not native desktop or full application acceptance. Report any failure
and identify the missing prerequisite.

When prerequisites are missing in a disposable development/agent environment,
use the setup script, then repeat the readiness check:

```bash
bash tools/setup-agent.sh
```

The default project root is the checkout containing the script, independent of the
working directory. To target another checkout, pass its absolute path:

```bash
DTFLOW_PROJECT_ROOT="/path/to/dtflow" bash "/path/to/tools/setup-agent.sh"
```

The target must contain `pyproject.toml`, `uv.lock` and `.python-version`.
Python comes from that file rather than a hard-coded checkout path or Python
version. Setup installs a uv-managed interpreter, runs `uv sync --frozen` and
prints the Python/SQLite versions. It does not update the lockfile. Printing a
SQLite version is diagnostic information, not acceptance of persistence behavior.

On Debian/Ubuntu Linux, setup installs `libegl1`, `libgl1`, `libxkbcommon0`,
`libdbus-1-3` and `libfontconfig1` through apt-get. Root or non-interactive
sudo is required. Both APT commands use `-o APT::Sandbox::User=root` to avoid
switching to the `_apt` user in disposable containers that block user/group
changes. This does not grant container permissions or package-mirror access;
APT failures still stop setup. Other Linux distributions fail clearly and require manual
system-library setup. macOS and Windows Bash (Git Bash/MSYS/Cygwin) skip the Linux
package step; this platform routing is not evidence of native runtime acceptance.
Windows needs Bash to run this file; it is not a PowerShell script.

Existing uv is reused. When missing, Python with venv/pip support bootstraps
`uv==0.12.23` in ignored `tmp/agent-bootstrap/`, separate from the project's
`.venv`. Setup requires network access to package repositories and Python
downloads. Report setup failures without hiding them with `|| true`, and stop
dependent commands while their prerequisites remain unavailable. If a redundant
setup attempt fails because root/sudo is unavailable but the readiness check
passes, continue with the requested command; installed system libraries do not
need to be reinstalled.

These settings copy cached packages instead of linking, run Qt headlessly, use
system TLS certificates for uv, and select only uv-managed Python. Setup exports
them for its own subprocesses; executing it with Bash does not export them back
into the caller or future shells. If uv was bootstrapped, also export
`PATH="$DTFLOW_PROJECT_ROOT/tmp/agent-bootstrap/bin:$PATH"` after setting
`DTFLOW_PROJECT_ROOT` to the absolute checkout path (use `Scripts` instead of
`bin` in Windows Bash).

Use `uv run --frozen` for later agent commands to retain the committed lockfile.
Unset `QT_QPA_PLATFORM` or select the native platform when explicitly performing
interactive desktop acceptance. This setup is not intended for immutable
production/prebuilt runtimes. It runs no tests, UI renders, training, inference,
model downloads or provider calls.

## Diagnostic probes

The diagnostic probes' help is available without submitting generation:

```bash
uv run python tools/drawthings/drawthings_probe.py --help
uv run python tools/drawthings/grpc_capabilities_probe.py --help
```

Metadata probe option pairs are `-f/--filename`, `-s/--sha256`, `-z/--size`, `-a/--api-base` and `-m/--matrix` for `get_byom_probe.py`; the local fingerprint probe uses `-f/--filename` and `-s/--known-sha256`, alongside `-a/--host` and `-p/--port`. Existing long spellings remain supported.

The gRPC capability probe is read-only/non-generative: it calls only Echo, FilesExist, Pubkey, and Hours and can save a sanitized JSON report. GenerateImage and UploadFile are deliberately excluded.

Diagnostic tools can contact a real Draw Things server or direct DT+ Cloud Compute and submit potentially billable generation, including through Bridge mode. Read `--help` and select the intended operation deliberately. The direct-cloud probe reads `DRAWTHINGS_API_KEY` from the environment; it does not accept the key on the command line. Keep credentials and tokens out of logs and saved request metadata.

## Desktop development and packaging

Read [DESIGN.md](../../DESIGN.md) before adding or changing a desktop tab. Canonical Designer forms are under `src/dtflow/ui/forms/`; shared QSS is under `src/dtflow/resources/styles/`. Business rules and backend protocols belong behind shared services, not in UI or generated Designer Python.

Use the [Windows desktop build guide](building.md) for the canonical GUI build and owner acceptance steps. [Windows packaging findings](../research/windows-packaging.md) retain historical console trials and target-machine evidence. Source execution does not establish packaged compatibility. The initial distribution target is the Qt application, not a separate packaged CLI.

## Offline Images presentation

The Images tab and standalone launcher load the same canonical gallery form and
fixture controller. Use the shared QSS/token pipeline; do not create a separate
palette or copy presentation behavior into a developer-only implementation.

```bash
uv run python tools/ui/preview_gallery_detail.py --view grid
uv run python tools/ui/preview_gallery_detail.py --view list
uv run python tools/ui/preview_gallery_detail.py --state details
uv run python tools/ui/preview_gallery_detail.py --state raw
```

These commands show bundled illustrative samples without output discovery or
provider execution. The [preview plan](../plans/2026-10-05-phase-8a-ui-preview.md)
records implemented controls and validation limits. Actual saved-output services
remain governed by the [viewer plan](../plans/2026-10-05-phase-8a-viewer.md).

## Pixel-statistics detector PoC

The standalone [image detector tool](../../tools/images/README.md) reports provisional noise/blank flags and colour/spatial statistics from local images. It is read-only, uncalibrated and separate from export and job status; owner-labelled evaluation remains pending.

## Working directories

Application state and durable outputs use `.dtflow/`; retained live evidence uses `test-runs/`; diagnostics use `logs/`; disposable scratch uses `tmp/`. These repository-development paths are ignored by Git. See [runtime layout](../architecture/runtime-layout.md) before changing ownership or cleanup behavior.

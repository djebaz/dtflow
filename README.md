# DTFlow

Desktop workflows, generation and automation for [Draw Things](https://drawthings.ai).

DTFlow (DTF) is a Windows-first project for reproducible generation, durable jobs and queues, and workflows.
It targets direct DT+ Cloud Compute and a local Draw Things gRPC server through one backend-neutral contract.

**Current status:** Phase 2A provides an offline fake backend, shared async services, SQLite attempt records,
and a minimal headless command that saves a fixture PNG and reproducibility metadata. Phase 2B adds the local
Draw Things gRPC adapter, scripted TLS/gRPC fixtures and an optional non-generative live connectivity/inventory check.
Phase 2C adds the direct DT+ adapter, focused offline authentication fixtures and optional live token/inventory and
complete generation smoke checks. Deployed-cloud validation remains unverified. Real-backend CLI generation selection,
durable rolling queues and Qt remain planned. See the [implementation plan](devdocs/plans/initial.md),
[release roadmap](devdocs/plans/releases.md) and [recorded evidence](devdocs/research/drawthings-grpc-proof.md).

## Design principles

These are implementation requirements, not a claim that every feature exists today.

- **Reproducible.** Record resolved prompts, seeds, model and LoRA identifiers, effective parameters and output references.
- **Honest about outcomes.** Require successful terminal status, valid final outputs and durable saving before reporting
  application success. Record ambiguous remote attempts without silently replaying them; keep previews distinct.
- **Explicit selection.** Do not fall back to another backend or credential after failure. Initial cloud generation
  does not opt into PAYG; subscription quota may still be consumed.
- **Separated layers.** Keep UI, services, backend-neutral core, persistence and provider adapters separate.
  Provider types stay inside the integration boundary.
- **Offline by default.** Ordinary tests must not submit real or potentially billable generation.

## Getting started

Requirements: Python 3.13 and [uv](https://docs.astral.sh/uv/). Run these commands from the repository root:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The source checkout also provides a thin pytest wrapper with explicit test groups:

```powershell
uv run dtflow test                 # Offline suite (default)
uv run dtflow test -all -live      # Offline suite plus live connectivity
uv run dtflow test -all -gen       # Offline suite plus live generation
uv run dtflow test -live           # Live connectivity only
uv run dtflow test -gen            # Live generation only
```

Option pairs are `-all/--all`, `-live/--live`, and `-gen/--generate`; groups can be combined.
Live connectivity runs local Draw Things and DT+ checks. It requires `DRAWTHINGS_API_KEY` for DT+, plus
`DTFLOW_LIVE_HOST` (the local server's IP) and `DTFLOW_LIVE_PORT` in the environment;
`DTFLOW_LIVE_SHARED_SECRET` is optional. Local trust defaults to the bundled Draw Things CA, so no manual CA setup
is needed. Explicit `DTFLOW_LIVE_CA_FILE`/`DTFLOW_LIVE_SERVER_NAME` settings select strict alternative trust.
The flag supplies the live opt-in without a separate marker or opt-in environment variable. Requested live checks
fail clearly if unconfigured. The DT+ generation smoke requires an exact discovered `DTFLOW_LIVE_MODEL`. It submits one 256×256, four-step
image with seed 42 and guidance 1, without LoRA or PAYG fields, and may consume subscription credits.
`-live` never calls `/authenticate` or submits generation. The wrapper preserves pytest output/exit status
and requires the checkout's development dependencies. Ordinary `uv run pytest` remains offline with live tests gated.

```powershell
# DRAWTHINGS_API_KEY must already be set in your environment.
uv run dtflow test -live
$run = Get-ChildItem test-runs -Directory | Sort-Object Name -Descending | Select-Object -First 1
Get-Content "$($run.FullName)\inventory\cloud_inventory.json"
$env:DTFLOW_LIVE_MODEL = "exact-model-identifier-from-inventory"
uv run dtflow test -gen
uv run dtflow test -all -live -gen
```

The model must support the stated txt2img workload; listing alone does not establish compatibility.
Each invocation retains evidence under `test-runs/<run-id>/`: flat `inventory/local_inventory.json` and
`cloud_inventory.json` snapshots, `state/attempts.sqlite3` when generating, `outputs/<attempt-id>/`, and one
`results.json` with build identity and check outcomes. The run path is printed after testing. Inventory JSON includes
`cache_version`; these snapshots are retained evidence, not application cache. Failed/unknown attempts remain recorded.
Each explicit `-gen` run requests a new generation; unknown attempts are never automatically replayed.

Run project tools through `uv run` so they use the project environment. The lockfile records resolved dependencies.
The current compatibility path pins NumPy 2.3.5 for the tested legacy Windows machine; see the
[recorded dependency findings](devdocs/research/drawthings-grpc-proof.md#observed-compatibility-evidence).
Source execution does not establish packaged Windows compatibility.

The diagnostic probes' help is available without submitting generation:

```sh
uv run python tools/drawthings/drawthings_probe.py --help
uv run python tools/drawthings/grpc_capabilities_probe.py --help
```

The gRPC capability probe is read-only/non-generative: it calls only Echo, FilesExist, Pubkey, and Hours and can save a sanitized JSON report. GenerateImage and UploadFile are deliberately excluded.

Diagnostic tools can contact a real Draw Things server or direct DT+ Cloud Compute and submit potentially billable
generation, including through Bridge mode. Read `--help` and select the intended operation deliberately.
The direct-cloud probe reads `DRAWTHINGS_API_KEY` from the environment; it does not accept the key on the command line.
Keep credentials and tokens out of logs and saved request metadata.

The minimal Phase 2A entry point is offline and requires explicit fake selection:

```sh
uv run dtflow --help
uv run dtflow check --backend fake
uv run dtflow models --backend fake --refresh
uv run dtflow generate --backend fake --prompt "Offline fixture" --seed 0
uv run dtflow attempts
```

The fixture is a deterministic color swatch; no model inference occurs. State is stored in `.dtflow/attempts.sqlite3`
and outputs under `.dtflow/outputs/<attempt-id>/`. Use `--state` to choose another local state directory and `--out`
to choose the output root. `--scenario lost` exercises an uncertain outcome; inspection never resubmits it.
The full real-backend CLI is Phase 3 work. Run state on a local filesystem, with a patched SQLite runtime as described
in the [backend contract](devdocs/architecture/backend-contract.md#durable-state-and-layer-enforcement).

## Repository layout

```text
src/dtflow/   application contracts, services, persistence and fake backend
tests/        automated checks
tools/        diagnostic and development tooling
devdocs/      plans, research, architecture, decisions and release notes
```

Repository conventions reserve ignored `.dtflow/` for application state/outputs, `test-runs/` for retained live-test
evidence, `logs/` for diagnostics and `tmp/` for disposable scratch. See the
[runtime layout](devdocs/architecture/runtime-layout.md) for current paths and planned cache/packaged paths.

## Documentation

- [Implementation plan](devdocs/plans/initial.md) and [release roadmap](devdocs/plans/releases.md)
- [Research](devdocs/research/) and [architecture notes](devdocs/architecture/)
- [Pending release notes](devdocs/releases/unreleased.md)
- [Repository contract](AGENTS.md): naming, architecture, CLI, logging, testing and workflow rules

Documents under `devdocs/plans/` describe planned work, not shipped behavior. Research distinguishes recorded,
user-reported, source-inspected and unverified behavior.

## Contributing

Read [AGENTS.md](AGENTS.md) first, and use a feature branch and pull request.
Dependency changes go through uv and keep `pyproject.toml` and `uv.lock` synchronized.

## License

DTFlow is licensed under **GPL-3.0-only**. See [LICENSE](LICENSE).
The project adopted GPL-3.0-only before its first public release, aligning DTFlow with the
GPL-3.0-only `drawthings-py` runtime dependency. Third-party dependencies retain their own licenses
and notices; packaged releases must include the corresponding compliance material.

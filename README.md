# DTFlow

Desktop workflows, generation and automation for [Draw Things](https://drawthings.ai).

DTFlow (DTF) is a Windows-first project for reproducible generation, durable jobs and queues, and workflows.
It targets direct DT+ Cloud Compute and a local Draw Things gRPC server through one backend-neutral contract.

**Current status:** Phase 2A provides an offline fake backend, shared async services, SQLite attempt records,
and a minimal headless command that saves a fixture PNG and reproducibility metadata. Phase 2B adds the local
Draw Things gRPC adapter, scripted TLS/gRPC fixtures and an optional non-generative live connectivity/inventory check.
Phase 2C adds the direct DT+ adapter, focused offline authentication fixtures and optional live token/inventory and
complete generation smoke checks. The owner reports deployed token/inventory and one bounded generation passed at an
earlier checkpoint using `z_image_turbo_1.0_q6p.ckpt`. This covers that workload only; final-head Windows validation,
broad model/LoRA compatibility, production cancellation latency, charging/refunds and a packaged, stamped release build remain unverified (a frozen one-file CLI has run on the target machine; see the [packaging findings](devdocs/research/windows-packaging.md)). Phase 3A exposes those adapters through the source-run CLI (offline-validated; the owner reports one successful local
and one successful DT+ plain generation through it, plus one local LoRA generation after a first attempt interrupted by an iOS app freeze). Durable rolling queues, cloud LoRA upload/reuse and Qt remain planned. See the [implementation plan](devdocs/plans/initial.md),
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
uv run dtflow test -live -local    # Live connectivity, local Draw Things only
uv run dtflow test -live -cloud    # Live connectivity, DT+ only
```

Option pairs are `-all/--all`, `-live/--live`, `-gen/--generate`, `-local/--local` and `-cloud/--cloud`; groups can be
combined. `-local` and `-cloud` limit the live checks to one side (and imply `-live` when given alone); `-local` cannot
be combined with `-gen`, which is DT+ only. A cloud-only run omits the local LoRA inventory comparison, and each scope
requires only its own settings.
Live connectivity runs local Draw Things and DT+ checks. It requires `DRAWTHINGS_API_KEY` for DT+, plus
`DTFLOW_LIVE_HOST` (the local server's IP) and `DTFLOW_LIVE_PORT` in the environment;
`DTFLOW_LIVE_SHARED_SECRET` is optional. Local trust defaults to the bundled Draw Things CA, so no manual CA setup
is needed. Explicit `DTFLOW_LIVE_CA_FILE`/`DTFLOW_LIVE_SERVER_NAME` settings select strict alternative trust.
The flag supplies the live opt-in without a separate marker or opt-in environment variable. Requested live checks
fail clearly if unconfigured. The DT+ generation smoke requires an exact discovered `DTFLOW_LIVE_MODEL`. It submits one 256×256, four-step
image with seed 42 and guidance 1, without LoRA or PAYG fields, and may consume subscription credits.
The live inventory check enriches local LoRA availability using a bounded metadata-only BYOM lookup. Without
`DTFLOW_LIVE_LORA`, it records the selected LoRA's availability, including legitimate upload-required/unknown states;
an empty local LoRA inventory omits enrichment. Set `DTFLOW_LIVE_LORA` only to a known already-available cloud fixture
when you want to assert reuse. It retains `inventory/lora_inventory.json` when enrichment runs.
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

Metadata probe option pairs are `-f/--filename`, `-s/--sha256`, `-z/--size`, `-a/--api-base` and `-m/--matrix`
for `get_byom_probe.py`; the local fingerprint probe uses `-f/--filename` and `-s/--known-sha256`, alongside
`-a/--host` and `-p/--port`. Existing long spellings remain supported.

The gRPC capability probe is read-only/non-generative: it calls only Echo, FilesExist, Pubkey, and Hours and can save a sanitized JSON report. GenerateImage and UploadFile are deliberately excluded.

Diagnostic tools can contact a real Draw Things server or direct DT+ Cloud Compute and submit potentially billable
generation, including through Bridge mode. Read `--help` and select the intended operation deliberately.
The direct-cloud probe reads `DRAWTHINGS_API_KEY` from the environment; it does not accept the key on the command line.
Keep credentials and tokens out of logs and saved request metadata.

## Command line (Phase 3A, source-run)

`uv run dtflow` selects a backend explicitly (`-b/--backend fake|local|cloud`); there is never a fallback from one
backend to another. Results are JSON on stdout; progress and diagnostics go to stderr.

```sh
uv run dtflow --help
uv run dtflow check    -b fake                                   # offline fixture, no model inference
uv run dtflow check    -b local -e 192.168.1.20:7859             # Draw Things gRPC; secret from DRAWTHINGS_SHARED_SECRET
uv run dtflow check    -b cloud                                  # DT+; key from DRAWTHINGS_API_KEY
uv run dtflow models   -b local -e HOST:PORT -k lora            # inventory with provenance; -k filters by kind
uv run dtflow generate -b fake -p "Offline fixture" -s 0
uv run dtflow generate -b cloud -m EXACT_MODEL -p "a cat" -l EXACT_LORA:0.8 -r -u cat.json
uv run dtflow generate -b cloud -c cat.json -s 7                  # reuse a config; explicit flags override it
uv run dtflow attempts                                            # inspect durable attempts; never resubmits
uv run dtflow settings -j 2                                       # persist the cloud concurrency maximum (1-4, default 4)
```

`generate`, `check` and `models` are the only commands that contact a backend; `generate -b local|cloud` submits a
real, potentially billable generation, so run it deliberately. `check` and `models` are non-generative.
Local/cloud generation requires an exact `-m/--model` listed by the server's inventory (refreshed before each run).

- **Options.** Generation: `-p/--prompt`, `-n/--negative`, `-m/--model`, `-s/--seed`, `-w/--width`, `-y/--height`,
  `-i/--steps`, `-g/--guidance`, `-a/--sampler`, `-l/--lora ID:WEIGHT` (repeatable, explicit weight required),
  `-r/--prefix`, `-o/--out`, `-t/--timeout`. Connection: `-b/--backend`, `-e/--endpoint HOST:PORT` (local),
  `-d/--state`, `-nc/--no-cache` (the CLI keeps no inventory cache yet, so the flag currently only guarantees none is
  read or written). Top level: `-v/--version`, `-x/--dev` (more log detail only), `-q/--logs DIR`. Fake-only: `-f/--scenario`.
- **Configuration.** `-c/--config` reads a versioned, credential-free generation config (`dtflow.generation-config`,
  version 1) or a previous run's `metadata.json`; `-u/--export` writes the effective config of the current request
  before dispatch. Precedence is default < file < explicit flag, and the metadata records where each field came from.
  Seed `0` is preserved. Files containing credential-like keys or a newer version are rejected.
- **LoRAs.** Each selection needs an exact inventory identifier and weight. Preflight compares the LoRA's reported
  architecture with the model's: a confirmed mismatch or a LoRA missing from the inventory stops the request before
  any attempt is recorded; unknown architecture is reported and allowed, never silently dropped. `-r/--prefix`
  explicitly prepends the server-supplied trigger prefixes; the prompt is never changed otherwise. Preflight is
  metadata evidence only: successful generation does not prove a LoRA was applied. The official base/refiner and
  Kandinsky/SD-v1 exception rules are not pinned yet, so only whole-model LoRAs on a base model are supported.
- **Exit codes.** `0` success; `2` invalid input, config, preflight or missing credentials (nothing was dispatched);
  `3` backend rejection or failure with a known outcome; `4` unknown remote outcome (never replayed); `5` local
  state, output or deadline failure; `130` local interruption. Nonzero results print one JSON error line on stderr.
- **Logging.** One shared logging setup writes rotating UTF-8 `dtflow-<session>.log` files under repository `logs/`
  (or `%LOCALAPPDATA%\DTFlow\logs\` outside a checkout), with session/attempt correlation and bounded retention.
  Prompts, credentials, headers and payloads are not logged; a log failure is reported once and never changes the command.

The fixture backend is a deterministic color swatch; no model inference occurs. State is stored in
`.dtflow/attempts.sqlite3` and outputs under `.dtflow/outputs/<attempt-id>/`. Run state on a local filesystem, with a
patched SQLite runtime as described in the
[backend contract](devdocs/architecture/backend-contract.md#durable-state-and-layer-enforcement).

## Repository layout

```text
src/dtflow/   application contracts, services, composition root, persistence, backends and CLI
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

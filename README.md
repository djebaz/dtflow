# DTFlow

Desktop workflows, generation and automation for [Draw Things](https://drawthings.ai).

DTFlow (DTF) is a Windows-first project for reproducible generation, durable jobs and queues, and workflows.
It targets direct DT+ Cloud Compute and a local Draw Things gRPC server through one backend-neutral contract.

**Current status:** the repository contains diagnostic tooling, offline probe checks and development specifications.
The application backends, durable service path, user-facing CLI and Qt interface are being developed; they are not
shipped features. See the [implementation plan](devdocs/plans/initial.md), [release roadmap](devdocs/plans/releases.md)
and [recorded evidence](devdocs/research/drawthings-grpc-proof.md) for scope and verification.

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

Run project tools through `uv run` so they use the project environment. The lockfile records resolved dependencies.
The current compatibility path pins NumPy 2.3.5 for the tested legacy Windows machine; see the
[recorded dependency findings](devdocs/research/drawthings-grpc-proof.md#observed-compatibility-evidence).
Source execution does not establish packaged Windows compatibility.

The diagnostic probe's help is available without submitting generation:

```sh
uv run python tools/drawthings/drawthings_probe.py --help
```

Diagnostic tools can contact a real Draw Things server or direct DT+ Cloud Compute and submit potentially billable
generation, including through Bridge mode. Read `--help` and select the intended operation deliberately.
The direct-cloud probe reads `DRAWTHINGS_API_KEY` from the environment; it does not accept the key on the command line.
Keep credentials and tokens out of logs and saved request metadata.

A user-facing `uv run dtflow` entry point is planned; it is not available yet.

## Repository layout

```text
src/dtflow/   application package and architectural skeleton
tests/        automated checks
tools/        diagnostic and development tooling
devdocs/      plans, research, architecture, decisions and release notes
```

Repository conventions reserve ignored `.dtflow/` for local runtime state, `logs/` for diagnostics and `tmp/`
for scratch outputs. Packaged per-user paths are planned separately.

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

See [LICENSE](LICENSE). Third-party dependencies carry their own licenses.
The distribution implications of the GPL-3.0-only drawthings-py dependency remain unresolved in the
[development plan](devdocs/plans/phase-2-backends.md#phase-2d--packaging-licensing-and-feature-inventory).

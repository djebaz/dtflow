# DTFlow

Desktop workflows, generation and automation for [Draw Things](https://drawthings.ai).

DTFlow (DTF) is a Windows-first desktop application for reproducible image generation with
local Draw Things and direct DT+ Cloud Compute. Its Qt interface brings connection management,
generation parameters, LoRA selection, saved outputs and durable queues into one workspace.
Batch planning lets you compare prompts, seeds and other parameters while keeping each request inspectable.

## Getting started

Requirements: Python 3.13 and [uv](https://docs.astral.sh/uv/).
From the repository root, synchronize the environment and launch the desktop:

```bash
uv sync
uv run dtflow gui
```

Start with the **fake** provider for an offline tour, or configure local Draw Things or DT+ on the
Connection page. See the [desktop guide](devdocs/guides/desktop.md) for connection setup and usage.

## Repository layout

```text
src/dtflow/   application code: core, services, persistence, backends, CLI and Qt UI
tests/       automated checks
tools/       diagnostic, development and packaging tools
devdocs/     usage guides, plans, architecture, research, decisions and release notes
```

Designer forms live under `src/dtflow/ui/forms/`; shared styles and resources live under
`src/dtflow/resources/`. See [DESIGN.md](DESIGN.md) before changing the desktop UI.

## Documentation

- [Desktop guide](devdocs/guides/desktop.md) — connections, generation, outputs and saved settings.
- [Queues and batch plans](devdocs/guides/queues.md) — queue controls, concurrency and parameter comparisons.
- [CLI reference](devdocs/guides/cli.md) — automation and development interface.
- [Testing](devdocs/guides/testing.md) and [development tools](devdocs/guides/development.md).
- [Architecture](devdocs/architecture/) and [runtime layout](devdocs/architecture/runtime-layout.md).
- [Implementation plan](devdocs/plans/initial.md) and [release roadmap](devdocs/plans/releases.md).
- [Research and verification evidence](devdocs/research/) and [release notes](devdocs/releases/).

## Contributing

Read [AGENTS.md](AGENTS.md) and the [current development status](devdocs/current_dev_status.md)
for repository rules and coding orientation. Use a feature branch and pull request.
Dependency changes go through uv, with `pyproject.toml` and `uv.lock` kept together.

## License

DTFlow is licensed under **GPL-3.0-only**. See [LICENSE](LICENSE) and the
[dependency licensing decision](devdocs/decisions/0001-drawthings-py-license.md).

# DTFlow

Desktop workflows, generation and automation for [Draw Things](https://drawthings.ai).

DTFlow (DTF) is a Windows-first desktop application for reproducible image generation with
local Draw Things and direct DT+ Cloud Compute. Its Qt interface brings connection management,
generation parameters, LoRA selection, saved outputs and durable queues into one workspace.
Batch planning lets you compare prompts, seeds and other parameters while keeping each request inspectable.

## Features

### A connected desktop workspace

Connection, Generate and Queues share one interface, with persistent navigation and an
Inspector for request details and output evidence. Choose local Draw Things or direct DT+
Cloud Compute, check the connection, and refresh model and LoRA inventory without generating.
Windows Credential Manager stores a saved cloud API key outside ordinary configuration files.

### Image generation and inspection

Compose prompts and control the model, seed, dimensions, steps, guidance and sampler.
After inventory refresh, classified model discovery narrows the active provider's available
checkpoints by All/Image/Video, base-model family and exact checkpoint; Prime models are shown
by default and an explicit option adds Niche models. Checkpoint selection opens a searchable list with case-insensitive substring matching; an explicit custom-identifier action keeps unclassified checkpoints usable without DTFlow guessing their family or compatibility.

Select LoRAs with explicit weights and an optional trigger-prefix action. For a classified
checkpoint, the Add chooser is narrowed to available LoRAs with known family/mode compatibility;
compatibility feedback still distinguishes confirmed mismatches from unknown metadata rather
than guessing.

Follow progress and previews in an image-first workspace, then inspect saved results with
fit, zoom and pan. Output details link images to their generation metadata and provide
folder/path actions. Save and load generation configurations to reuse a setup.

### Durable queues and controlled comparisons

Create a paused queue from the generation form, then start, pause, resume or cancel it.
Queue items retain their resolved requests, attempts and saved outputs, so execution remains
inspectable after restart. Concurrency controls respect backend and shared resource limits.

Build seed sweeps or Pair/Cross batches over prompts, models, seeds, dimensions and other
parameters. Review the item count and dry-run table before creating a batch; vary only the
parameters you intend to compare.

### Explicit cloud assets and offline exploration

Prepare converted cloud LoRA files through a separate, confirmed upload action and inspect
local asset receipts for reuse. Generation never hides an upload inside an ordinary request.

The fake provider lets you explore the same forms, progress, outputs and queue controls
offline. Fixture images and configurable simulation outcomes require no model inference.

The Images tab browses committed saved outputs in Grid, List and Detail views with a
shared compact header, 25–250% thumbnail scaling and a full-height Detail filmstrip.
Positive-prompt search, model/LoRA/date/dimensions/aspect filters, deterministic sorting
and refresh use the same bounded read-only gallery services as the CLI. Detail exposes
recorded provenance and safe raw metadata without inventing unavailable values.
See the [desktop guide](devdocs/guides/desktop.md#images).

## Design principles

- **Desktop-first.** Keep the main workflow in a responsive graphical workspace, with
  details available when needed instead of forcing every action through a terminal.
- **Reproducible by design.** Record resolved prompts, seeds, model and LoRA identifiers,
  effective parameters and output references so each result has an inspectable origin.
- **Durable, not disposable.** Preserve attempts, queue state and saved outputs separately
  from rebuildable cache. A restart must not silently discard results or replay uncertain work.
- **Honest outcomes.** Previews are not finished images. Report success only after terminal
  success, output validation and durable saving; stopping local waiting is not remote cancellation.
- **Explicit choices.** Do not silently switch backend or credential, modify prompts, upload
  assets or enable PAYG after a failure. Real generation may still consume subscription credits.
- **Shared foundations.** Keep presentation, orchestration, persistence and provider protocols
  separate. The GUI and automation interface use the same generation and queue services.
- **Safe exploration.** Make offline fixtures useful for development and interaction checks,
  without submitting real generation just to exercise application plumbing.

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
tests/        automated checks
tools/        diagnostic, development and packaging tools
devdocs/      usage guides, plans, architecture, research, decisions and release notes
```

Designer forms live under `src/dtflow/ui/forms/`; shared styles and resources live under
`src/dtflow/resources/`. See [DESIGN.md](DESIGN.md) before changing the desktop UI.

## Documentation

- [Desktop guide](devdocs/guides/desktop.md) — connections, generation, outputs and saved settings.
- [Queues and batch plans](devdocs/guides/queues.md) — queue controls, concurrency and parameter comparisons.
- [CLI reference](devdocs/guides/cli.md) — automation and development interface.
- [Testing](devdocs/guides/testing.md), [development tools](devdocs/guides/development.md) and [Windows builds](devdocs/guides/building.md).
- [Architecture](devdocs/architecture/), [runtime layout](devdocs/architecture/runtime-layout.md) and the [design system reference](devdocs/design-system/README.md).
- [Implementation plan](devdocs/plans/initial.md) and [release roadmap](devdocs/plans/releases.md).
- [Research and verification evidence](devdocs/research/) and [release notes](devdocs/releases/).

## Contributing

Read [AGENTS.md](AGENTS.md) and the [current development status](devdocs/current_dev_status.md)
for repository rules and coding orientation. Use a feature branch and pull request.
Dependency changes go through uv, with `pyproject.toml` and `uv.lock` kept together.

## License

DTFlow is licensed under **GPL-3.0-only**. See [LICENSE](LICENSE) and the
[dependency licensing decision](devdocs/decisions/0001-drawthings-py-license.md).

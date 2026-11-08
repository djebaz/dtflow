# DTFlow

Desktop workflows, generation and automation for [Draw Things](https://drawthings.ai).

DTFlow (DTF) is a Windows-first desktop application for reproducible image generation with
local Draw Things and direct DT+ Cloud Compute. It brings connection management, generation
parameters, LoRA selection, saved outputs, durable queues and diagnostics into one Qt workspace,
and records every request so each image has an inspectable origin.

## Features

- **One workspace.** Connection, Generate, Queues, Images and Logs share one window with a
  persistent status bar, inline confirmations and notices, and an Inspector for request details.
- **Local or cloud.** Use local Draw Things over gRPC or direct DT+ Cloud Compute. Model and LoRA
  inventory refreshes without generating; cloud API keys are stored in Windows Credential Manager (several can be saved and switched; see devdocs/guides/credentials.md).
- **Generation with provenance.** Pick a checkpoint from a searchable, classified catalogue, add
  LoRAs with explicit weights, and control seed, size, steps, guidance and sampler. Results record
  prompts, seeds, model and LoRA identifiers and effective parameters. An offline DT+ Compute Units
  estimate is shown for supported text-to-image checkpoints.
- **Durable queues and comparisons.** Save and load reusable text-to-image queue recipes; compose queues with prompt, seed and parameter variations, start, pause, resume or cancel them, and inspect each item's resolved request, attempts and saved result. Queue state survives restarts without silently replaying uncertain work.
- **Saved image gallery.** Browse outputs in Grid, List and Detail views with prompt search,
  filters and zoom; reuse or regenerate saved settings; export JPEG copies with readable filenames;
  move originals to Trash or delete them with explicit confirmation.
- **Searchable logs.** Browse, filter and export structured application diagnostics from the
  desktop. Sensitive values such as tokens, signed URLs and image data are kept out of logs.
- **Offline exploration.** A fake provider exercises the same forms, progress, outputs and queues
  without model inference or network access.
- **Automation CLI.** The same generation, queue and gallery services are available from the
  command line for scripting and development.
- **Native workflows.** Build saved graphs of generation, face-mask detection, inpainting and refinement steps, with per-node settings, branch-level progress and explicit retry. See the [Workflows guide](devdocs/guides/workflows.md) for supported nodes and acceptance limits.
- **Scripts.** Inspect saved JavaScript, fill one Setup form and run through durable queues with linked outputs and optional bounded parallel batches. The [Scripts guide](devdocs/guides/scripts.md) describes the supported hooks and limits.

The [desktop guide](devdocs/guides/desktop.md) and [queues guide](devdocs/guides/queues.md) describe each
workspace in detail.

## Getting started

### Windows executable

Published releases attach a single-file Windows GUI executable to
[GitHub Releases](https://github.com/djebaz/dtflow/releases). Download it, verify it against the
attached `SHA256SUMS.txt`, and run it; no Python installation is needed.

### From source

Requirements: Python 3.13 and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync
uv run dtflow gui
```

Pick the **fake** provider on the Connection page for an offline tour, or configure local
Draw Things or DT+. The [desktop guide](devdocs/guides/desktop.md) covers connection setup,
launch options and each workspace.

At startup, DTFlow checks the restored connection once in the background. Its colored power icon at the far right of the bottom bar opens Connection and retains the last check result; it is not continuous monitoring. Images opens from the button above ordinary inspectors (above the centre on narrow windows), hidden in Images and Logs. Missing connection configuration stays unchecked, and startup never generates images or resumes queues.

> Generating on local Draw Things or DT+ submits real work and may consume subscription credits.
> DTFlow never switches backend or credential, uploads assets or enables pay-as-you-go on its own.

### Command line

```bash
uv run dtflow --help
uv run dtflow check -b fake
uv run dtflow generate -b fake -p "Offline fixture" -s 0
```

See the [CLI reference](devdocs/guides/cli.md) for backends, options and examples.

## Design principles

- **Desktop-first.** The main workflow lives in a responsive graphical workspace, with details
  available when needed.
- **Reproducible by design.** Resolved prompts, seeds, model and LoRA identifiers, effective
  parameters and output references are recorded for every result.
- **Durable, not disposable.** Attempts, queue state and saved outputs are kept separately from
  rebuildable cache. A restart must not silently discard results or replay uncertain work.
- **Honest outcomes.** Previews are not finished images. Success is reported only after terminal
  success, output validation and durable saving; stopping local waiting is not remote cancellation.
- **Explicit choices.** No silent backend or credential switch, prompt modification, asset upload
  or pay-as-you-go after a failure.
- **Shared foundations.** Presentation, orchestration, persistence and provider protocols stay
  separate, and the GUI and CLI use the same services.
- **Safe exploration.** Offline fixtures support development and interaction checks without
  submitting real generation.

## Documentation

**Using DTFlow**

- [Desktop guide](devdocs/guides/desktop.md): connections, generation, images, logs and settings
- [Queues and batch plans](devdocs/guides/queues.md): queue controls, concurrency and comparisons
- [Workflows](devdocs/guides/workflows.md): graph authoring, saved files, branch execution and model-download consent
- [Scripts](devdocs/guides/scripts.md): Inspect > Setup > Run Script, optional bounded parallel batches and LoRA readiness
- [Portable Krea2 mixer](devdocs/guides/krea2-mixer.md): cached LoRA discovery, single-form Setup, saved settings and explicit chunks
- [CLI reference](devdocs/guides/cli.md): automation and development interface
- [Release notes](devdocs/releases/) and the [release roadmap](devdocs/plans/releases.md)

**Developing DTFlow**

- [Standalone CPU segmentation/OBB experiments](tools/images/segmentation-pocs.md): local image-tool recipes, review-only annotations and verification limits; not desktop model support

- [Development tools](devdocs/guides/development.md), [testing](devdocs/guides/testing.md),
  [Windows builds](devdocs/guides/building.md) and [releasing](devdocs/guides/releasing.md)
- [Architecture](devdocs/architecture/), including the [runtime layout](devdocs/architecture/runtime-layout.md),
  [structured diagnostics](devdocs/architecture/structured-diagnostics.md) with its
  [event catalogue](devdocs/architecture/diagnostic-event-schema.md) and
  [JSON Schema](devdocs/schemas/diagnostic-record-v1.schema.json), and
  [image-operation preparation](devdocs/architecture/image-operations.md) (developer-only), and the
  [scripting layer](devdocs/architecture/scripting.md) (developer-only)
- [Efficient Qt model/view patterns](devdocs/guides/qt-model-view-patterns.md)
- [DESIGN.md](DESIGN.md), the [design system](devdocs/design-system/README.md), the
  [Logs workspace specification](devdocs/design-system/logs-workspace.md) and [UI mockups](devdocs/mockups/)
- [Current development status](devdocs/current_dev_status.md), the [implementation plan](devdocs/plans/initial.md),
  [plans](devdocs/plans/),
  [research](devdocs/research/) and [decisions](devdocs/decisions/)
- [Scripts implementation](devdocs/architecture/scripting.md) and [parallel-batch plan](devdocs/plans/2026-11-03-parallel-script-batch.md). Earlier [desktop preview corrections](devdocs/plans/2026-11-02-scripts-review-fixes.md) and [independent-review alignment](devdocs/plans/2026-11-02-scripts-independent-review.md) record historical UI-only slices, not the current runtime boundary.

## Repository layout

```text
src/dtflow/   application code: core, services, persistence, backends, CLI and Qt UI
tests/        automated checks
tools/        diagnostic, development and packaging tools
devdocs/      guides, architecture, plans, research, decisions and release notes
```

## Contributing

Read [AGENTS.md](AGENTS.md) for repository rules and [DESIGN.md](DESIGN.md) before changing the
desktop UI. Work on a feature branch and open a pull request; CI runs lock, lint and test checks.
Manage dependencies through uv and commit `pyproject.toml` and `uv.lock` together.

## License

DTFlow is licensed under **GPL-3.0-only**. It bundles the MIT-licensed Acorn parser unmodified; see
[its notice](src/dtflow/scripting/resources/acorn.NOTICE.txt). For DTFlow itself see [LICENSE](LICENSE) and the
[dependency licensing decision](devdocs/decisions/0001-drawthings-py-license.md).

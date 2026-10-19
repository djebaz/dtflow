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
Inventory choices return with their provider; last-observed model/LoRA counts and timestamps
remain visible through refresh failures and cache-only startup.
Windows Credential Manager stores a saved cloud API key outside ordinary configuration files.

Action confirmations and notices stay inside the main window. Confirm changes the original
button to red for Delete/Clear and mint/teal (matching Auto-refresh ON) otherwise; Cancel stays in the shared bottom overlay. Searchable checkpoint and one-step
LoRA/weight editors avoid extra windows, while file/folder pickers remain native.
Confirmations, progress and results share one thin bottom overlay without resizing content. Success and amber warning/partial/stopped overlays fade after six seconds with state-colored countdowns and draining bars, paused by overlay hover or focus; red errors remain until dismissed and informational inspections close with × or a click outside. Essential information stays visible; Export and cloud LoRA Prepare (after file selection) start without a second confirmation. The overlay lifetime only hides its presentation; it never changes saved operation evidence or remote execution state.

### Image generation and inspection

Compose prompts and control the model, seed, dimensions, steps, guidance and sampler; samplers are filtered to the selected model and Draw Things' official presets are available read-only.
After inventory refresh, classified model discovery narrows the active provider's available
checkpoints by All/Image/Video, base-model family and exact checkpoint; Prime models are shown
by default and an explicit option adds Niche models. In Local mode, `Include DT+ Cloud` can add classified catalogue checkpoints that a Draw Things bridge may omit from its reported local inventory. Checkpoint selection opens a searchable list with case-insensitive substring matching; an explicit custom-identifier action keeps unclassified checkpoints usable without DTFlow guessing their family or compatibility.

Import user-owned custom LoRA metadata as JSON from the Connection page; DTFlow validates it before atomically replacing the durable custom catalogue under the application state directory. Select LoRAs with explicit weights and an optional trigger-prefix action. For a classified
checkpoint, the Add chooser is narrowed to available LoRAs with known family/mode compatibility;
compatibility feedback still distinguishes confirmed mismatches from unknown metadata rather
than guessing.

Write prompts in a compact two-line field with Generate image in its header; track real steps and Stop waiting locally through the shared overlay while the preview has no duplicate controls. Follow previews in an image-first workspace, then inspect saved results with
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
shared compact header, 25–250% thumbnail scaling (100% uses a 320 px display size), List thumbnails whose width and height follow zoom, and a full-height Detail filmstrip. Grid, List and the filmstrip share one 600 px maximum thumbnail decode per image; the main Detail viewer reads the original saved output at native resolution.
Positive-prompt search, model/LoRA/date/dimensions/aspect filters, deterministic sorting
and refresh use the same bounded read-only gallery services as the CLI.
When live gallery reads are unavailable across Windows/WSL1, Images can browse the last published read-only snapshot with its timestamp. Publication happens after a generation/queue session and on clean close only when state changed or no snapshot exists; snapshot browsing cannot authorize export, editing or removal. See the [snapshot design and limitations](devdocs/architecture/gallery-snapshot.md).
Windows and WSL 1 can share one Windows-hosted state: each side shows, copies, opens and exports the other's managed outputs through verified read access without rewriting saved references, and Delete removes eligible managed originals across that boundary through a guarded Windows helper. WSL 2 Windows-drive mounts are not supported yet. See the [desktop guide](devdocs/guides/desktop.md#images) for prerequisites and limitations.
Detail exposes recorded provenance and safe raw metadata without inventing unavailable values. Saved settings can populate Generate without dispatch, or be explicitly regenerated as a fresh lineage-linked attempt. Draw Things `custom_configs.json` presets are durable and globally imported/exported from Connection, while Generate can save/load named presets. Connection also measures and clears only disposable cache categories; inventory snapshots and source image computations use disk cache, while gallery thumbnails use memory and a disposable disk cache. Cleanup is confirmed and coordinated with active cache operations; saved originals and exported copies are never cache-cleanup targets.
Connection and Images share one saved-image export hub; Images opens it with an always-enabled Export panel toggle (blue while open), independent of checked images, in a right-hand column beside the listing. Starting an Images export still requires checked images. Copy checked images, one image or a filtered batch into one folder as JPEGs with readable filenames and Windows-visible generation properties. Prompt privacy and optional JSON sidecars keep sharing explicit; original PNGs and saved history stay untouched. A default-on checkbox sorts pixel-statistics-flagged copies into `noise/` and `blank/`; turn it off for a completely flat folder. These are advisory flags, not generation-status verdicts. Full output identities in filenames let repeat exports skip existing copies before image processing, with bounded recycle-aware availability checks; source computations are cached centrally across destinations, without an export-folder index; new Windows files retain the saved generation-completion date in their creation time and EXIF.
The Images split action selects **Trash** (Recycle Bin on Windows) or **Delete** (permanent), remembering separate Windows/WSL choices. Trash remains the default; unavailable Trash never silently becomes permanent deletion. Permanent removal always needs fresh inline irreversible-action confirmation. Both modes also remove an image edit's raw provider output, preserve metadata, exported copies and generation history, and use durable intent to prevent replaying uncertain removals. A card whose original was deleted outside DTFlow shows **File missing** and can be cleared with Delete. Recovered files are not automatically reintroduced from path/timestamp matches; verified recovery remains future work. See the [removal architecture and platform limits](devdocs/architecture/image-recycling.md).
See the [desktop guide](devdocs/guides/desktop.md#images) and
[image export guide](devdocs/guides/desktop.md#export-saved-images).

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
- [Image-operation preparation](devdocs/architecture/image-operations.md) — developer-only permit-gated single-shot path; no default image capability or accepted editor/checkpoint support.
- [Architecture](devdocs/architecture/), [runtime layout](devdocs/architecture/runtime-layout.md) and the [design system reference](devdocs/design-system/README.md).
- [Structured diagnostics architecture](devdocs/architecture/structured-diagnostics.md) — current centralized logging foundation and the proposed JSONL ownership/evidence boundaries (PR #105, not yet shipped).
- [Diagnostic event catalogue and migration inventory](devdocs/architecture/diagnostic-event-schema.md) with [proposed JSON Schema v1](devdocs/schemas/diagnostic-record-v1.schema.json) — machine-readable record fields and source-based event mappings (PR #105, not yet shipped).
- [Efficient Qt model/view patterns](devdocs/guides/qt-model-view-patterns.md) — bounded table updates, worker/search integration and external reference implementations.

- [Implementation plan](devdocs/plans/initial.md) and [release roadmap](devdocs/plans/releases.md).
- [Research and verification evidence](devdocs/research/) and [release notes](devdocs/releases/).

### Desktop interaction mockup

The [PR #58 inline interaction mockup](devdocs/mockups/dtflow_inline_panels.html)
illustrates main-window confirmations, activity overlays, the Generate prompt,
and Images selection/export behavior. Its example images and state transitions
are illustrative only: the mockup cannot read saved outputs, export files,
delete originals, or contact Draw Things.

## Contributing

Read [AGENTS.md](AGENTS.md) and the [current development status](devdocs/current_dev_status.md)
for repository rules and coding orientation. Use a feature branch and pull request.
Dependency changes go through uv, with `pyproject.toml` and `uv.lock` kept together.

## License

DTFlow is licensed under **GPL-3.0-only**. See [LICENSE](LICENSE) and the
[dependency licensing decision](devdocs/decisions/0001-drawthings-py-license.md).


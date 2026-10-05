# Development tools and conventions

See [AGENTS.md](../../AGENTS.md) for repository rules, [current development status](../current_dev_status.md) for implementation orientation, and [testing](testing.md) for validation commands.

## Design principles

These are implementation requirements, not a claim that every feature exists today.

- **Reproducible.** Record resolved prompts, seeds, model and LoRA identifiers, effective parameters and output references.
- **Honest about outcomes.** Require successful terminal status, valid final outputs and durable saving before reporting application success. Record ambiguous remote attempts without silently replaying them; keep previews distinct.
- **Explicit selection.** Do not fall back to another backend or credential after failure. Initial cloud generation does not opt into PAYG; subscription quota may still be consumed.
- **Separated layers.** Keep UI, services, backend-neutral core, persistence and provider adapters separate. Provider types stay inside the integration boundary.
- **Offline by default.** Ordinary tests must not submit real or potentially billable generation.

## Environment and compatibility evidence

Run project tools through `uv run` so they use the project environment. The lockfile records resolved dependencies. The current compatibility path pins NumPy 2.3.5 for the tested legacy Windows machine; see the [recorded dependency findings](../research/drawthings-grpc-proof.md#observed-compatibility-evidence). Source execution does not establish packaged Windows compatibility.

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

## Working directories

Application state and durable outputs use `.dtflow/`; retained live evidence uses `test-runs/`; diagnostics use `logs/`; disposable scratch uses `tmp/`. These repository-development paths are ignored by Git. See [runtime layout](../architecture/runtime-layout.md) before changing ownership or cleanup behavior.

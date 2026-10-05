# Repository contract

These instructions apply throughout `dtflow`.

## Naming

- Use `DTFlow` for the human-facing product, `DTF` as the short product name, and `dtflow` for Python packages, commands, and machine-facing artifact names.
- Keep established external Draw Things, DT+, model, checkpoint, LoRA, scheduler, and sampler names unchanged unless a compatibility/migration reason requires a mapping.

## Before changing behavior

- Read this file, `README.md`, `devdocs/current_dev_status.md`, `devdocs/plans/initial.md`, `devdocs/plans/releases.md`, `devdocs/releases/unreleased.md`, relevant architecture/research documents, and the current canonical files before editing.
- Treat repository files and verified upstream interfaces as the source of truth. Do not invent runtime behavior, compatibility, benchmarks, test results, or verification.
- Preserve unrelated working behavior.

## Architecture

- Keep UI, orchestration, backend-neutral models, persistence, workers, and generation backends separated by clear boundaries.
- Keep protobuf/gRPC and provider-specific types inside the relevant backend integration.
- The UI must not directly own Draw Things gRPC or future DT+ protocol behavior.
- Preserve reproducibility: record prompts, seeds, model/checkpoint identifiers, LoRA settings, relevant generation parameters, outputs, and durable job state where applicable.
- Keep long-running workflows resumable and inspectable.

## Python environment and dependencies

- DTFlow uses Astral `uv` as the canonical Python project, environment, and dependency workflow.
- Keep project metadata and declared dependencies in `pyproject.toml` and commit `uv.lock` for reproducible development and builds.
- Use `uv sync` to create or synchronize the project environment.
- Run project tools and Python commands through `uv run` where practical, for example `uv run pytest`, `uv run ruff check .`, and `uv run dtflow`.
- Add, remove, and update dependencies through the uv-managed project configuration rather than ad-hoc `pip install` commands.
- When a change adds, removes, or changes a third-party dependency, use the appropriate uv workflow and keep `pyproject.toml` and `uv.lock` synchronized in the same change.
- Put development-only tools in the dev dependency group rather than runtime dependencies.
- Commit dependency metadata and lockfile changes together with the code that requires them.
- Do not manually edit `uv.lock`; regenerate it through uv.
- Do not maintain a parallel hand-edited `requirements.txt`. Add a compatibility export only if a concrete external tool or deployment requirement needs one, and treat it as generated output rather than a source of truth.
- Prefer `uv run` for validation so checks execute in the locked project environment.
- Do not mutate a supposedly reproducible environment with arbitrary package upgrades during normal validation.
- When changing `pyproject.toml` tool configuration, keep documented commands and CI behavior aligned with the canonical configuration.

## Runtime state and cache

- Follow [the runtime layout](devdocs/architecture/runtime-layout.md). During repository development, application state and backend-neutral outputs live under ignored `.dtflow/`; retained live-test evidence lives separately under ignored `test-runs/<run-id>/`. Offline tests use temporary directories.
- Keep cache flat within each type: inventory uses `cache/inventory/local_inventory.json` and `cloud_inventory.json`, plus `lora_inventory.json`, not nested backend/endpoint directories. Use these fixed names for the current single-local/single-cloud configuration and retained live evidence. Endpoint/server identities belong inside JSON as observations, not stable filename keys. When cache reads are introduced, refresh/invalidate on endpoint or credential changes; never reuse account-dependent evidence across changed credentials. Include integer `cache_version` from the inventory writer's `CACHE_VERSION`; future cache readers treat missing/mismatched versions as cache misses and rebuild, without migrating. Durable state has its own schema policy.
- Keep outputs grouped by attempt, not backend. Create directories only when needed; cache cleanup must preserve outputs, durable state and retained test evidence.
- Packaged Windows builds must use an appropriate per-user application-data location by default rather than writing beside the executable.
- Keep disposable cache separate from durable state. Organize cache by type so categories can be measured and purged independently.
- Provide cache cleanup early enough to prevent generated images and derived assets from growing without bounds.
- Support a cache-bypass launch option `-nc` / `--no-cache`; it must disable cache reads and writes for that run.

## Logging and development diagnostics

- Use one shared Python logging setup for CLI, Qt, services and backend adapters. Prefer the standard-library logging module; presentation layers must not maintain separate logging engines.
- In repository development, write diagnostic files under repository-root `logs/`, which remains ignored by Git. Keep application state under `.dtflow/` and disposable scratch work under `tmp/`; logs do not change those conventions.
- Packaged Windows builds default to `%LOCALAPPDATA%\DTFlow\logs\`, never the repository, executable directory or current working directory. Allow an explicit log-location override when implemented.
- Write readable UTF-8 text logs with timestamps, severity, component and session identity. Include version/source commit and backend identity; attach local job/attempt IDs when available to correlate concurrent work.
- Normal mode records important lifecycle events and errors. Explicit development mode adds sanitized connection/auth-refresh events, request validation, state transitions, stream events, timings and exception tracebacks. It changes diagnostic detail only, never generation, retry or billing behavior.
- Bound file size and retention through rotation. Avoid rotation collisions between simultaneous processes and keep log I/O from blocking Qt interaction.
- Never log API keys, tokens, authorization headers, credentials or raw image/Base64 payloads. Exclude full prompts and other sensitive request data by default; sanitize exception details as well as ordinary messages.
- Logs are diagnostic evidence, not the durable job/attempt store or proof of successful generation. Log cleanup must not delete durable state or outputs.
- Report log-location/write failures clearly without exposing secrets or triggering generation retries. Logging setup must not consume subscription credits or enable external network access.

## CLI

- The initial CLI runs from source for development and automation; packaging initially targets the Qt application, without a separate packaged CLI.
- Provide a `dtflow` entry point for normal development/application launch rather than requiring long source paths.
- For normal user-facing options, expose a canonical pair: a stable, unique lowercase short alias and a concise long form.
- Reserve `-h` / `--help` for the standard help action. Do not use uppercase short aliases such as `-H` or `-P` as collision workarounds, and avoid repurposing widely expected flags when a clearer mnemonic is available.
- Prefer a single lowercase letter for short aliases. If the obvious letter is already taken or would be confusing, choose another clear lowercase mnemonic. Use lowercase multi-letter aliases only when needed to preserve an established contract or when a single-letter choice would be materially worse; `-nc` / `--no-cache` is an intentional existing example.
- Keep long option names short and recognizable, usually one word where that remains clear, for example `--host`, `--port`, `--cloud`, `--preset`, `--jobs`, `--full`, `--serial`, `--out`, and `--conn`. Use a multi-word long form only when the shorter form would be ambiguous; avoid verbose names such as `--full-workload` when `--full` is sufficient.
- Short and long forms of the same option must have identical semantics, defaults, validation, and side effects. `--help` should advertise only the current canonical spellings.
- When renaming an established option, preserve the old spelling as a hidden compatibility alias where practical and safe. Documentation and examples should use only the canonical spelling.
- Keep every documented flag synchronized with the implemented CLI and `--help`.

## UI and Qt

- Use PySide6 and Qt Widgets initially.
- Read the root `DESIGN.md` before adding or changing a desktop tab; follow its shared tokens, interaction states, layout principles and page checklist. Keep its token snapshot aligned with the runtime `tokens.json` when values change.
- Keep Qt Designer `.ui` files canonical and tracked under `src/dtflow/ui/forms/`.
- Keep the shared application QSS under `src/dtflow/resources/styles/`; apply it once at startup and prefer stable object names or dynamic properties over scattered inline styles.
- Do not put business logic or backend access into generated Designer Python.
- Treat `previewHost`, `queueItemsView` and similar Designer objects as presentation seams for handwritten widgets/models; promoted/custom widgets must not own backend protocols or service rules.
- Do not block the Qt event loop with network, generation, or other long-running work.

## Generation safety

- Do not assume LoRA, checkpoint, quantization, scheduler, sampler, or runtime compatibility without evidence.
- Keep prompts, seeds, dimensions, and other relevant parameters controlled in comparisons unless intentionally varied.
- Do not run expensive or potentially billable training/inference merely to validate syntax, configuration, or application plumbing.
- Never expose credentials, tokens, authorization headers, or sensitive payloads in logs.
- Shared cloud credential resolution selects `DRAWTHINGS_API_KEY` when present, otherwise the saved Windows credential. Empty/invalid selected credentials fail clearly; never silently switch sources after validation or authentication failure.

## Tests

- Use `pytest` as the standard test runner. Use `pytest-qt` when Qt-specific behavior needs direct coverage.
- Separate unit tests from live integration tests.
- Ordinary test execution must never submit a real Draw Things or cloud generation accidentally. Live generation tests must be explicit and separately gated.
- Prefer extending an existing contract-focused test over accumulating one-off regression tests for every implementation change.
- Test each behavior at one authoritative layer. Higher layers cover only new behavior or necessary composition of tested layers; reuse requires the same implementation path. Acceptance bullets do not require one test each.
- Live checks may replace duplicated end-to-end fixture coverage, but retain deterministic tests for scheduling, persistence, uncertainty and controlled failure boundaries. Measure added runtime; test counts and test/source line ratios are not quality gates.
- Report only checks actually run.

## Packaging

- Target a Windows single-executable distribution initially, while keeping source execution supported for development.
- The canonical `tools/packaging/build_exe.py` build targets `launch_gui.py`: open the desktop directly, use Windows windowed mode, and preserve shared logging/build identity, canonical forms/resources and secure credential dependencies. Keep source CLI execution separate. Follow [the build guide](devdocs/guides/building.md); compilation alone does not establish Windows executable acceptance.
- Treat `build/` and `dist/` as generated artifacts.
- Do not commit model/checkpoint artifacts or duplicate large model files.
- Keep build inputs and version metadata reproducible.

## Version and build identity

- Keep one authoritative application version and one shared build-identity source; CLI, Qt, packaging and output metadata must consume them rather than maintain independent copies.
- Display the application version plus a short Git commit ID in CLI version output and a discreet Qt About/status area.
- Name distributed builds `dtflow-<version>-g<short-sha>-<platform>-<arch>.<ext>`, using the actual source revision, target architecture and artifact type. An eventual installer follows the same identity convention.
- Capture the full source commit and dirty-worktree status at build time and bundle them with the application. Packaged execution must not require Git or a repository checkout.
- Build public releases from a clean checkout. Development builds must disclose local modifications; when revision metadata is unavailable, report it as unknown rather than inventing a commit ID.
- Persist application version and full source commit in generation metadata where available, and include build identity in smoke-test reports so results identify the executable actually exercised.
- Check that packaged filenames, CLI output and Qt display agree when those delivery paths exist. Do not claim runtime or packaging verification from documentation checks alone.
- Use manual release downloads initially; an integrated updater is outside the initial scope.

## Documentation and releases

- Keep `README.md` GUI-first: product presentation, implemented feature descriptions and design principles, minimal setup/desktop launch, repository layout and documentation navigation. Link the CLI only as an automation/development reference; put detailed usage, testing, diagnostics and safety instructions in `devdocs/guides/`, not README status sections or implementation history.
- Keep `devdocs/current_dev_status.md` short and dated, mapping implemented, remaining and added scope to `devdocs/plans/initial.md`. Update it when scope changes; distinguish code presence from acceptance and link detailed evidence rather than duplicating it. Repository source and verified evidence remain authoritative.
- Follow [the release roadmap](devdocs/plans/releases.md) for usable version milestones and acceptance gates; planned numbers are not shipped releases.
- Version durable state schemas separately from application versions. Preserve state and outputs on supported migrations; reject unsupported schemas clearly and never replay ambiguous attempts during an upgrade.
- Track pending changes in `devdocs/releases/unreleased.md`.
- Follow [the version-bump and release guide](devdocs/guides/releasing.md) for uv metadata/lock updates and release sequencing. The owner builds and smoke-tests the final Windows executable after the release PR is squash-merged; wait for owner acceptance before tagging/publishing.
- When releasing a version, move the relevant entries into `devdocs/releases/v<version>.md` and reset `unreleased.md`.
- Update the relevant plan, architecture, research, and release documentation when behavior changes.

## Workflow

- Avoid committing directly to `main`. Normal repository changes should go through a feature branch and pull request. Direct commits to `main` are reserved for exceptional, low-risk cases where a pull request would add no meaningful review or safety value.
- For non-trivial work, create the feature branch and pull request early, after an initial coherent commit, so implementation progress and review context are visible from the start.
- Commit regularly at meaningful checkpoints and group related file changes into coherent commits. Do not create one commit per file or per trivial edit unless the change is genuinely independent.
- Keep the working PR history reasonably reviewable even when the final merge will be squashed; squash merge is not a substitute for excessively fragmented development history.
- Placeholder directories are intentional in this repository; use `.gitkeep` only while a directory has no real tracked files.
- For runnable Python changes, validate syntax, imports, configuration parsing, and `--help` where applicable.
- At the end of implementation work, state what changed, what was verified, what was not verified, whether training/inference ran, and whether any potentially billable action was performed.

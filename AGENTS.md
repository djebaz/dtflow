# Repository contract

These instructions apply throughout `dtflow`.

## Naming

- Use `DTFlow` for the human-facing product, `DTF` as the short product name, and `dtflow` for Python packages, commands, and machine-facing artifact names.
- Keep established external Draw Things, DT+, model, checkpoint, LoRA, scheduler, and sampler names unchanged unless a compatibility/migration reason requires a mapping.

## Before changing behavior

- Read this file, `README.md`, `devdocs/plans/initial.md`, `devdocs/releases/unreleased.md`, relevant architecture/research documents, and the current canonical files before editing.
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

- During repository development, local runtime state lives under repository-root `.dtflow/`; it is ignored by Git.
- Packaged Windows builds must use an appropriate per-user application-data location by default rather than writing beside the executable.
- Keep disposable cache separate from durable state. Organize cache by type so categories can be measured and purged independently.
- Provide cache cleanup early enough to prevent generated images and derived assets from growing without bounds.
- Support a cache-bypass launch option `-nc` / `--no-cache`; it must disable cache reads and writes for that run.

## CLI

- Provide a `dtflow` entry point for normal development/application launch rather than requiring long source paths.
- Common launch options should have stable, unique short aliases and descriptive long forms.
- Keep every documented flag synchronized with the implemented CLI and `--help`.

## UI and Qt

- Use PySide6 and Qt Widgets initially.
- Keep Qt Designer `.ui` files canonical and tracked.
- Do not put business logic or backend access into generated Designer Python.
- Do not block the Qt event loop with network, generation, or other long-running work.

## Generation safety

- Do not assume LoRA, checkpoint, quantization, scheduler, sampler, or runtime compatibility without evidence.
- Keep prompts, seeds, dimensions, and other relevant parameters controlled in comparisons unless intentionally varied.
- Do not run expensive or potentially billable training/inference merely to validate syntax, configuration, or application plumbing.
- Never expose credentials, tokens, authorization headers, or sensitive payloads in logs.

## Tests

- Use `pytest` as the standard test runner. Use `pytest-qt` when Qt-specific behavior needs direct coverage.
- Separate unit tests from live integration tests.
- Ordinary test execution must never submit a real Draw Things or cloud generation accidentally. Live generation tests must be explicit and separately gated.
- Prefer extending an existing contract-focused test over accumulating one-off regression tests for every implementation change.
- Report only checks actually run.

## Packaging

- Target a Windows single-executable distribution initially, while keeping source execution supported for development.
- Treat `build/` and `dist/` as generated artifacts.
- Do not commit model/checkpoint artifacts or duplicate large model files.
- Keep build inputs and version metadata reproducible.

## Documentation and releases

- Keep `README.md` focused on current usage, setup, repository layout, and validation rather than per-version history.
- Track pending changes in `devdocs/releases/unreleased.md`.
- When releasing a version, move the relevant entries into `devdocs/releases/v<version>.md` and reset `unreleased.md`.
- Update the relevant plan, architecture, research, and release documentation when behavior changes.

## Workflow

- Keep `main` stable. Use feature branches and pull requests for substantial changes.
- Placeholder directories are intentional in this repository; use `.gitkeep` only while a directory has no real tracked files.
- For runnable Python changes, validate syntax, imports, configuration parsing, and `--help` where applicable.
- At the end of implementation work, state what changed, what was verified, what was not verified, whether training/inference ran, and whether any potentially billable action was performed.

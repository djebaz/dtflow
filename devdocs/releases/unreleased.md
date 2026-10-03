# Unreleased

Pending changes for the next DTFlow release.

## Added

- Initial repository conventions and development plan.
- LF-first line-ending policy.
- DTFlow local-state, cache, generated-output, model-artifact, IDE, and coding-agent ignore rules.
- Initial architectural directory skeleton.
- Repository contract in `AGENTS.md`.
- Release-note workflow based on this unreleased file.
- Initial `pyproject.toml` with the uv-native project metadata, pytest development dependencies, and Ruff lint/format configuration.
- Repository rules requiring dependency changes to keep `pyproject.toml` and `uv.lock` synchronized through uv.

## Changed

- Nothing yet.

## Fixed

- Configure Hatchling to package the `src/dtflow` application package so editable `uv sync` installs can build DTFlow.

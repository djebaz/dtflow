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
- Python 3.13 development baseline via `.python-version`.

## Changed

- Limit the declared Python compatibility to Python 3.11-3.13 after Windows environment validation: Python 3.12 and 3.13 sync successfully, while Python 3.14 currently fails while building the transitive `fpzip==1.2.5` dependency used by `drawthings-py==0.4.0`.

## Fixed

- Configure Hatchling to package the `src/dtflow` application package so editable `uv sync` installs can build DTFlow.

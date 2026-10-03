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

- Use Python 3.13 as the current DTFlow development/runtime baseline.
- Temporarily pin NumPy to 2.3.5 for the verified Windows/Python 3.13 environment. NumPy 2.5.3 fails to load `_multiarray_umath` there, while 2.3.5 imports successfully. Keep this pin narrow and revisit it when the upstream Windows binary issue is resolved.
- Python 3.14 is currently excluded because the `drawthings-py==0.4.0` dependency chain fails while building `fpzip==1.2.5` in the tested Windows environment.

## Fixed

- Configure Hatchling to package the `src/dtflow` application package so editable `uv sync` installs can build DTFlow.

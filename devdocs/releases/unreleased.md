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
- Project-level uv prerelease policy so normal `uv lock`, `uv sync`, and `uv run` commands use the same resolver mode required by the current dependency graph.
- Project-level uv NumPy compatibility override for the bounded `drawthings-py>=0.4.0,<0.5.0` dependency line.

## Changed

- Use Python 3.13 as the current DTFlow development/runtime baseline.
- Python 3.14 is currently excluded because the `drawthings-py==0.4.0` dependency chain fails while building `fpzip==1.2.5` in the tested Windows environment.
- Use NumPy 2.3.5 for the current Draw Things Python client path on the tested legacy Windows Pentium E6500 machine. NumPy 2.4.4 and 2.5.3 fail while loading `_multiarray_umath` there; NumPy 2.3.5 imports successfully, `drawthings-py==0.4.0` imports successfully, and the real gRPC/model-browser connectivity probe succeeds. Generation/result decoding with this NumPy override is not yet verified.

## Fixed

- Configure Hatchling to package the `src/dtflow` application package so editable `uv sync` installs can build DTFlow.

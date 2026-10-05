# Plan: GUI-first Windows executable

## Scope

- In: stamped one-file GUI build, desktop-first launcher, Qt/data/credential packaging inputs, no-console diagnostics, build/smoke instructions and status synchronization.
- Out: version bump/release, UI redesign, backend behavior, live inference/uploads and a separately distributed CLI.

## Action items

- [x] Add a thin desktop launcher using the shared Qt application, build identity and logging.
- [x] Make build_exe.py target that launcher with Windows windowed mode, preserve forms/resources and provider trust files, and bundle Windows credential dependencies.
- [x] Preserve canonical dev/log/cache options and readable help/version/errors without a console; keep source CLI unchanged.
- [x] Update packaging instructions, development status and unreleased notes.
- [x] Run syntax/import/help/configuration and lint/format checks; final tests and Windows executable smoke remain with the owner.
- [x] Push the implementation and ready PR #29 without merging or releasing.

## Decisions and Design Changes

- 2026-10-05: The canonical artifact becomes the desktop application, not a console CLI requiring a gui subcommand. Keep the original CLI trial launcher for historical evidence only; source uv run dtflow remains supported.
- 2026-10-05: PyInstaller windowed mode has no standard streams; provide safe third-party stream handles and route application startup/logging errors to GUI diagnostics, without displaying raw exceptions or secrets.
- 2026-10-05: Retain existing version/stamping policy. This PR does not claim to ship 0.3.0; the owner identifies portable Windows acceptance as the remaining release gate.

## Open questions

None blocking. The exact executable still needs owner acceptance on the target Windows machine.

## Validation

Tests and smoke tests are not run in this implementation workflow. Use the locked environment for syntax, import, configuration/help, Ruff and whitespace checks. No packaging success or target-Windows acceptance is claimed until actually performed.

## Implementation evidence

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed (162 Python files).
- `uv run python -m compileall -q` on the four changed/new Python files: passed.
- Imports of the shared Qt application and desktop bootstrap, plus desktop option parsing: passed without starting QApplication or submitting work.
- Source desktop launcher help/version, builder help and unchanged source `dtflow gui --help`: passed.
- Local Markdown destinations/anchors, JSON examples and whitespace checks: passed.
- Tests and smoke tests were not run by the agent; final validation remains with the owner. No training/inference, uploads or billable generation were performed by the agent.
- Separate owner-started PyInstaller processes were observed in the shared checkout during implementation. Their current artifact/stamp includes dirty WIP; no packaging success is claimed here and they were not stopped or cleaned by the agent. Rebuild the committed source revision for release acceptance.

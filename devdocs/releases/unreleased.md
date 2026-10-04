# Unreleased

Pending changes for the next DTFlow release.

## Added

- `tools/packaging/build_exe.py` stamped one-file build: records the full source commit and clean/dirty status in an ignored generated `dtflow/_buildinfo.py`, so a frozen executable reports its identity without Git, and names the artifact `dtflow-<version>-g<short-sha>-<platform>-<arch>.exe`. Source runs still report `unknown`. Verified offline by unit tests and one Linux PyInstaller build in the agent sandbox; no Windows build, generation or billable action was run.

## Changed

## Fixed

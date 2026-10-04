# Unreleased

Pending changes for the next DTFlow release.

## Added

- `tools/packaging/build_exe.py` stamped one-file build: records the full source commit and clean/dirty status in an ignored generated `dtflow/_buildinfo.py`, so a frozen executable reports its identity without Git, and names the artifact `dtflow-<version>-g<short-sha>-<platform>-<arch>.exe`. Source runs still report `unknown`. Verified offline by unit tests and one Linux PyInstaller build in the agent sandbox; no Windows build, generation or billable action was run.

- Durable queues and rolling dispatch (Phase 3B, queue half): `dtflow queue add|list|show|start|pause|resume|cancel|override|cap`. Queue, item, control and event tables join the attempts database (schema version 2; a release 0.1.0 database migrates atomically after a recoverable `attempts.pre-v2.sqlite3` copy, newer versions are refused). The dispatch owner is the process holding the state directory's ownership lock; other processes read and post controls only. Items reserve slots before any attempt exists, refill immediately on release, honour the queue and backend maximum, restore paused after restart, and keep `unknown` reservations blocking until an explicit recorded override. Verified offline only (3B-O1 to O5 and O10); no live queue run, generation or billable action was performed.

- Minimal Qt shell (Phase 4): `dtflow gui` launches a PySide6 Widgets window loaded from the canonical Designer file `src/dtflow/ui/forms/main_window.ui`. It selects fake/local/cloud, runs the bounded non-generative connection check through the shared composition root on a background asyncio thread (the GUI thread never awaits backend work) and shows healthy/auth-failed/unreachable/unknown status. API-key entry, replace and removal go through the shared `SavedCredentials` service backed by Windows Credential Manager via `keyring`; any other keyring backend is refused (no plaintext fallback), and `DRAWTHINGS_API_KEY` keeps precedence. The CLI stays environment-only and never imports Qt. The status bar shows the shared version and commit identity and the state path from the new `dtflow.paths`. New dependencies: PySide6-Essentials and keyring (runtime), pytest-qt (dev). Verified offline on Linux with the offscreen platform; the real Credential Manager boundary, a visible Windows launch and the packaged executable were not exercised, and no generation or billable action was run.

- Qt UX design foundation: a centralized Splitvids-inspired dark QSS now styles the source-run shell through semantic object names and dynamic status properties instead of inline status colors. Canonical `main_window.ui`, `generation_page.ui` and `queue_page.ui` sources define the three-area desktop information architecture, accessibility names and presentation seams under `src/dtflow/ui/forms/`. The native Windows frame follows Splitvids' DWM attribute 20/19 dark-title-bar path before and after first show. Only the existing Phase 4 connection shell is wired; the Phase 5 generation and queue forms are design sources, not usable workflows. The forms were mechanically converted with `pyside6-uic`; tests, another visible Windows review and packaged-resource validation remain pending for this branch.

## Changed

## Fixed

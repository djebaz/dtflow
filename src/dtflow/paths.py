"""Application state location shared by the Qt shell and any later packaged entry point."""

import os
from pathlib import Path


def application_state_dir() -> Path:
    """Repository-root ``.dtflow`` from a source checkout; per-user application data otherwise."""
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and (root / "src" / "dtflow").is_dir():
        return root / ".dtflow"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DTFlow"
    return Path.home() / ".local" / "state" / "dtflow"

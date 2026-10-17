"""Names the runtime that reads a state directory it may share with another environment."""

import os
import platform
import sys


def environment_key() -> str:
    """Return ``windows``, ``wsl`` or the platform name, so shared state can keep per-runtime values."""
    if os.name == "nt":
        return "windows"
    if "microsoft" in platform.release().lower():
        return "wsl"
    return sys.platform

"""Open a local folder or file with the Windows shell from WSL, where Linux desktop openers usually do nothing.

Qt-free and only used under WSL; native Windows and Linux keep their ordinary desktop services. Commands use
discrete arguments (never a shell string), the Windows path comes from ``wslpath`` rather than a guessed drive
mapping, and no path is logged.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from dtflow.persistence.runtime_environment import environment_key

log = logging.getLogger(__name__)


class LaunchUnavailable(OSError):
    """A sanitized reason the request could not be handed to a desktop program."""


def uses_windows_shell() -> bool:
    """True under WSL, where opening goes through Windows interop instead of a Linux desktop handler."""
    return environment_key() == "wsl"


def _executable(name: str, fallback: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if Path(fallback).is_file():
        return fallback
    raise LaunchUnavailable("Windows interop is unavailable here, so the Windows shell cannot be started.")


def windows_path(path: Path) -> str:
    """The Windows spelling of an existing local path, as WSL itself reports it."""
    if not path.exists():
        raise LaunchUnavailable("The folder or file no longer exists.")
    command = [_executable("wslpath", "/usr/bin/wslpath"), "-w", "--", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("desktop launch path conversion failed reason=%s", type(exc).__name__)
        raise LaunchUnavailable("The path could not be translated for Windows.") from exc
    converted = result.stdout.strip()
    if not converted or "\n" in converted or "\0" in converted:
        raise LaunchUnavailable("The path could not be translated for Windows.")
    return converted


def open_with_windows_shell(path: Path, *, folder: bool) -> None:
    """Start Explorer on a folder, or the registered Windows viewer on a file; raise LaunchUnavailable on failure.

    Explorer's exit status does not report success, so only a failure to start it is detectable.
    """
    path = Path(path)
    if folder and not path.is_dir():
        raise LaunchUnavailable("The folder no longer exists.")
    if not folder and not path.is_file():
        raise LaunchUnavailable("The image no longer exists.")
    target = windows_path(path)
    explorer = _executable("explorer.exe", "/mnt/c/Windows/explorer.exe")
    try:
        subprocess.Popen(
            [explorer, target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        log.warning("desktop launch failed kind=%s reason=%s", "folder" if folder else "file", type(exc).__name__)
        raise LaunchUnavailable("Windows Explorer could not be started.") from exc

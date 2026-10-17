"""Prepare desktop requests off-thread and report safe hand-off failures on Qt.

On WSL, translate with ``wslpath -w``; folders go to Explorer and files use Windows associations via PowerShell 7.
The fixed script receives only JSON data on stdin, never an interpolated command. Native Qt hand-off stays on Qt.
"""

import asyncio
import base64
import json
import logging
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)

WSLPATH_TIMEOUT = 5
ASSOCIATION_TIMEOUT = 10
_ASSOCIATION_SCRIPT = """$ErrorActionPreference = 'Stop'
try {
    $target = [string](ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()))
    Start-Process -FilePath $target -WindowStyle Normal -ErrorAction Stop
    exit 0
} catch {
    exit 1
}
"""
_ASSOCIATION_COMMAND = base64.b64encode(_ASSOCIATION_SCRIPT.encode("utf-16-le")).decode("ascii")


def _wsl() -> bool:
    return os.name != "nt" and "microsoft" in platform.release().lower()


def _validate_target(path: str, *, directory: bool) -> str | None:
    """A fresh metadata/directory-access check, never image-content authority."""
    target = Path(path)
    if not target.is_absolute():
        return "its location is not an absolute path."
    kind = "folder" if directory else "file"
    try:
        mode = target.stat().st_mode
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            return f"its location is not a {kind}."
        if directory:
            with os.scandir(target):
                pass
    except FileNotFoundError:
        return f"the {kind} no longer exists."
    except PermissionError:
        return f"the {kind} is inaccessible (permission denied)."
    except OSError as exc:
        log.warning("desktop open target unavailable reason=%s", type(exc).__name__)
        return f"the {kind} could not be accessed."
    return None


def _open_on_worker(path: str, *, directory: bool) -> str | None:
    if reason := _validate_target(path, directory=directory):
        return reason
    if not _wsl():
        return None  # Native Qt hand-off belongs on the GUI thread after validation.
    wslpath = shutil.which("wslpath")
    launcher = shutil.which("explorer.exe" if directory else "pwsh.exe")
    if wslpath is None:
        log.warning("desktop open unavailable reason=no-wslpath")
        return "wslpath is unavailable in this WSL session."
    if launcher is None:
        log.warning("desktop open unavailable reason=%s", "no-explorer" if directory else "no-pwsh")
        name = "Windows Explorer" if directory else "PowerShell 7 (pwsh.exe)"
        return f"{name} is unavailable through Windows interop; check this WSL session's PATH."
    try:
        converted = subprocess.run(
            [wslpath, "-w", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=WSLPATH_TIMEOUT,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        log.warning("desktop open path translation failed reason=%s", type(exc).__name__)
        return "its location could not be translated to a Windows path."
    if not converted or "\n" in converted:
        return "its location could not be translated to a Windows path."
    if not directory:
        try:
            # Only the fixed script is executable code. Even quotes/metacharacters
            # in the translated path remain JSON data, not PowerShell syntax.
            subprocess.run(
                [
                    launcher,
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-EncodedCommand",
                    _ASSOCIATION_COMMAND,
                ],
                input=json.dumps(converted, ensure_ascii=True).encode("ascii"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=ASSOCIATION_TIMEOUT,
                check=True,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired:
            log.warning("desktop open association hand-off unconfirmed reason=timeout")
            return "the Windows viewer hand-off could not be confirmed."
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("desktop open association failed reason=%s", type(exc).__name__)
            return "Windows could not open the file with its associated application; check its default viewer."
        return None  # Shell accepted the request, not proof that a viewer window appeared.
    try:
        # Explorer's exit status is not a success signal, so a started process is the hand-off.
        subprocess.Popen(
            [launcher, converted],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log.warning("desktop open launch failed reason=%s", type(exc).__name__)
        return "Windows Explorer could not be started."
    return None


def open_local_path(path: str, *, runner, directory: bool, on_error) -> None:
    """Validate/translate/launch via the existing runner; callbacks and native Qt stay on Qt."""
    if runner.closed:
        return

    async def work():
        return await asyncio.to_thread(_open_on_worker, path, directory=directory)

    def done(reason, error):
        if error is not None:
            log.warning("desktop open preparation failed reason=%s", type(error).__name__)
            reason = "the desktop request could not be prepared."
        elif reason is None and not _wsl():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                reason = "the system could not open it."
        if reason:
            on_error(reason)

    runner.submit(work(), done)

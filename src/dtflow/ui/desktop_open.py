"""Prepare desktop requests off-thread and report safe hand-off failures on Qt.

On WSL the Linux ``xdg-open`` hand-off often succeeds without opening anything, so a WSL path is translated with
``wslpath -w`` and handed to Windows Explorer through WSL interop. Both programs are found on ``PATH`` and run
with discrete arguments, never a shell string. Elsewhere Qt's desktop services decide.
"""

import asyncio
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
    wslpath, explorer = shutil.which("wslpath"), shutil.which("explorer.exe")
    if wslpath is None or explorer is None:
        log.warning("desktop open unavailable reason=%s", "no-wslpath" if wslpath is None else "no-interop")
        return "Windows interop is unavailable in this WSL session, so Explorer cannot be started."
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
    try:
        # Explorer's exit status is not a success signal, so a started process is the hand-off.
        subprocess.Popen(
            [explorer, converted],
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

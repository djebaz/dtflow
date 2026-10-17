"""Open a folder or file in the desktop's file manager or registered viewer, reporting real failures.

On WSL the Linux ``xdg-open`` hand-off often succeeds without opening anything, so a WSL path is translated with
``wslpath -w`` and handed to Windows Explorer through WSL interop. Both programs are found on ``PATH`` and run
with discrete arguments, never a shell string. Elsewhere Qt's desktop services decide.
"""

import logging
import os
import platform
import shutil
import subprocess

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)

WSLPATH_TIMEOUT = 5


def _wsl() -> bool:
    return os.name != "nt" and "microsoft" in platform.release().lower()


def open_local_path(path: str) -> str | None:
    """Open ``path``; return None on hand-off or a short user-facing reason. Paths are never logged."""
    if not _wsl():
        if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            return None
        return "the system could not open it."
    if not path.startswith("/"):
        return "its location is not an absolute path."
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

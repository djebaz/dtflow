"""Open a local folder or file for the user, with a visible reason instead of a silent no-op."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from dtflow.services.desktop_launch import LaunchUnavailable, open_with_windows_shell, uses_windows_shell


def open_local(runner, path: Path, *, folder: bool, failed: Callable[[str], None]) -> None:
    """Use the Windows shell under WSL (off the GUI thread) and Qt desktop services elsewhere."""
    if uses_windows_shell():

        def done(_result, error) -> None:
            if error is not None:
                failed(
                    str(error) if isinstance(error, LaunchUnavailable) else "The Windows shell could not be started."
                )

        runner.submit(asyncio.to_thread(open_with_windows_shell, path, folder=folder), done)
        return
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        failed(
            "No system file manager accepted the folder."
            if folder
            else "No system viewer is registered for this image."
        )

"""Shared application theme loading for the Qt presentation layer."""

import ctypes
import json
import re
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtWidgets import QApplication

THEME_FILE = Path(__file__).parents[1] / "resources" / "styles" / "dtflow_dark.qss"
TOKENS_FILE = THEME_FILE.with_name("tokens.json")


def load_tokens() -> dict[str, str]:
    """One tracked palette/scale for the stylesheet and custom painted presentation."""
    return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))


def load_theme() -> str:
    """Read the tracked QSS source used by source and packaged Qt execution."""
    try:
        source = THEME_FILE.read_text(encoding="utf-8")
        tokens = load_tokens()
    except (OSError, ValueError) as exc:
        raise OSError("The DTFlow Qt stylesheet could not be loaded.") from exc

    def token(match: re.Match) -> str:
        try:
            return tokens[match.group(1)]
        except KeyError:
            raise OSError(f"The DTFlow Qt stylesheet uses an unknown token: {match.group(1)}.") from None

    resolved = re.sub(r"\$\{([\w.]+)\}", token, source)
    return re.sub(
        r"url\((\.\./images/(?:gallery|controls)/[^)]+)\)",
        lambda match: f'url("{(THEME_FILE.parent / match.group(1)).resolve().as_posix()}")',
        resolved,
    )


def apply_theme(application: QApplication) -> None:
    """Apply the shared stylesheet once at application startup."""
    application.setStyleSheet(load_theme())


def apply_native_dark_titlebar(window) -> bool:
    """Request the Splitvids-style dark DWM frame on Windows 10/11."""
    if sys.platform != "win32":
        return False
    try:
        hwnd = int(window.winId())
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):  # current DWM value, then the Windows 10 1809 compatibility value
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_int(attribute),
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
            if result == 0:
                return True
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return False

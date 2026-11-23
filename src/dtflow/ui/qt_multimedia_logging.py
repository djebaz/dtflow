"""Route QtMultimedia/FFmpeg warnings into DTFlow's existing diagnostics.

This is a GUI-only Qt adapter. Logging configuration and JSONL ownership remain
in dtflow.logging_setup; headless CLI and services never import Qt.
"""

import logging
import os
import sys

from PySide6.QtCore import QtMsgType, qFormatLogMessage, qInstallMessageHandler

from dtflow.diagnostics import event
from dtflow.logging_setup import dev_enabled, sanitize

_QT_MULTIMEDIA_HANDLER = None


def _silence_native_output() -> None:
    """Keep FFmpeg and Qt multimedia chatter off the console without any Python callback.

    Qt's multimedia categories and uncategorized warnings are switched off through logging rules (evaluated
    natively; set DTFLOW_QT_WARNINGS=1 to keep the uncategorized warnings), and the bundled
    FFmpeg's global log level is set to quiet through its own C function, which needs no GIL. Both are best
    effort and respect an explicit QT_LOGGING_RULES.
    """
    rules = "qt.multimedia.*=false"
    if not os.environ.get("DTFLOW_QT_WARNINGS"):
        # Qt's "D3D11 smoke test: Failed to create vertex shader" is an uncategorized warning, so it can only
        # be hidden together with the other uncategorized ones; DTFLOW_QT_WARNINGS=1 keeps them visible.
        rules += ";default.warning=false"
    os.environ.setdefault("QT_LOGGING_RULES", rules)
    try:
        import ctypes
        from pathlib import Path

        import PySide6

        library = next(iter(sorted(Path(PySide6.__file__).parent.glob("avutil*.dll"))), None)
        if library is None:
            library = next(iter(sorted(Path(PySide6.__file__).parent.glob("Qt/lib/libavutil*"))), None)
        if library is not None:
            set_level = ctypes.CDLL(str(library)).av_log_set_level
            set_level.argtypes = [ctypes.c_int]
            set_level(-8)  # AV_LOG_QUIET
    except (ImportError, OSError, AttributeError):
        pass


def install_qt_multimedia_logging() -> None:
    """Opt-in only: set DTFLOW_QT_MULTIMEDIA_LOG=1 to route Qt multimedia/FFmpeg messages into diagnostics.

    The handler is Python code, so every Qt/FFmpeg log line written by a decoder thread needs the GIL.
    Stopping or replacing a clip makes the GUI thread wait for those threads while holding the GIL, and the
    two block each other forever (py-spy dump of a frozen Gallery, #580: GUI thread in `QMediaPlayer.stop`
    waiting on the playback engine; decoder threads in `av_log` -> Qt message output -> `PyGILState_Ensure`).
    By default no handler is installed, so Qt's FFmpeg plugin writes to stderr natively without the GIL.

    When enabled, Qt's FFmpeg plugin routes its messages through Qt (QT_FFMPEG_DEBUG) and they are logged
    in development mode only; unrelated Qt and fatal messages are left alone.
    """
    if not os.environ.get("DTFLOW_QT_MULTIMEDIA_LOG"):
        _silence_native_output()
        return
    global _QT_MULTIMEDIA_HANDLER
    if _QT_MULTIMEDIA_HANDLER is not None:
        return

    # Set before QtMultimedia initializes. Respect an explicit user setting.
    os.environ.setdefault("QT_FFMPEG_DEBUG", "1")
    logger = logging.getLogger("dtflow.qt.multimedia")
    previous = None

    def route(kind, context, message) -> None:
        category = context.category or ""
        multimedia = (
            category == "qt.multimedia"
            or category.startswith("qt.multimedia.")
            or message.lstrip('"').startswith(("FFmpeg log:", "D3D11 smoke test:"))
        )
        if multimedia and kind != QtMsgType.QtFatalMsg:
            if dev_enabled() and kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
                detail = sanitize(message)
                if len(detail) > 2048:
                    detail = "[omitted: oversized Qt multimedia diagnostic]"
                event(
                    logger,
                    "qt.multimedia.warning",
                    level=logging.DEBUG,
                    data={"category": category, "severity": kind.name, "message": detail},
                )
            return

        if previous is not None:
            previous(kind, context, message)
        elif sys.stderr is not None:
            sys.stderr.write(qFormatLogMessage(kind, context, message) + "\n")

    previous = qInstallMessageHandler(route)
    _QT_MULTIMEDIA_HANDLER = route

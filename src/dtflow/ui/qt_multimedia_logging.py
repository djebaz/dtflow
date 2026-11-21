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


def install_qt_multimedia_logging() -> None:
    """Hide routine playback output and log useful warnings only in development mode.

    Qt's FFmpeg plugin normally writes directly to stderr. QT_FFMPEG_DEBUG
    routes its messages through Qt but also enables verbose decoder output.
    Discard those extra messages; leave unrelated Qt and fatal messages alone.
    """
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

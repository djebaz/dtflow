"""Desktop-only launch options and shared logging for the packaged Qt application."""

import argparse
import ctypes
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from dtflow._diagnostic_notifications import DiagnosticNotice
from dtflow.build import display_identity, identity
from dtflow.diagnostics import event as diagnostic_event, set_perf
from dtflow.logging_setup import LoggingSetupError, configure_logging

log = logging.getLogger("dtflow.desktop")


class _ArgumentsInvalid(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Never echo arbitrary supplied arguments into logs or startup diagnostics.
        raise _ArgumentsInvalid


def parser() -> argparse.ArgumentParser:
    command = _Parser(description="Launch the DTFlow desktop application.", add_help=False)
    command.add_argument("-h", "--help", action="store_true", help="Show desktop launch options.")
    command.add_argument("-v", "--version", action="store_true", help="Show application version and build identity.")
    command.add_argument("-x", "--dev", action="store_true", help="Add sanitized diagnostic detail to the log file.")
    command.add_argument(
        "-perf", "--perf", action="store_true", help="With -x, also write performance measurements to the log file."
    )
    command.add_argument("-q", "--logs", type=Path, help="Override the diagnostic log directory.")
    command.add_argument("-nc", "--no-cache", action="store_true", help="Bypass disposable cache reads and writes.")
    command.add_argument(
        "-vd", "--video-decoder", choices=("upstream", "lut"), default="upstream",
        help="Video tensor decoder: upstream (default) or experimental lut for CPU comparison.",
    )
    return command


def _notice(message: str, *, console: bool, error: bool = False) -> None:
    """Native Windows diagnostics still work if Qt cannot import or no console exists."""
    if not console and os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "DTFlow", 0x10 if error else 0x40)
    else:
        print(message, file=sys.stderr if error else sys.stdout)


def _run(argv: list[str] | None, report: Callable[..., None]) -> int:
    command = parser()
    try:
        args = command.parse_args(argv)
    except _ArgumentsInvalid:
        report("Invalid desktop launch options.\n\n" + command.format_help(), error=True)
        return 2
    if args.help:
        report(command.format_help())
        return 0
    if args.version:
        report(display_identity())
        return 0

    set_perf(args.perf)
    session = None
    notices = DiagnosticNotice()
    try:
        try:
            session = configure_logging(
                directory=args.logs,
                dev=args.dev,
                build=identity(),
                video_decoder=args.video_decoder,
                report_error=notices.post,
            )
        except LoggingSetupError:
            notices.post("Diagnostic logs are unavailable. Continuing without a log file.")
        from dtflow.services import video_encode
        from dtflow.ui.splash import run_gui_with_splash

        video_encode.set_video_decoder(args.video_decoder)
        return run_gui_with_splash(no_cache=args.no_cache, diagnostic_notices=notices)
    except KeyboardInterrupt:
        diagnostic_event(
            log,
            "app.desktop_interrupted_locally",
            level=logging.WARNING,
            data={},
        )
        return 130
    except Exception as exc:
        # Keep exception contents (potentially credentials or request data) out of dialogs.
        diagnostic_event(
            log,
            "app.desktop_failed",
            level=logging.ERROR,
            data={
                "type": type(exc).__name__,
            },
        )
        report("DTFlow could not start or continue. Check its diagnostic logs; no work will be retried.", error=True)
        return 5
    finally:
        if session is not None:
            session.close()


def main(argv: list[str] | None = None) -> int:
    """Open Qt by default; do not require a gui subcommand or expose packaged generation CLI commands."""
    console = sys.stderr is not None
    original = sys.stdout, sys.stderr
    replacements = []
    try:
        # Third-party imports can assume flush/write exist even in PyInstaller windowed mode.
        for name in ("stdout", "stderr"):
            if getattr(sys, name) is None:
                stream = open(os.devnull, "w", encoding="utf-8")
                replacements.append(stream)
                setattr(sys, name, stream)
        return _run(argv, lambda message, **kwargs: _notice(message, console=console, **kwargs))
    finally:
        sys.stdout, sys.stderr = original
        for stream in replacements:
            stream.close()

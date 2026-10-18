"""Desktop-only launch options and shared logging for the packaged Qt application."""

import argparse
import ctypes
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from dtflow.build import display_identity, identity
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
    command.add_argument("-q", "--logs", type=Path, help="Override the diagnostic log directory.")
    command.add_argument("-nc", "--no-cache", action="store_true", help="Bypass disposable cache reads and writes.")
    command.add_argument("-ml", "--max-load", type=int, metavar="N", help="Development: target cards loaded per window (1–2000).")
    command.add_argument("-mc", "--max-cards", type=int, metavar="N", help="Development: maximum mounted cards (300, 500, 1000, 2000).")
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
    if (args.max_load is not None or args.max_cards is not None) and not args.dev:
        report("Gallery tuning arguments require -x / --dev.", error=True)
        return 2
    if args.max_load is not None and not 1 <= args.max_load <= 2000:
        report("--max-load must be between 1 and 2000.", error=True)
        return 2
    if args.max_cards is not None and args.max_cards not in (300, 500, 1000, 2000):
        report("--max-cards must be 300, 500, 1000, or 2000.", error=True)
        return 2

    session = None
    try:
        try:
            session = configure_logging(
                directory=args.logs,
                dev=args.dev,
                build=identity(),
                report_error=lambda message: report(message, error=True),
            )
        except LoggingSetupError:
            report("Diagnostic logs are unavailable. Continuing without a log file.", error=True)
        # Gallery module reads these settings on first import; CLI overrides the
        # existing experimental environment setting for this launch only.
        if args.max_load is not None:
            os.environ["DTFLOW_GALLERY_LOAD_SIZE"] = str(args.max_load)
        if args.max_cards is not None:
            os.environ["DTFLOW_GALLERY_RESIDENT_LIMIT"] = str(args.max_cards)
        from dtflow.ui.app import run_gui

        return run_gui(no_cache=args.no_cache)
    except KeyboardInterrupt:
        log.warning("desktop interrupted locally")
        return 130
    except Exception as exc:
        # Keep exception contents (potentially credentials or request data) out of dialogs.
        log.error("desktop startup/runtime failed type=%s", type(exc).__name__)
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

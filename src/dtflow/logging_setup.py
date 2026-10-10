"""One shared standard-library logging setup for CLI, services and adapters.

File I/O runs on a queue listener thread, so callers (and later Qt) never block on disk. Each process writes its own
``dtflow-<session>.log`` (no cross-process rotation collisions) and old session files are pruned to a bounded count.
Messages and exception text are sanitized before they reach the file; prompts, headers, tokens and payloads are never
logged by DTFlow code, and the sanitizer is a second line of defence. Logging never changes generation, retry or
billing behavior, and a failure to write is reported once without raising into the application.
"""

import logging
import logging.handlers
import os
import queue
import re
import sys
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

LOGGER = "dtflow"
MAX_BYTES = 1_000_000
BACKUPS = 3
RETAINED_SESSIONS = 20
SECRET_ENVIRONMENT = ("DRAWTHINGS_API_KEY", "DRAWTHINGS_SHARED_SECRET")
# Correlates concurrent work; set by the generation service around one attempt.
ATTEMPT: ContextVar[str] = ContextVar("dtflow_attempt", default="-")

_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (
        re.compile(
            r"""(?i)\b(authorization|api[_-]?key|token|secret|password|shared[_-]?secret)(["']?\s*[:=]\s*)\S+"""
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"[A-Za-z0-9+/=_-]{200,}"), "[redacted-payload]"),
)


class LoggingSetupError(Exception):
    """The log destination is unusable; the application continues without file logging."""


def sanitize(text: str, environ=None) -> str:
    environ = os.environ if environ is None else environ
    for name in SECRET_ENVIRONMENT:
        secret = environ.get(name, "").strip()
        if len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    home = str(Path.home())
    return text.replace(home, "~") if len(home) > 1 else text


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return sanitize(super().format(record))

    def formatException(self, exc_info) -> str:
        return sanitize(super().formatException(exc_info))


class _SafeFileHandler(logging.handlers.RotatingFileHandler):
    """Report a write failure once, in one sanitized line, never raising into the caller."""

    reported = False
    report_error: Callable[[str], None] | None = None

    def handleError(self, record: logging.LogRecord) -> None:
        if not type(self).reported:
            type(self).reported = True
            message = "Log file could not be written; continuing without it."
            if self.report_error is not None:
                self.report_error(message)
            else:
                print(message, file=sys.stderr)


def default_directory() -> Path:
    """Repository-root ``logs/`` from a source checkout; per-user application data otherwise."""
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and (root / "src" / "dtflow").is_dir():
        return root / "logs"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DTFlow" / "logs"
    return Path.home() / ".local" / "state" / "dtflow" / "logs"


@dataclass
class LogSession:
    session: str
    path: Path
    listener: logging.handlers.QueueListener
    handler: logging.Handler

    def close(self) -> None:
        logger = logging.getLogger(LOGGER)
        self.listener.stop()
        for handler in list(logger.handlers):
            if getattr(handler, "_dtflow_session", None) == self.session:
                logger.removeHandler(handler)
        self.handler.close()


def _prune(directory: Path, keep: str) -> None:
    sessions = sorted(
        (path for path in directory.glob("dtflow-*.log") if path.name != f"dtflow-{keep}.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in sessions[RETAINED_SESSIONS - 1 :]:
        for leftover in (stale, *directory.glob(stale.name + ".*")):
            leftover.unlink(missing_ok=True)


def configure_logging(
    *,
    directory: Path | None = None,
    dev: bool = False,
    build: dict[str, str] | None = None,
    backend: str = "-",
    session: str | None = None,
    report_error: Callable[[str], None] | None = None,
) -> LogSession:
    """Start file logging for this process. Normal mode logs lifecycle/errors; ``dev`` adds debug detail only."""
    session = session or uuid4().hex[:12]
    directory = Path(directory) if directory is not None else default_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"dtflow-{session}.log"
        handler = _SafeFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.report_error = report_error
        _prune(directory, session)
    except OSError as exc:
        raise LoggingSetupError("Log destination could not be prepared.") from exc
    build = build or {}
    commit = build.get("source_commit", "unknown")
    handler.setFormatter(
        _Formatter(
            "%(asctime)s %(levelname)s %(name)s session=" + session + " attempt=%(attempt)s %(message)s",
            "%Y-%m-%dT%H:%M:%S%z",
        )
    )
    # The correlation field is attached on the producing thread, before the record crosses the queue.
    logging_queue: queue.SimpleQueue = queue.SimpleQueue()
    queue_handler = logging.handlers.QueueHandler(logging_queue)
    queue_handler._dtflow_session = session
    queue_handler.addFilter(_attach_attempt)
    # QueueHandler bakes tracebacks into the message with its own formatter, so that one must sanitize too.
    queue_handler.setFormatter(_Formatter("%(message)s"))
    listener = logging.handlers.QueueListener(logging_queue, handler)
    logger = logging.getLogger(LOGGER)
    logger.setLevel(logging.DEBUG if dev else logging.INFO)
    logger.propagate = False
    logger.addHandler(queue_handler)
    listener.start()
    logger.info(
        "session start version=%s commit=%s backend=%s dev=%s",
        build.get("version", "unknown"),
        commit[:12],
        backend,
        dev,
    )
    return LogSession(session, path, listener, handler)


def dev_enabled() -> bool:
    """True when this process runs in explicit development mode (``-x``); it only adds diagnostic detail."""
    return logging.getLogger(LOGGER).isEnabledFor(logging.DEBUG)


def _attach_attempt(record: logging.LogRecord) -> bool:
    record.attempt = ATTEMPT.get()
    return True

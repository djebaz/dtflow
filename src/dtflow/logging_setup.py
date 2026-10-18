"""One shared standard-library logging setup for CLI, services and adapters.

File I/O runs on a queue listener thread, so callers (and later Qt) never block on disk. Each process writes its own
``dtflow-<session>.log`` (no cross-process rotation collisions) and old session files are pruned to a bounded count.
Messages and exception text are sanitized before they reach the file; prompts, headers, tokens and payloads are never
logged by DTFlow code, and the sanitizer is a second line of defence. Logging never changes generation, retry or
billing behavior, and a failure to write is reported once without raising into the application.
"""

import csv
import logging
import logging.handlers
import os
import queue
import re
import sys
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
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




class _GalleryPerfCsvHandler(logging.Handler):
    """Write sanitized gallery measurements in a separate, per-session CSV.

    Called only by the queue listener's fan-out; no CSV I/O runs in Qt or
    thumbnail worker callbacks. Each metric has its own filterable CSV row.
    """

    _HEADER = ("utc", "phase", "metric", "samples", "mean", "p95", "max", "total", "details")

    def __init__(self, path: Path, report_error: Callable[[str], None] | None):
        super().__init__()
        self.report_error = report_error
        self._reported = False
        self._stream = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._stream)
        self._writer.writerow(self._HEADER)

    def _write(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not message.startswith("gallery_perf phase="):
            return
        # Sanitize before CSV quoting; no prompt or path payload is introduced.
        fields = {
            key: sanitize(value)
            for key, value in (
                part.split("=", 1) for part in message.split() if "=" in part
            )
        }
        phase = fields.pop("phase", "unknown")
        samples = fields.pop("samples", "1")
        metrics: dict[str, dict[str, float]] = {}
        if phase.endswith("_summary"):
            for key in tuple(fields):
                for suffix in ("_mean", "_p95", "_max", "_total"):
                    if key.endswith(suffix):
                        name = key[: -len(suffix)]
                        try:
                            metrics.setdefault(name, {})[suffix[1:]] = float(fields.pop(key))
                        except ValueError:
                            pass
                        break
        else:
            for key in tuple(fields):
                if key.endswith("_ms"):
                    try:
                        value = float(fields.pop(key))
                    except ValueError:
                        continue
                    metrics[key] = {"mean": value, "p95": value, "max": value, "total": value}
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        utc = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        for name, values in (metrics or {"": {}}).items():
            self._writer.writerow(
                (
                    utc,
                    phase,
                    name,
                    samples,
                    values.get("mean", ""),
                    values.get("p95", ""),
                    values.get("max", ""),
                    values.get("total", ""),
                    details,
                )
            )

    def emit(self, record: logging.LogRecord) -> None:
        if self._stream is None:
            return
        try:
            self._write(record)
        except (OSError, csv.Error, ValueError):
            if not self._reported:
                self._reported = True
                message = "Gallery CSV logging unavailable; continuing with text logs."
                if self.report_error is not None:
                    self.report_error(message)
                else:
                    print(message, file=sys.stderr)
            self._stream.close()
            self._stream = None

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()


class _GalleryPerfFanoutHandler(logging.Handler):
    """Deliver pre-aggregated diagnostic records to the text and CSV sinks."""

    def __init__(self, text_handler: logging.Handler, csv_handler: logging.Handler | None):
        super().__init__()
        self.text_handler = text_handler
        self.csv_handler = csv_handler

    def emit(self, record: logging.LogRecord) -> None:
        self.text_handler.handle(record)
        if self.csv_handler is not None:
            self.csv_handler.handle(record)

    def close(self) -> None:
        if self.csv_handler is not None:
            self.csv_handler.close()
        self.text_handler.close()
        super().close()


class _GalleryPerfAggregationHandler(logging.Handler):
    """Summarize frequent gallery samples on the logging listener thread.

    Measurements still originate in the same workers and GUI callbacks.
    The ordinary log keeps outliers and lifecycle records individually; the
    high-frequency measurements are written in bounded statistical batches.
    """

    _AGGREGATED = frozenset(
        {"thumbnail_worker", "thumbnail_end_to_end", "scroll_intent", "prepare_slice", "gui_gap"}
    )
    _BATCH_SIZE = 64
    _BATCH_SECONDS = 10.0

    def __init__(self, target: logging.Handler):
        super().__init__()
        self.target = target
        self._batches: dict[tuple[str, tuple[tuple[str, str], ...]], dict] = {}

    @staticmethod
    def _fields(message: str) -> dict[str, str]:
        return dict(part.split("=", 1) for part in message.split() if "=" in part)

    def _flush_batch(self, key: tuple[str, tuple[tuple[str, str], ...]]) -> None:
        batch = self._batches.pop(key, None)
        if batch is None:
            return
        phase, dimensions = key
        parts = ["gallery_perf", f"phase={phase}_summary", f"samples={batch['samples']}"]
        parts.extend(f"{name}={value}" for name, value in dimensions)
        for name, samples in sorted(batch["metrics"].items()):
            ordered = sorted(samples)
            count = len(ordered)
            p95 = ordered[max(0, (95 * count + 99) // 100 - 1)]
            parts.extend(
                (
                    f"{name}_mean={sum(samples) / count:.1f}",
                    f"{name}_p95={p95:.1f}",
                    f"{name}_max={ordered[-1]:.1f}",
                    f"{name}_total={sum(samples):.1f}",
                )
            )
        summary = logging.LogRecord(
            "dtflow.ui.gallery_perf",
            logging.DEBUG,
            "",
            0,
            " ".join(parts),
            (),
            None,
        )
        summary.attempt = "-"
        self.target.handle(summary)

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not message.startswith("gallery_perf phase="):
            self.target.handle(record)
            return
        fields = self._fields(message)
        phase = fields.get("phase")
        if phase not in self._AGGREGATED:
            self.target.handle(record)
            return
        # Keep unusually large GUI stalls individually visible with timestamps.
        if phase == "gui_gap" and float(fields.get("elapsed_ms", "0")) >= 100.0:
            self.target.handle(record)
        dimensions = tuple(
            (name, fields[name])
            for name in ("direction", "source", "mode", "success")
            if name in fields
        )
        key = (phase, dimensions)
        bucket = self._batches.setdefault(
            key, {"samples": 0, "first": record.created, "metrics": {}}
        )
        bucket["samples"] += 1
        for name, value in fields.items():
            if name in {"phase", "direction", "source", "mode", "success"}:
                continue
            try:
                number = float(value)
            except ValueError:
                continue
            bucket["metrics"].setdefault(name, []).append(number)
        if (
            bucket["samples"] >= self._BATCH_SIZE
            or record.created - bucket["first"] >= self._BATCH_SECONDS
        ):
            self._flush_batch(key)

    def close(self) -> None:
        for key in tuple(self._batches):
            self._flush_batch(key)
        self.target.close()
        super().close()


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
        stem = stale.name.removesuffix(".log")
        for leftover in (
            stale,
            *directory.glob(stale.name + ".*"),
            *directory.glob(stem + "-gallery.csv*"),
        ):
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
    csv_handler = None
    if dev:
        try:
            csv_handler = _GalleryPerfCsvHandler(
                directory / f"dtflow-{session}-gallery.csv", report_error
            )
        except OSError:
            message = "Gallery CSV logging unavailable; continuing with text logs."
            if report_error is not None:
                report_error(message)
            else:
                print(message, file=sys.stderr)
    aggregate_handler = _GalleryPerfAggregationHandler(_GalleryPerfFanoutHandler(handler, csv_handler))
    listener = logging.handlers.QueueListener(logging_queue, aggregate_handler)
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
    return LogSession(session, path, listener, aggregate_handler)


def dev_enabled() -> bool:
    """True when this process runs in explicit development mode (``-x``); it only adds diagnostic detail."""
    return logging.getLogger(LOGGER).isEnabledFor(logging.DEBUG)


def _attach_attempt(record: logging.LogRecord) -> bool:
    record.attempt = ATTEMPT.get()
    return True

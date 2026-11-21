"""Single configuration/lifecycle authority for CLI, Qt, services and adapters.

Retains stdlib queue/listener logging with owned capture/schema/aggregation
internals and one JSONL sink. Consumers share the reader introduced in Phase 2.
"""

import logging
import os
import re
import stat
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dtflow._diagnostic_ownership import ownership
from dtflow._diagnostic_schema import (
    AGGREGATION_POLICY,
    INSTRUMENTATION_VERSION,
    MAX_RECORD_BYTES,
    QUEUE_BYTES,
    QUEUE_RECORDS,
    SECRET_ENVIRONMENT,
    SESSION_NAME,
    sanitize,
)
from dtflow._diagnostic_writer import Admission, CaptureHandler, DiagnosticListener, JsonlHandler
from dtflow.diagnostics import ATTEMPT, perf_requested

__all__ = [
    "ATTEMPT",
    "SECRET_ENVIRONMENT",
    "LogSession",
    "LoggingSetupError",
    "configure_logging",
    "install_qt_multimedia_logging",
    "configured_directory",
    "default_directory",
    "dev_enabled",
    "perf_enabled",
    "sanitize",
]
LOGGER = "dtflow"
MAX_BYTES = 1_000_000
BACKUPS = 3
RETAINED_SESSIONS = 20
_SEGMENT = re.compile(r"^dtflow-([A-Za-z0-9_-]{1,128})\.jsonl(?:\.[1-3])?$")
_CONFIGURATION = threading.Lock()


class LoggingSetupError(Exception):
    """The destination is unusable; continue without file diagnostics."""


def default_directory() -> Path:
    """Repository logs from source; per-user application data otherwise."""
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and (root / "src" / "dtflow").is_dir():
        return root / "logs"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DTFlow" / "logs"
    return Path.home() / ".local" / "state" / "dtflow" / "logs"


def configured_directory() -> Path:
    """Use the active logging session's destination, including CLI overrides."""
    for handler in logging.getLogger(LOGGER).handlers:
        directory = getattr(handler, "_dtflow_directory", None)
        if directory is not None:
            return directory
    return default_directory()


@dataclass
class LogSession:
    session: str
    path: Path
    listener: DiagnosticListener
    handler: JsonlHandler
    capture_handler: CaptureHandler
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        logger = logging.getLogger(LOGGER)
        logger.removeHandler(self.capture_handler)
        with self.capture_handler.admission.lock:
            self.capture_handler.admission.accepting = False
        self.listener.stop()
        self.capture_handler.close()


def _prune(directory: Path, keep: str) -> None:
    groups = {}
    try:
        candidates = list(directory.iterdir())
    except OSError:
        return
    for path in candidates:
        match = _SEGMENT.fullmatch(path.name)
        if match is None or match[1] == keep:
            continue
        try:
            info = path.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            continue
        group = groups.setdefault(match[1], {"time": 0, "paths": []})
        group["time"] = max(group["time"], info.st_mtime_ns)
        group["paths"].append(path)
    sessions = sorted(groups.values(), key=lambda group: group["time"], reverse=True)
    for stale in sessions[RETAINED_SESSIONS - 1 :]:
        base = stale["paths"][0].name.split(".jsonl", 1)[0]
        try:
            lease = ownership(directory / f"{base}.owner")
        except OSError:
            # An active/unknown owner is never made stale by its mtime.
            continue
        try:
            for path in stale["paths"]:
                try:
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                        path.unlink()
                except OSError:
                    continue
        finally:
            try:
                lease.close()
            except OSError:
                pass


_DEVELOPMENT = False
_QT_MULTIMEDIA_HANDLER = None


def development_mode() -> bool:
    """Whether this process runs with the explicit -x/--dev diagnostic mode; it never changes generation."""
    return _DEVELOPMENT


def install_qt_multimedia_logging() -> None:
    """Keep Qt/FFmpeg playback warnings off the console; retain them in the dev log only.

    Called once for the GUI after normal logging setup. Capture only messages Qt
    already emits; enabling QT_FFMPEG_DEBUG would flood the console with decoder
    internals. Native stderr writes outside Qt cannot be intercepted here.
    Non-multimedia Qt messages keep their existing output behavior.
    """
    global _QT_MULTIMEDIA_HANDLER
    if _QT_MULTIMEDIA_HANDLER is not None:
        return

    from PySide6.QtCore import QtMsgType, qFormatLogMessage, qInstallMessageHandler

    logger = logging.getLogger("dtflow.qt.multimedia")
    previous = None

    def route(kind, context, message) -> None:
        category = context.category or ""
        multimedia = (
            category == "qt.multimedia"
            or category.startswith("qt.multimedia.")
            or message.lstrip('"').startswith("FFmpeg log:")
        )
        if multimedia and kind != QtMsgType.QtFatalMsg:
            if development_mode() and kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
                # Sanitize before bounding: truncating first could expose a partial credential.
                detail = sanitize(message)
                if len(detail) > 2048:
                    detail = "[omitted: oversized Qt multimedia diagnostic]"
                from dtflow.diagnostics import event

                event(
                    logger,
                    "qt.multimedia.warning",
                    level=logging.DEBUG,
                    data={"category": category, "severity": kind.name, "message": detail},
                )
            return

        # Preserve unrelated Qt warnings (and fatal diagnostics) rather than redirecting
        # the process's stderr or suppressing important errors outside the player.
        if previous is not None:
            previous(kind, context, message)
        elif sys.stderr is not None:
            sys.stderr.write(qFormatLogMessage(kind, context, message) + "\n")

    previous = qInstallMessageHandler(route)
    _QT_MULTIMEDIA_HANDLER = route


def configure_logging(
    *,
    directory: Path | None = None,
    dev: bool = False,
    build: dict[str, str] | None = None,
    backend: str = "-",
    session: str | None = None,
    report_error=None,
) -> LogSession:
    """Start one bounded asynchronous session; never changes generation."""
    if not _CONFIGURATION.acquire(blocking=False):
        raise LoggingSetupError("Diagnostic configuration is already in progress.")
    try:
        result = _configure_logging(
            directory=directory, dev=dev, build=build, backend=backend, session=session, report_error=report_error
        )
        global _DEVELOPMENT
        _DEVELOPMENT = bool(dev)
        return result
    finally:
        _CONFIGURATION.release()


def _configure_logging(*, directory, dev, build, backend, session, report_error) -> LogSession:
    logger = logging.getLogger(LOGGER)
    if any(isinstance(handler, CaptureHandler) for handler in logger.handlers):
        raise LoggingSetupError("A diagnostic session is already configured.")
    session = session or uuid4().hex[:12]
    if type(session) is not str or not SESSION_NAME.fullmatch(session):
        raise LoggingSetupError("Invalid diagnostic session identity.")
    directory = Path(directory) if directory is not None else default_directory()
    lease = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"dtflow-{session}.jsonl"
        for candidate in (path, *(directory / f"{path.name}.{index}" for index in range(1, BACKUPS + 1))):
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            raise FileExistsError("Diagnostic session already exists.")
        # Never append a fresh seq=1 to an existing session or redirected file.
        owner_path = directory / f"dtflow-{session}.owner"
        try:
            lease = ownership(owner_path, create=True)
        except FileExistsError:
            lease = ownership(owner_path)
        with path.open("xb"):
            pass
        _prune(directory, session)
        sink = JsonlHandler(path, max_bytes=MAX_BYTES, backups=BACKUPS, report_error=report_error)
    except OSError as exc:
        if lease is not None:
            lease.close()
        raise LoggingSetupError("Log destination could not be prepared.") from exc
    build = build or {}
    status = build.get("source_status", "unknown")
    startup = {
        "session_id": session,
        "version": sanitize(build.get("version", "unknown")),
        "source_commit": sanitize(build.get("source_commit", "unknown")),
        "build_state": status if status in ("clean", "dirty") else "unknown",
        "source_status": sanitize(status),
        "runtime": f"Python {sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
        "dev": dev,
        "instrumentation_version": INSTRUMENTATION_VERSION,
        "schema_version": 1,
        "queue_max_records": QUEUE_RECORDS,
        "queue_max_bytes": QUEUE_BYTES,
        "max_record_bytes": MAX_RECORD_BYTES,
        "aggregation_policy": AGGREGATION_POLICY,
        "aggregation_max_groups": 64,
        "aggregation_max_samples": 64,
        "aggregation_window_s": 10,
        "capture_max_depth": 16,
        "capture_max_nodes": 4096,
        "capture_reservation_policy": "incremental-preallocation-v1",
        "retention_ownership_policy": "os-session-lock-v1",
        "numeric_policy": "native-observations-legacy1dp-listener-statistics-v1",
    }
    if "source_modified" in build:
        startup["source_modified"] = sanitize(build["source_modified"])
    if backend and backend != "-":
        startup["backend"] = sanitize(backend)
    admission = Admission()
    capture_handler = CaptureHandler(admission)
    capture_handler._dtflow_session = session
    capture_handler._dtflow_directory = path.absolute().parent
    listener = DiagnosticListener(admission, sink, startup)
    listener.ownership = lease
    logger.setLevel(logging.DEBUG if dev else logging.INFO)
    logger.propagate = False
    logger.addHandler(capture_handler)
    try:
        listener.start()
    except Exception as exc:
        logger.removeHandler(capture_handler)
        sink.close()
        lease.close()
        raise LoggingSetupError("Diagnostic listener could not be started.") from exc
    return LogSession(session, path, listener, sink, capture_handler)


def dev_enabled() -> bool:
    """Development mode changes diagnostic detail only."""
    return logging.getLogger(LOGGER).isEnabledFor(logging.DEBUG)


def perf_enabled() -> bool:
    """Performance measurements need both ``-x`` and ``-perf``; they change diagnostic detail only."""
    return perf_requested() and dev_enabled()

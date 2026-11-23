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
    "configured_directory",
    "default_directory",
    "dev_enabled",
    "development_mode",
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


def configure_logging(
    *,
    directory: Path | None = None,
    dev: bool = False,
    build: dict[str, str] | None = None,
    backend: str = "-",
    video_decoder: str = "lut",
    session: str | None = None,
    report_error=None,
) -> LogSession:
    """Start one bounded asynchronous session; never changes generation."""
    if not _CONFIGURATION.acquire(blocking=False):
        raise LoggingSetupError("Diagnostic configuration is already in progress.")
    try:
        result = _configure_logging(
            directory=directory,
            dev=dev,
            build=build,
            backend=backend,
            video_decoder=video_decoder,
            session=session,
            report_error=report_error
        )
        return result
    finally:
        _CONFIGURATION.release()


def _configure_logging(*, directory, dev, build, backend, video_decoder, session, report_error) -> LogSession:
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
        "video_decoder": video_decoder,
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


def development_mode() -> bool:
    """Compatibility query for GUI diagnostics; does not import Qt."""
    return dev_enabled()


def perf_enabled() -> bool:
    """Performance measurements need both ``-x`` and ``-perf``; they change diagnostic detail only."""
    return perf_requested() and dev_enabled()

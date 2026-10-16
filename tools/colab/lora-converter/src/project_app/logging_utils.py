"""Runtime-global/per-run logging and streaming subprocess execution."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

LOGGER_NAME = "production_colab"
URL_RE = re.compile(r"https?://[^\s'\"]+")


def redact_text(value: object) -> str:
    def replace(match: re.Match[str]) -> str:
        parsed = urlsplit(match.group(0))
        hostname = parsed.hostname or ""
        if parsed.port:
            hostname += f":{parsed.port}"
        netloc = ("***@" if parsed.username or parsed.password else "") + hostname
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

    return URL_RE.sub(replace, str(value))


def runtime_log_path(log_root: Path) -> Path:
    configured = os.environ.get("COLAB_RUNTIME_LOG")
    if configured:
        return Path(configured)
    session_file = log_root.parent / ".runtime_session_id"
    if session_file.exists():
        session_id = session_file.read_text(encoding="utf-8").strip()
    else:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(session_id + "\n", encoding="utf-8")
    return log_root / f"runtime_{session_id}.log"


def configure_global_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    resolved = str(log_path.resolve())
    for handler in list(logger.handlers):
        if getattr(handler, "_production_colab_global", False) and getattr(handler, "baseFilename", None) != resolved:
            logger.removeHandler(handler)
            handler.close()
    if not any(
        getattr(handler, "_production_colab_global", False)
        and getattr(handler, "baseFilename", None) == resolved
        for handler in logger.handlers
    ):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler._production_colab_global = True  # type: ignore[attr-defined]
        logger.addHandler(file_handler)

    if not any(getattr(handler, "_production_colab_console", False) for handler in logger.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        console._production_colab_console = True  # type: ignore[attr-defined]
        logger.addHandler(console)
    return logger


def attach_run_log(logger: logging.Logger, run_log: Path) -> logging.Handler:
    run_log.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(run_log, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return handler


def run_logged_command(command: Sequence[str], logger: logging.Logger, *, cwd=None, env=None) -> None:
    safe_command = " ".join(shlex.quote(redact_text(part)) for part in command)
    logger.info("COMMAND START: %s", safe_command)
    started = time.monotonic()
    process = subprocess.Popen(
        [str(part) for part in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        logger.info("%s", redact_text(line.rstrip("\n")))
    return_code = process.wait()
    duration = time.monotonic() - started
    logger.info("COMMAND END: exit_code=%s duration_seconds=%.2f", return_code, duration)
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)

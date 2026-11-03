"""A script run as one queue item: the stored record, content-addressed source, and the run-state to item-state mapping.

The item's ``request_json`` is a script request record (``kind`` ``script``), not a generation request. It names its run
(``run_id``) before anything starts; the run's own ledger lives in ``script_runs.sqlite3`` and is only ever *read* here
(never written), because the two stores are separate files with no shared transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dtflow.core.models import ValidationError

KIND_SCRIPT = "script"
MAX_SOURCE_BYTES = 1_048_576
# Child attempt states that can no longer change; anything else (or "unknown") means the outcome is unresolved.
TERMINAL_ATTEMPTS = ("succeeded", "failed", "cancelled", "save_failed", "interrupted_before_submission")


@dataclass(frozen=True, slots=True)
class ScriptRequest:
    run_id: str
    source_name: str
    source_sha256: str
    max_generations: int
    answers: list | None
    backend: str
    base: dict[str, Any]  # asdict of the starting ResolvedGenerationRequest (text-to-image)
    require_start: bool = False  # desktop Setup must park before any generation


def is_script_record(record: Any) -> bool:
    return isinstance(record, dict) and record.get("kind") == KIND_SCRIPT


def script_record(
    *,
    run_id: str,
    source_name: str,
    source_sha256: str,
    base: dict[str, Any],
    backend: str,
    max_generations: int,
    answers: list | None,
    expected_steps: int | None = None,
    require_start: bool = False,
) -> dict[str, Any]:
    return {
        "kind": KIND_SCRIPT,
        "run_id": run_id,
        "source_name": source_name,
        "source_sha256": source_sha256,
        "answers": answers,
        "expected_steps": expected_steps,
        "require_start": require_start,
        "start": {"backend": backend, "max_generations": max_generations, "base": base},
    }


def script_request_from_record(record: Any) -> ScriptRequest:
    """Decode and validate a stored script record; raises ``ValidationError`` for anything malformed."""
    if not is_script_record(record):
        raise ValidationError("Stored item is not a script request.")
    start = record.get("start")
    run_id, name, sha = record.get("run_id"), record.get("source_name"), record.get("source_sha256")
    answers = record.get("answers")
    require_start = record.get("require_start", False)
    if not (
        isinstance(start, dict)
        and isinstance(run_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", run_id) is not None
        and isinstance(name, str)
        and isinstance(sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", sha) is not None
        and (answers is None or isinstance(answers, list))
        and type(require_start) is bool
        and (not require_start or answers is None)
        and isinstance(start.get("base"), dict)
        and isinstance(start.get("backend"), str)
        and type(start.get("max_generations")) is int
        and 1 <= start["max_generations"] <= 32
    ):
        raise ValidationError("Stored script request is malformed.")
    return ScriptRequest(run_id, name, sha, start["max_generations"], answers, start["backend"], start["base"], require_start)


def source_path(state: Path, sha256: str) -> Path:
    return Path(state) / "script_sources" / f"{sha256}.js"


def store_source(state: Path, source: str) -> str:
    """Keep the script text under its SHA-256 (until the managed library exists); returns the SHA."""
    data = source.encode("utf-8")
    if len(data) > MAX_SOURCE_BYTES:
        raise ValidationError("Script source is too large.")
    sha = hashlib.sha256(data).hexdigest()
    path = source_path(state, sha)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    return sha


def load_source(state: Path, sha256: str) -> str:
    try:
        data = source_path(state, sha256).read_bytes()
    except OSError as exc:
        raise ValidationError("The script source for this item is no longer available.") from exc
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValidationError("The stored script source does not match its recorded hash.")
    return data.decode("utf-8")


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    state: str
    error: str | None
    children: tuple[str, ...]  # attempt ids in call order
    created_at: str | None = None
    finished_at: str | None = None


def read_run_snapshot(directory: Path, run_id: str) -> RunSnapshot | None:
    """The run row and its ordered children, read-only; ``None`` when no such run exists (it never started)."""
    path = Path(directory) / "script_runs.sqlite3"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)) as connection:
        connection.execute("PRAGMA query_only=ON")
        try:
            row = connection.execute(
                "SELECT state, error, created_at, finished_at FROM script_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            children = tuple(
                r[0]
                for r in connection.execute(
                    "SELECT attempt_id FROM script_run_children WHERE run_id=? ORDER BY ordinal", (run_id,)
                )
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None
            raise
    return RunSnapshot(row[0], row[1], children, row[2], row[3])


def child_states(db: sqlite3.Connection, children: tuple[str, ...]) -> list[str | None]:
    """The attempt state of each child (``None`` when its attempt row was never created)."""
    states: list[str | None] = []
    for attempt_id in children:
        row = db.execute("SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        states.append(row[0] if row else None)
    return states


def item_outcome(run: RunSnapshot, states: list[str | None]) -> tuple[str, str | None]:
    """The item state (and failure JSON, if any) a run's state plus its children decide. Never invents success."""
    if run.state == "completed":
        if any(s not in TERMINAL_ATTEMPTS for s in states):  # missing attempts are unresolved too
            message = "The script completed but a step has no confirmed outcome; check the backend before retrying."
            return "unknown", json.dumps({"code": "script_step_unknown", "message": message})
        return "succeeded", None
    if run.state == "stopped":
        return "cancelled", None
    if run.state == "failed":
        return "failed", json.dumps({"code": "script_failed", "message": run.error or "Script run failed."})
    unresolved = run.state == "running" or any(s not in TERMINAL_ATTEMPTS for s in states)
    if unresolved:
        return "unknown", json.dumps({"code": "script_interrupted", "message": run.error or "Run interrupted."})
    return "failed", json.dumps({"code": "script_interrupted", "message": run.error or "Run interrupted."})

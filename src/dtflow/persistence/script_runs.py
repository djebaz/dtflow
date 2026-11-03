"""Durable ScriptRun ledger in ``<state>/script_runs.sqlite3``.

Versioned independently of the application and of the attempt store (``PRAGMA user_version``); a newer schema is
rejected, never rewritten or reset. A run left ``running`` by a crash becomes ``interrupted`` when the store is next
opened and is never resumed or replayed. Child attempts are linked in order, before any external submission by the
caller, and are never removed.
"""

from __future__ import annotations

import errno
import json
import logging
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("dtflow.script")
SCHEMA_VERSION = 3
PROMPT_POLL_SECONDS = 0.25
MAX_PROMPT_JSON_CHARS = 1_000_000
MAX_ANSWERS_JSON_CHARS = 6_000_000
STATES = ("running", "completed", "failed", "stopped", "interrupted")
TERMINAL = ("completed", "failed", "stopped", "interrupted")


class ScriptRunStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScriptRun:
    run_id: str
    state: str
    source_name: str
    source_sha256: str
    api_version: str | None
    vendored_js_sha256: str
    answers_json: str
    context_json: str
    created_at: str
    finished_at: str | None
    error: str | None
    children: tuple[str, ...]


_PROMPTS_SQL = """
CREATE TABLE IF NOT EXISTS script_run_prompts (
    run_id TEXT NOT NULL REFERENCES script_runs(run_id),
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    confirm TEXT NOT NULL,
    components_json TEXT NOT NULL,
    answer_json TEXT,
    state TEXT NOT NULL CHECK (state IN ('open','answered','cancelled')),
    created_at TEXT NOT NULL,
    answered_at TEXT,
    PRIMARY KEY (run_id, ordinal)
);
"""


@dataclass(frozen=True, slots=True)
class ScriptPrompt:
    run_id: str
    ordinal: int
    title: str
    confirm: str
    components: list
    state: str
    answers: list | None


def _lock_is_held(exc: OSError) -> bool:
    """True only for "another holder has the lock"; anything else (ENOLCK, EOPNOTSUPP, EINVAL...) means unsupported."""
    return isinstance(exc, BlockingIOError) or exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)


def _try_lock(path: Path):
    """Take an exclusive OS lock on ``path`` and return the open file, or ``None`` if another holder has it.

    The lock lives exactly as long as the owning process (the OS drops it on exit or crash), so ownership cannot be
    confused by process-id reuse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - the open file is the lock and is returned to the caller
    try:
        if sys.platform == "win32":
            import msvcrt

            if handle.seek(0, 2) == 0:
                handle.write(b"x")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if _lock_is_held(exc):
            return None
        raise ScriptRunStoreError(
            f"This filesystem does not support the script-run ownership lock ({type(exc).__name__}: {exc})."
        ) from exc
    return handle


def _release(handle, path: Path) -> None:
    handle.close()
    try:
        path.unlink()
    except OSError:
        pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ScriptRunStore:
    def __init__(self, state: Path) -> None:
        self.path = Path(state) / "script_runs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._held: dict[str, object] = {}
        self.unverified: list[str] = []  # running rows whose liveness could not be decided (no lock support)
        with closing(self._connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ScriptRunStoreError("Unsupported script-run schema; no reset or migration was performed.")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS script_runs (
                        run_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL CHECK (state IN ('running','completed','failed','stopped','interrupted')),
                        source_name TEXT NOT NULL,
                        source_sha256 TEXT NOT NULL,
                        api_version TEXT,
                        vendored_js_sha256 TEXT NOT NULL,
                        answers_json TEXT NOT NULL DEFAULT '{}',
                        context_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        finished_at TEXT,
                        error TEXT
                    );
                    CREATE TABLE IF NOT EXISTS script_run_children (
                        run_id TEXT NOT NULL REFERENCES script_runs(run_id),
                        ordinal INTEGER NOT NULL,
                        attempt_id TEXT NOT NULL,
                        PRIMARY KEY (run_id, ordinal)
                    );
                    """
                )
            if version < 2:  # schema 2 adds the mid-run input prompts; a v1 file keeps all of its rows
                connection.executescript(_PROMPTS_SQL)
            if version < 3:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS script_prompt_inputs (
                        token TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES script_runs(run_id),
                        ordinal INTEGER NOT NULL,
                        field_json TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        record_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS script_form_answers (
                        source_sha256 TEXT NOT NULL,
                        form_sha256 TEXT NOT NULL,
                        answers_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (source_sha256, form_sha256)
                    );
                """)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            # A run is live while its owner holds the run's OS lock. Only runs whose lock is free were interrupted by a
            # crash; a second app instance sharing this state directory must not invalidate a live run.
            with connection:
                for (run_id,) in connection.execute("SELECT run_id FROM script_runs WHERE state='running'").fetchall():
                    lock = self._lock_path(run_id)
                    try:
                        handle = _try_lock(lock)
                    except ScriptRunStoreError:
                        self.unverified.append(run_id)
                        log.warning("Script run %s left running: ownership lock unsupported here.", run_id)
                        continue
                    if handle is not None:
                        connection.execute(
                            "UPDATE script_runs SET state='interrupted', finished_at=? WHERE run_id=? AND state='running'",
                            (_now(), run_id),
                        )
                        _release(handle, lock)

    def _lock_path(self, run_id: str) -> Path:
        return self.path.parent / "script_run_locks" / f"{run_id}.lock"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def create(
        self,
        *,
        source_name: str,
        source_sha256: str,
        api_version: str | None,
        vendored_js_sha256: str,
        answers_json: str = "{}",
        context_json: str = "{}",
        run_id: str | None = None,
    ) -> str:
        """Create the run row, owned before it exists. ``run_id`` lets a queue item name its run durably beforehand;
        it must be unused."""
        run_id = run_id or uuid.uuid4().hex
        handle = _try_lock(self._lock_path(run_id))  # held before the row exists so no opener can see it unowned
        if handle is None:
            raise ScriptRunStoreError("Could not take the script-run ownership lock.")
        self._held[run_id] = handle
        try:
            self._insert(
                run_id, source_name, source_sha256, api_version, vendored_js_sha256, answers_json, context_json
            )
        except Exception:
            self._held.pop(run_id, None)
            _release(handle, self._lock_path(run_id))
            raise
        return run_id

    def _insert(self, run_id, source_name, source_sha256, api_version, vendored_js_sha256, answers_json, context_json):
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO script_runs (run_id, state, source_name, source_sha256, api_version, vendored_js_sha256,"
                " answers_json, context_json, created_at) VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    source_name,
                    source_sha256,
                    api_version,
                    vendored_js_sha256,
                    answers_json,
                    context_json,
                    _now(),
                ),
            )

    def append_answers(self, run_id: str, entry: dict) -> int:
        """Durably append one form-answer record (before the script sees the answers). Returns the entry count."""
        import json

        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state, answers_json FROM script_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row[0] != "running":
                raise ScriptRunStoreError("Answers can only be recorded for a running script run.")
            try:
                entries = json.loads(row[1])
            except ValueError:
                entries = []
            if not isinstance(entries, list):
                entries = []
            entries.append(entry)
            text = json.dumps(entries, separators=(",", ":"))
            if len(text) > MAX_ANSWERS_JSON_CHARS:
                raise ScriptRunStoreError("Recorded answers exceed the size limit.")
            connection.execute("UPDATE script_runs SET answers_json=? WHERE run_id=?", (text, run_id))
        return len(entries)

    def link_child(self, run_id: str, attempt_id: str) -> int:
        """Atomically reserve a unique child ordinal before any provider submission.

        Parallel batch workers use distinct SQLite connections. Lock the writer before
        reading MAX(ordinal), otherwise workers can select the same next ordinal.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT state FROM script_runs WHERE run_id=?", (run_id,)).fetchone()
            if state is None or state[0] != "running":
                raise ScriptRunStoreError("Child attempts can only be linked to a running script run.")
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM script_run_children WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO script_run_children (run_id, ordinal, attempt_id) VALUES (?, ?, ?)",
                (run_id, ordinal, attempt_id),
            )
        return ordinal

    def finish(self, run_id: str, state: str, error: str | None = None) -> bool:
        """Settle a running run; returns False (changing nothing) if it was no longer running, e.g. interrupted."""
        try:
            if state not in TERMINAL:
                raise ScriptRunStoreError("Unknown terminal script-run state.")
            with closing(self._connect()) as connection, connection:
                changed = connection.execute(
                    "UPDATE script_runs SET state=?, error=?, finished_at=? WHERE run_id=? AND state='running'",
                    (state, error, _now(), run_id),
                ).rowcount
            return changed == 1
        finally:
            # Always give up ownership, even if settlement failed (SQLite timeout or write error): the row then stays
            # 'running' with a free lock, so the next open recovers it as interrupted instead of treating it as live.
            handle = self._held.pop(run_id, None)
            if handle is not None:
                _release(handle, self._lock_path(run_id))

    def get(self, run_id: str) -> ScriptRun | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT run_id, state, source_name, source_sha256, api_version, vendored_js_sha256, answers_json,"
                " context_json, created_at, finished_at, error FROM script_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            children = tuple(
                r[0]
                for r in connection.execute(
                    "SELECT attempt_id FROM script_run_children WHERE run_id=? ORDER BY ordinal", (run_id,)
                )
            )
        return ScriptRun(*row, children)

    def list_ids(self) -> tuple[str, ...]:
        with closing(self._connect()) as connection:
            return tuple(r[0] for r in connection.execute("SELECT run_id FROM script_runs ORDER BY created_at DESC"))

    # -- mid-run input (requestFromUser) --------------------------------------------------------------------------

    def open_prompt(self, run_id: str, title: str, confirm: str, components: list) -> int:
        """Durably publish a form for the run and return its ordinal. Any earlier open prompt of the run is superseded."""
        from dtflow.scripting.forms import validate_components

        validate_components(components)
        if any(not isinstance(x, str) or len(x) > 64_000 for x in (title, confirm)):
            raise ScriptRunStoreError("Form title or confirmation exceeds its limit.")
        text = json.dumps(components, separators=(",", ":"))
        if len(text) > MAX_PROMPT_JSON_CHARS:
            raise ScriptRunStoreError("The form is too large.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT state FROM script_runs WHERE run_id=?", (run_id,)).fetchone()
            if state is None or state[0] != "running":
                raise ScriptRunStoreError("A form can only be opened for a running script run.")
            connection.execute(
                "UPDATE script_run_prompts SET state='cancelled', answered_at=? WHERE run_id=? AND state='open'",
                (_now(), run_id),
            )
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM script_run_prompts WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if ordinal > 128:
                raise ScriptRunStoreError("This script run exceeded its 128-form limit.")
            connection.execute(
                "INSERT INTO script_run_prompts (run_id, ordinal, title, confirm, components_json, state, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'open', ?)",
                (run_id, ordinal, title, confirm, text, _now()),
            )
        return ordinal

    def answer_prompt(self, run_id: str, ordinal: int, answers: list) -> bool:
        """Record the answers of an open prompt; False (nothing changed) if it is no longer open."""
        from dtflow.scripting.forms import (
            MAX_ANSWER_BYTES,
            bounded_json,
            form_key,
            recallable_answers,
            validate_answers,
        )

        text = bounded_json(answers, MAX_ANSWER_BYTES)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT p.components_json, r.source_sha256 FROM script_run_prompts p"
                " JOIN script_runs r USING (run_id)"
                " WHERE p.run_id=? AND p.ordinal=? AND p.state='open' AND r.state='running'",
                (run_id, ordinal),
            ).fetchone()
            if row is None:
                return False

            def origin(field, kind, token):
                found = connection.execute(
                    "SELECT 1 FROM script_prompt_inputs WHERE token=? AND run_id=? AND ordinal=?"
                    " AND field_json=? AND kind=?",
                    (token, run_id, ordinal, json.dumps(field), kind),
                ).fetchone()
                if found is None:
                    raise ScriptRunStoreError("Selected input does not belong to this run, form and field.")

            validate_answers(json.loads(row[0]), answers, origin=origin)
            changed = (
                connection.execute(
                    "UPDATE script_run_prompts SET state='answered', answer_json=?, answered_at=?"
                    " WHERE run_id=? AND ordinal=? AND state='open'",
                    (text, _now(), run_id, ordinal),
                ).rowcount
                == 1
            )
            if changed:
                connection.execute(
                    "INSERT INTO script_form_answers VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(source_sha256, form_sha256) DO UPDATE SET"
                    " answers_json=excluded.answers_json, updated_at=excluded.updated_at",
                    (
                        row[1],
                        form_key(json.loads(row[0])),
                        bounded_json(recallable_answers(json.loads(row[0]), answers), MAX_ANSWER_BYTES),
                        _now(),
                    ),
                )
                connection.execute(
                    "DELETE FROM script_form_answers WHERE rowid NOT IN"
                    " (SELECT rowid FROM script_form_answers ORDER BY updated_at DESC, rowid DESC LIMIT 128)"
                )
            return changed

    def register_prompt_input(self, run_id: str, ordinal: int, field: tuple, kind: str, record: dict) -> str:
        """Register a worker-created owned snapshot, only while its exact form is still open."""
        token = "dtflow-input:" + uuid.uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            live = connection.execute(
                "SELECT 1 FROM script_run_prompts p JOIN script_runs r USING(run_id)"
                " WHERE p.run_id=? AND p.ordinal=? AND p.state='open' AND r.state='running'",
                (run_id, ordinal),
            ).fetchone()
            if live is None:
                raise ScriptRunStoreError("The input form has closed; selected files were not submitted.")
            count = connection.execute(
                "SELECT COUNT(*) FROM script_prompt_inputs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if count >= 1024:
                raise ScriptRunStoreError("This script run exceeded its 1024 owned-input selection limit.")
            connection.execute(
                "INSERT INTO script_prompt_inputs VALUES (?, ?, ?, ?, ?, ?)",
                (token, run_id, ordinal, json.dumps(field), kind, json.dumps(record)),
            )
        return token

    def prompt_input(self, run_id: str, ordinal: int, field: tuple, kind: str, token: str) -> dict:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_json FROM script_prompt_inputs"
                " WHERE token=? AND run_id=? AND ordinal=? AND field_json=? AND kind=?",
                (token, run_id, ordinal, json.dumps(field), kind),
            ).fetchone()
        if row is None:
            raise ScriptRunStoreError("Selected input does not belong to this run, form and field.")
        return json.loads(row[0])

    def cancel_prompts(self, run_id: str) -> None:
        """Resolve every open prompt of the run as cancelled (Stop, or the script ended)."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE script_run_prompts SET state='cancelled', answered_at=? WHERE run_id=? AND state='open'",
                (_now(), run_id),
            )

    def cancel_prompt(self, run_id: str, ordinal: int) -> bool:
        with closing(self._connect()) as connection, connection:
            return (
                connection.execute(
                    "UPDATE script_run_prompts SET state='cancelled', answered_at=?"
                    " WHERE run_id=? AND ordinal=? AND state='open'",
                    (_now(), run_id, ordinal),
                ).rowcount
                == 1
            )

    def prompt(self, run_id: str, ordinal: int) -> ScriptPrompt | None:
        with closing(self._connect()) as connection:
            row = connection.execute(_PROMPT_SELECT + " WHERE run_id=? AND ordinal=?", (run_id, ordinal)).fetchone()
        return _prompt(row)

    def wait_for_answer(
        self, run_id: str, ordinal: int, cancel: threading.Event, poll: float = PROMPT_POLL_SECONDS
    ) -> list | None:
        """Block until the prompt is answered; ``None`` when it was cancelled or Stop was requested."""
        while True:
            if cancel.is_set():
                self.cancel_prompts(run_id)
                return None
            found = self.prompt(run_id, ordinal)
            if found is None or found.state == "cancelled":
                return None
            if found.state == "answered":
                return found.answers
            time.sleep(poll)


_PROMPT_SELECT = "SELECT run_id, ordinal, title, confirm, components_json, state, answer_json FROM script_run_prompts"


def _prompt(row) -> ScriptPrompt | None:
    if row is None:
        return None
    return ScriptPrompt(
        row[0], row[1], row[2], row[3], json.loads(row[4]), row[5], None if row[6] is None else json.loads(row[6])
    )


def read_open_prompt(directory: Path, run_id: str) -> ScriptPrompt | None:
    """The run's open prompt, read-only; ``None`` when there is none (or the ledger predates prompts)."""
    path = Path(directory) / "script_runs.sqlite3"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)) as connection:
        connection.execute("PRAGMA query_only=ON")
        try:
            row = connection.execute(
                _PROMPT_SELECT + " WHERE run_id=? AND state='open' ORDER BY ordinal DESC LIMIT 1", (run_id,)
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None
            raise
    return _prompt(row)


def read_recalled_answers(directory: Path, run_id: str, components: list) -> list | None:
    """Read-only suggestions for the exact source hash and complete descriptor fingerprint."""
    from dtflow.scripting.forms import form_key, validate_answers

    path = Path(directory) / "script_runs.sqlite3"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            return None
        row = connection.execute(
            "SELECT a.answers_json FROM script_form_answers a JOIN script_runs r"
            " ON r.source_sha256=a.source_sha256 WHERE r.run_id=? AND a.form_sha256=?",
            (run_id, form_key(components)),
        ).fetchone()
    if row is None:
        return None
    answers = json.loads(row[0])
    validate_answers(components, answers)
    return answers

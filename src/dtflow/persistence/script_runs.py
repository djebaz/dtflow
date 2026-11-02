"""Durable ScriptRun ledger in ``<state>/script_runs.sqlite3``.

Versioned independently of the application and of the attempt store (``PRAGMA user_version``); a newer schema is
rejected, never rewritten or reset. A run left ``running`` by a crash becomes ``interrupted`` when the store is next
opened and is never resumed or replayed. Child attempts are linked in order, before any external submission by the
caller, and are never removed.
"""

from __future__ import annotations

import errno
import logging
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("dtflow.script")
SCHEMA_VERSION = 1
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
    ) -> str:
        run_id = uuid.uuid4().hex
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

    def link_child(self, run_id: str, attempt_id: str) -> int:
        """Reserve the next ordinal for a child attempt; call before the attempt is submitted."""
        with closing(self._connect()) as connection, connection:
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

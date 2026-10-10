"""Single-owner SQLite attempts with short transactions and one owned worker."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import BackendFailure, RemoteOutcome, ResolvedGenerationRequest, SubmissionStage, ValidationError
from dtflow.core.request_records import upgraded_request_record
from dtflow.core.tasks import settle
from dtflow.persistence.queues import QUEUE_SCHEMA, QueueMixin

# 1: attempts only. 2: queues. 3: explicit request v2/image-input references.
# A supported older writer open backs up, then migrates/reconciles transactionally.
SCHEMA_VERSION = 3


class PersistenceError(RuntimeError):
    """A sanitized state failure; never grounds for remote resubmission."""


class StateBusyError(PersistenceError):
    """Another process holds the state directory's ownership lock; nothing was read, written or dispatched."""


def patched_sqlite(version: str) -> bool:
    parts = tuple(int(x) for x in version.split(".")[:3])
    return parts >= (3, 51, 3) or (3, 50, 7) <= parts < (3, 51, 0) or (3, 44, 6) <= parts < (3, 45, 0)


class SQLiteAttempts(QueueMixin):
    """Own one local state directory until close; queues never replay an attempt."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dtflow-state")
        self._connection: sqlite3.Connection | None = None
        self._lock = None
        self._closed = False

    async def _run(self, function, *args):
        if self._closed:
            raise PersistenceError("Execution store is closed.")
        loop = asyncio.get_running_loop()
        try:
            return await settle(loop.run_in_executor(self._pool, function, *args))
        except (sqlite3.Error, OSError) as exc:
            raise PersistenceError("Execution state could not be read or committed.") from exc

    async def open(self) -> None:
        await self._run(self._open)

    def _open(self) -> None:
        if self._connection is not None:
            return
        if not patched_sqlite(sqlite3.sqlite_version):
            raise PersistenceError("SQLite needs an official WAL-reset fix (3.51.3+ or a supported backport).")
        if str(self.directory).startswith("\\\\"):
            raise PersistenceError("Execution state requires a local filesystem, not a UNC share.")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = (self.directory / "owner.lock").open("a+b")
        try:
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt

                if self._lock.read(1) == b"":
                    self._lock.write(b"0")
                    self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            self._lock = None
            raise StateBusyError("Another process owns this execution state directory.") from exc
        try:
            connection = sqlite3.connect(self.directory / "attempts.sqlite3", timeout=2.0)
            self._connection = connection
            connection.row_factory = sqlite3.Row
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in range(SCHEMA_VERSION + 1):
                raise PersistenceError("Unsupported execution-state schema; no reset or migration was performed.")
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode.lower() != "wal":
                raise PersistenceError("Execution state could not enable WAL.")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=2000")
            if version in (1, 2):
                # A fresh consistent backup for each migration attempt; never rely
                # on an old/partial backup left by a previously failed upgrade.
                copy = self.directory / f"attempts.pre-v3-from-v{version}-{uuid4().hex}.sqlite3"
                with copy.open("xb"):
                    pass
                with closing(sqlite3.connect(copy)) as destination:
                    connection.backup(destination)
            # One transaction covers creation or migration and ownership reconciliation: failure keeps the old state.
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("""CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    backend_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    submission_stage TEXT NOT NULL,
                    remote_outcome TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    failure_json TEXT,
                    artifacts_json TEXT NOT NULL DEFAULT '[]'
                )""")
                for statement in QUEUE_SCHEMA:
                    connection.execute(statement)
                if version in (1, 2):
                    self._migrate_requests(connection)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                # Ownership lock ensures we are not reconciling another live dispatcher.
                now = self._now()
                connection.execute(
                    """UPDATE attempts SET state='unknown', remote_outcome='unknown',
                    updated_at_utc=? WHERE state='possibly_submitted'""",
                    (now,),
                )
                connection.execute(
                    """UPDATE attempts SET state='save_failed',
                    updated_at_utc=? WHERE state='saving'""",
                    (now,),
                )
                connection.execute(
                    """UPDATE attempts SET state='interrupted_before_submission',
                    updated_at_utc=? WHERE state='prepared'""",
                    (now,),
                )
                self.reconcile(connection, now)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        except BaseException:
            self._close()
            raise

    @staticmethod
    def _migrate_requests(connection: sqlite3.Connection) -> None:
        """Preserve identities/artifacts/state; only make txt2img request fields explicit."""
        for table, key in (("attempts", "attempt_id"), ("queue_items", "item_id")):
            after = None
            while True:
                condition = f"WHERE {key}>?" if after is not None else ""
                rows = connection.execute(
                    f"SELECT {key}, request_json FROM {table} {condition} ORDER BY {key} LIMIT 128",
                    (after,) if after is not None else (),
                ).fetchall()
                if not rows:
                    break
                updates = []
                for row in rows:
                    try:
                        record = json.loads(row["request_json"])
                        if not isinstance(record, dict) or record.get("operation", "txt2img") != "txt2img":
                            raise ValidationError("Historical execution requests must remain text-to-image.")
                        upgraded = upgraded_request_record(record)
                        updates.append((json.dumps(upgraded, allow_nan=False), row[key]))
                    except (ValueError, TypeError, KeyError) as exc:
                        raise PersistenceError("Request migration refused; original execution state was preserved.") from exc
                connection.executemany(f"UPDATE {table} SET request_json=? WHERE {key}=?", updates)
                after = rows[-1][key]

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise PersistenceError("Execution store is not open.")
        return self._connection

    async def create(self, attempt_id: str, backend_id: str, request: ResolvedGenerationRequest) -> None:
        await self._run(self._create, attempt_id, backend_id, request)

    def _create(self, attempt_id: str, backend_id: str, request: ResolvedGenerationRequest) -> None:
        if request.image_inputs is not None and request.image_inputs.attempt_id != attempt_id:
            raise PersistenceError("Image inputs must belong to the newly created attempt.")
        db = self._db()
        with db:
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    request.request_id,
                    backend_id,
                    json.dumps(asdict(request), allow_nan=False),
                    "prepared",
                    SubmissionStage.NOT_SUBMITTED,
                    RemoteOutcome.NOT_STARTED,
                    self._now(),
                    None,
                    "[]",
                ),
            )

    async def transition(
        self,
        attempt_id: str,
        expected: tuple[str, ...],
        state: str,
        stage: SubmissionStage,
        outcome: RemoteOutcome,
        *,
        failure: BackendFailure | None = None,
        artifacts: tuple[str, ...] = (),
    ) -> None:
        await self._run(self._transition, attempt_id, expected, state, stage, outcome, failure, artifacts)

    def _transition(self, attempt_id, expected, state, stage, outcome, failure, artifacts) -> None:
        db = self._db()
        placeholders = ",".join("?" for _ in expected)
        with db:
            cursor = db.execute(
                f"""UPDATE attempts SET state=?, submission_stage=?, remote_outcome=?,
                updated_at_utc=?, failure_json=?, artifacts_json=?
                WHERE attempt_id=? AND state IN ({placeholders})""",
                (
                    state,
                    stage,
                    outcome,
                    self._now(),
                    json.dumps({k: v for k, v in asdict(failure).items() if k != "partial_artifacts"})
                    if failure
                    else None,
                    json.dumps(artifacts),
                    attempt_id,
                    *expected,
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceError("Attempt transition refused; request identity/state is not eligible.")

    async def inspect(self, attempt_id: str | None = None) -> tuple[dict[str, Any], ...]:
        return await self._run(self._inspect, attempt_id)

    def _inspect(self, attempt_id) -> tuple[dict[str, Any], ...]:
        db = self._db()
        rows = (
            db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,))
            if attempt_id
            else db.execute("SELECT * FROM attempts ORDER BY updated_at_utc, attempt_id")
        )
        result = []
        for row in rows:
            item = dict(row)
            item["request"] = json.loads(item.pop("request_json"))
            item["failure"] = json.loads(item.pop("failure_json") or "null")
            item["artifacts"] = json.loads(item.pop("artifacts_json"))
            result.append(item)
        return tuple(result)

    async def backup(self, target: Path) -> None:
        await self._run(self._backup, Path(target))

    def _backup(self, target: Path) -> None:
        if target.exists():
            raise PersistenceError("Backup destination already exists.")
        with sqlite3.connect(target) as destination:
            self._db().backup(destination)

    async def aclose(self) -> None:
        if self._closed:
            return
        try:
            await self._run(self._close)
        finally:
            self._closed = True
            self._pool.shutdown(wait=True)

    def _close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None

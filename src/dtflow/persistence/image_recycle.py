"""Durable, separately versioned image-recycling outcomes; never alter attempt history."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple


class ReconcileResult(NamedTuple):
    """Newly unsettled Trash and permanent intents from this pass only."""

    trash_uncertain: int
    delete_uncertain: int
    withdrawn_restorations: int


SCHEMA_VERSION = 2
FILENAME = "image_recycle.sqlite3"
_RECORD_UPSERT = """INSERT INTO records(identity,path,outcome,operation_id,size,mtime_ns,updated_utc)
    VALUES(?,?,?,?,?,?,?)
    ON CONFLICT(identity) DO UPDATE SET
      path=excluded.path, outcome=excluded.outcome, operation_id=excluded.operation_id,
      size=excluded.size, mtime_ns=excluded.mtime_ns, updated_utc=excluded.updated_utc"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def writer(state: Path):
    """Open a short SQLite transaction that survives application interruption."""
    directory = Path(state).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / FILENAME, timeout=10)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, SCHEMA_VERSION):
            raise ValueError("Unsupported image-recycle ledger schema.")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE IF NOT EXISTS records (
            identity TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            outcome TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            updated_utc TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            created_utc TEXT NOT NULL,
            requested_count INTEGER NOT NULL,
            completed_count INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL
        )""")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def attach_read_only(connection: sqlite3.Connection, state: Path) -> None:
    """Attach the versioned availability table without initializing user state."""
    file = Path(state).resolve() / FILENAME
    if file.is_file():
        uri = file.as_uri() + "?mode=ro"
        connection.execute("ATTACH DATABASE ? AS image_recycle", (uri,))
        if connection.execute("PRAGMA image_recycle.user_version").fetchone()[0] not in (1, SCHEMA_VERSION):
            raise ValueError("Unsupported image-recycle ledger schema.")
    else:
        connection.execute("ATTACH DATABASE ':memory:' AS image_recycle")
        connection.execute("CREATE TABLE image_recycle.records (identity TEXT PRIMARY KEY, outcome TEXT NOT NULL)")


def start_operation(state: Path, operation_id: str, count: int) -> None:
    """Record operation intent before any Windows Shell mutation."""
    with writer(state) as db:
        db.execute(
            "INSERT INTO operations (operation_id, created_utc, requested_count, outcome) VALUES (?, ?, ?, 'running')",
            (operation_id, _now(), count),
        )


def record_outcome(
    state: Path,
    operation_id: str,
    identity: str,
    path: str,
    outcome: str,
    size: int,
    mtime_ns: int,
) -> None:
    """Checkpoint each item independently; an unknown Shell result stays uncertain."""
    with writer(state) as db:
        db.execute(
            _RECORD_UPSERT,
            (identity, path, outcome, operation_id, size, mtime_ns, _now()),
        )
        if outcome != "pending":
            db.execute(
                "UPDATE operations SET completed_count=completed_count+1 WHERE operation_id=?",
                (operation_id,),
            )


def claim_recycle_intent(
    state: Path, operation_id: str, identity: str, path: str, size: int, mtime_ns: int, *, permanent: bool = False
) -> bool:
    """Atomically claim new/known-failed originals; leave unsafe ledger evidence intact.

    Called under the batch and original-file leases after filesystem revalidation.
    The conditional conflict update observes the latest outcome in this write
    transaction, rejecting pending/uncertain/recycled/restored and unknown states.
    Return only after durable intent commits, before the external Trash call.
    """
    with writer(state) as db:
        changed = db.execute(
            _RECORD_UPSERT + " WHERE records.outcome='failed'",
            (identity, path, "delete_pending" if permanent else "pending", operation_id, size, mtime_ns, _now()),
        ).rowcount
    return changed == 1


def finish_operation(state: Path, operation_id: str, outcome: str) -> None:
    with writer(state) as db:
        db.execute("UPDATE operations SET outcome=? WHERE operation_id=?", (outcome, operation_id))


def outcomes(state: Path) -> dict[str, tuple[str, str, int, int]]:
    """Read latest per-image outcomes; older stores need no migration."""
    path = Path(state).resolve() / FILENAME
    if not path.is_file():
        return {}
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] not in (1, SCHEMA_VERSION):
            raise ValueError("Unsupported image-recycle ledger schema.")
        return {
            identity: (outcome, file, size, mtime)
            for identity, outcome, file, size, mtime in connection.execute(
                "SELECT identity,outcome,path,size,mtime_ns FROM records"
            )
        }
    finally:
        connection.close()


def reconcile(state: Path) -> ReconcileResult:
    """Settle interrupted intent and withdraw unverified historical restorations.

    Return newly uncertain Trash/deletion counts and withdrawn restorations. No old path
    proves original content identity, even when size and timestamps match. Keep
    these records recycled until an explicit content-verified recovery exists.
    Never retry a Trash operation or remove any reappearing file.
    """
    file = Path(state).resolve() / FILENAME
    if not file.is_file():
        return ReconcileResult(0, 0, 0)
    with writer(state) as db:
        trash_uncertain = db.execute(
            "UPDATE records SET outcome='uncertain',updated_utc=? WHERE outcome='pending'",
            (_now(),),
        ).rowcount
        delete_uncertain = db.execute(
            "UPDATE records SET outcome='uncertain',updated_utc=? WHERE outcome='delete_pending'",
            (_now(),),
        ).rowcount
        db.execute("UPDATE operations SET outcome='interrupted' WHERE outcome='running'")
        withdrawn = db.execute(
            "UPDATE records SET outcome='recycled',updated_utc=? WHERE outcome='restored'",
            (_now(),),
        ).rowcount
    return ReconcileResult(trash_uncertain, delete_uncertain, withdrawn)

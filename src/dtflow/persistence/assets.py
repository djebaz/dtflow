"""Independent durable registry of locally known cloud assets (``<state>/assets.sqlite3``, schema version 3).

A row is a *local candidate*, not remote inventory: it records what this machine measured and what a server once
acknowledged. It never stores raw credentials, signed URLs or file contents. A version-3 side table records per-key and per-name references
under the stable opaque credential identity; schema-1 receipts remain historical, unassigned evidence until checked
under a selected key. Confirmed per-key receipts are reused without routine network checks.

Writers (``prepare`` and ``reconcile``) hold an exclusive ``assets.lock`` for their lifetime; opening as a writer
turns any leftover ``intent``/``transferring`` operation of an interrupted process into explicit ``unknown`` evidence.
Readers (``list``/``show``) take no lock and change nothing. Connections are short and per operation, so reading never
blocks behind a long transfer.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import PREPARATION_STATES, AssetContent, PreparedAsset, ValidationError
from dtflow.persistence import store
from dtflow.persistence.store import PersistenceError

SCHEMA_VERSION = 3
UNRESOLVED = ("intent", "transferring")
SOURCES = ("transfer", "already_exists", "reconciled")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS assets (
        asset_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        identifier TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size INTEGER NOT NULL,
        backend_id TEXT NOT NULL,
        origin TEXT NOT NULL,
        remote_id TEXT,
        state TEXT NOT NULL,
        evidence TEXT NOT NULL,
        source TEXT,
        provenance TEXT NOT NULL DEFAULT '',
        created_at_utc TEXT NOT NULL,
        confirmed_at_utc TEXT,
        updated_at_utc TEXT NOT NULL,
        UNIQUE (kind, backend_id, origin, sha256, size)
    )""",
    """CREATE TABLE IF NOT EXISTS asset_operations (
        operation_id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL REFERENCES assets(asset_id),
        action TEXT NOT NULL,
        state TEXT NOT NULL,
        evidence TEXT NOT NULL,
        started_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS asset_key_receipts (
        asset_id TEXT NOT NULL REFERENCES assets(asset_id),
        credential_id TEXT NOT NULL,
        identifier TEXT NOT NULL,
        remote_id TEXT NOT NULL,
        state TEXT NOT NULL,
        confirmed_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        PRIMARY KEY(asset_id, credential_id, identifier)
    )""",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AssetsBusyError(PersistenceError):
    """Another process holds the asset writer lock; nothing was prepared, uploaded or changed."""


def _lock_file(path: Path):
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AssetsBusyError("Another process is preparing or reconciling assets in this state directory.") from exc
    return handle


def _record(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class AssetRegistry:
    """Open with ``writer=True`` to prepare/reconcile (exclusive), or read-only for inspection."""

    def __init__(self, directory: Path, *, writer: bool = False):
        self.directory = Path(directory).resolve()
        self.writer = writer
        self._lock = None
        self._ready = False

    @property
    def path(self) -> Path:
        return self.directory / "assets.sqlite3"

    # -- lifecycle ------------------------------------------------------------------------------------------------

    async def open(self) -> None:
        try:
            await asyncio.to_thread(self._open)
        except (sqlite3.Error, OSError) as exc:
            raise PersistenceError("Asset state could not be read or committed.") from exc

    def _open(self) -> None:
        if self._ready:
            return
        if not store.patched_sqlite(sqlite3.sqlite_version):
            raise PersistenceError("SQLite needs an official WAL-reset fix (3.51.3+ or a supported backport).")
        if str(self.directory).startswith("\\\\"):
            raise PersistenceError("Asset state requires a local filesystem, not a UNC share.")
        if not self.writer:
            if self.path.exists():
                with closing(self._connect()) as db:
                    self._check_version(db, create=False)
            self._ready = True
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_file(self.directory / "assets.lock")
        try:
            with closing(self._connect()) as db:
                self._check_version(db, create=True)
                now = _now()
                db.execute("BEGIN IMMEDIATE")
                try:
                    # Writers are exclusive, so an unresolved operation here belongs to a process that no longer runs.
                    for table in ("asset_operations", "assets"):
                        db.execute(
                            f"UPDATE {table} SET state='unknown', evidence='interrupted', updated_at_utc=? "
                            "WHERE state IN ('intent','transferring')",
                            (now,),
                        )
                    db.execute("COMMIT")
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
        except BaseException:
            self._release()
            raise
        self._ready = True

    def _check_version(self, db: sqlite3.Connection, *, create: bool) -> None:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, SCHEMA_VERSION):
            raise PersistenceError("Unsupported asset-registry schema; no reset or migration was performed.")
        if create:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                if version == 2:
                    # Keep every v2 receipt, its opaque credential identity and history, while allowing
                    # distinct LoRA filenames with identical content to coexist within the same key.
                    db.execute("ALTER TABLE asset_key_receipts RENAME TO asset_key_receipts_v2")
                for statement in SCHEMA:
                    db.execute(statement)
                if version == 2:
                    db.execute(
                        "INSERT INTO asset_key_receipts "
                        "(asset_id, credential_id, identifier, remote_id, state, confirmed_at_utc, updated_at_utc) "
                        "SELECT asset_id, credential_id, identifier, remote_id, state, "
                        "confirmed_at_utc, updated_at_utc FROM asset_key_receipts_v2"
                    )
                    db.execute("DROP TABLE asset_key_receipts_v2")
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=2.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=2000")
        return db

    def _release(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    async def aclose(self) -> None:
        self._release()
        self._ready = False

    # -- operations -----------------------------------------------------------------------------------------------

    async def _run(self, function, *args):
        if not self._ready:
            raise PersistenceError("Asset registry is not open.")
        try:
            return await asyncio.to_thread(function, *args)
        except (sqlite3.Error, OSError) as exc:
            raise PersistenceError("Asset state could not be read or committed.") from exc

    def _write(self, body, *args):
        if not self.writer:
            raise PersistenceError("Asset registry was opened read-only.")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = body(db, *args)
                db.execute("COMMIT")
                return result
            except BaseException:
                db.execute("ROLLBACK")
                raise

    async def begin(
        self, content: AssetContent, backend_id: str, origin: str, action: str = "prepare"
    ) -> tuple[str, str]:
        """Persist intent (before any negotiation or transfer) and return ``(asset_id, operation_id)``."""
        return await self._run(self._write, self._begin, content, backend_id, origin, action)

    @staticmethod
    def _begin(db, content: AssetContent, backend_id: str, origin: str, action: str) -> tuple[str, str]:
        now, operation = _now(), uuid4().hex
        row = db.execute(
            "SELECT asset_id, state FROM assets WHERE kind=? AND backend_id=? AND origin=? AND sha256=? AND size=?",
            (content.kind, backend_id, origin, content.sha256, content.size),
        ).fetchone()
        if row is None:
            asset_id = uuid4().hex
            db.execute(
                "INSERT INTO assets (asset_id, kind, identifier, sha256, size, backend_id, origin, state, evidence,"
                " provenance, created_at_utc, updated_at_utc) VALUES (?,?,?,?,?,?,?,'intent','intent_recorded',?,?,?)",
                (asset_id, content.kind, content.identifier, content.sha256, content.size, backend_id, origin,
                 content.provenance, now, now),
            )  # fmt: skip
        else:
            asset_id = row["asset_id"]
            if action == "prepare" and row["state"] != "confirmed":
                db.execute(
                    "UPDATE assets SET state='intent', evidence='intent_recorded', updated_at_utc=? WHERE asset_id=?",
                    (now, asset_id),
                )
        db.execute(
            "INSERT INTO asset_operations (operation_id, asset_id, action, state, evidence, started_at_utc,"
            " updated_at_utc) VALUES (?,?,?,'intent','intent_recorded',?,?)",
            (operation, asset_id, action, now, now),
        )
        return asset_id, operation

    async def mark(self, asset_id: str, operation_id: str, state: str, evidence: str, *, asset: bool = True) -> None:
        """Record a non-confirmed state (``transferring``, ``unknown`` or ``failed``) for an operation.

        ``asset=False`` keeps the asset row as it is: a failed metadata check says nothing about an existing receipt.
        """
        if state not in PREPARATION_STATES or state == "confirmed":
            raise ValidationError("Use confirm() to record a confirmed receipt.")
        await self._run(self._write, self._mark, asset_id, operation_id, state, evidence, asset)

    @staticmethod
    def _mark(db, asset_id: str, operation_id: str, state: str, evidence: str, asset: bool) -> None:
        now = _now()
        db.execute(
            "UPDATE asset_operations SET state=?, evidence=?, updated_at_utc=? WHERE operation_id=?",
            (state, evidence, now, operation_id),
        )
        if asset:
            db.execute(
                "UPDATE assets SET state=?, evidence=?, updated_at_utc=? WHERE asset_id=?",
                (state, evidence, now, asset_id),
            )

    async def confirm(
        self,
        asset_id: str,
        operation_id: str | None,
        remote_id: str,
        source: str,
        *,
        credential_id: str | None = None,
        identifier: str | None = None,
    ) -> PreparedAsset:
        """Commit server evidence; keep separate references for each DT+ credential bucket."""
        if source not in SOURCES:
            raise ValidationError("Unsupported receipt source.")
        return await self._run(
            self._write, self._confirm, asset_id, operation_id, remote_id, source, credential_id, identifier
        )

    @staticmethod
    def _confirm(
        db,
        asset_id: str,
        operation_id: str | None,
        remote_id: str,
        source: str,
        credential_id: str | None,
        identifier: str | None,
    ) -> PreparedAsset:
        now = _now()
        # The legacy path still updates its one reference. For scoped DT+ keys, keep
        # an already-confirmed historical row unchanged; each key's current reference
        # lives in asset_key_receipts instead.
        where = (
            "WHERE asset_id=?"
            if credential_id is None
            else "WHERE asset_id=? AND (state!='confirmed' OR remote_id IS NULL)"
        )
        db.execute(
            "UPDATE assets SET remote_id=?, state='confirmed', evidence=?, source=?, confirmed_at_utc=?,"
            " updated_at_utc=? " + where,
            (remote_id, "confirmed_" + source, source, now, now, asset_id),
        )
        if operation_id is not None:
            db.execute(
                "UPDATE asset_operations SET state='confirmed', evidence=?, updated_at_utc=? WHERE operation_id=?",
                ("confirmed_" + source, now, operation_id),
            )
        row = db.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        if credential_id is not None:
            db.execute(
                "INSERT INTO asset_key_receipts "
                "(asset_id, credential_id, identifier, remote_id, state, confirmed_at_utc, updated_at_utc)"
                " VALUES (?,?,?,?, 'confirmed',?,?) "
                "ON CONFLICT(asset_id, credential_id, identifier) DO UPDATE SET "
                "remote_id=excluded.remote_id, state='confirmed', "
                "confirmed_at_utc=excluded.confirmed_at_utc, updated_at_utc=excluded.updated_at_utc",
                (asset_id, credential_id, identifier or row["identifier"], remote_id, now, now),
            )
        prepared = AssetRegistry._prepared(row)
        if credential_id is not None:
            from dataclasses import replace

            prepared = replace(
                prepared,
                identifier=identifier or row["identifier"],
                remote_id=remote_id,
                confirmed_at_utc=now,
            )
        return prepared

    async def scoped(self, asset_id: str, credential_id: str, identifier: str) -> PreparedAsset | None:
        """Return a confirmed receipt only for this exact key and logical LoRA filename."""
        return await self._run(self._scoped, asset_id, credential_id, identifier)

    def _scoped(self, asset_id: str, credential_id: str, identifier: str) -> PreparedAsset | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                return None
            row = db.execute(
                "SELECT a.*, k.identifier AS scoped_identifier, k.remote_id AS scoped_remote, "
                "k.confirmed_at_utc AS scoped_confirmed FROM assets a "
                "JOIN asset_key_receipts k ON k.asset_id=a.asset_id "
                "WHERE a.asset_id=? AND k.credential_id=? AND k.identifier=? AND k.state='confirmed'",
                (asset_id, credential_id, identifier),
            ).fetchone()
            if row is None:
                return None
            from dataclasses import replace

            return replace(
                self._prepared(row),
                identifier=row["scoped_identifier"],
                remote_id=row["scoped_remote"],
                confirmed_at_utc=row["scoped_confirmed"],
            )

    async def invalidate_scope(self, asset_id: str, credential_id: str, identifier: str) -> None:
        """Invalidate only this key/name receipt; shared bytes and other key records remain valid."""
        return await self._run(self._write, self._invalidate_scope, asset_id, credential_id, identifier)

    @staticmethod
    def _invalidate_scope(db, asset_id: str, credential_id: str, identifier: str) -> None:
        db.execute(
            "UPDATE asset_key_receipts SET state='missing', updated_at_utc=? "
            "WHERE asset_id=? AND credential_id=? AND identifier=?",
            (_now(), asset_id, credential_id, identifier),
        )

    @staticmethod
    def _prepared(row: sqlite3.Row) -> PreparedAsset:
        return PreparedAsset(
            row["backend_id"],
            row["origin"],
            row["identifier"],
            row["remote_id"],
            row["sha256"],
            row["size"],
            row["asset_id"],
            row["confirmed_at_utc"],
        )

    async def choices(self) -> tuple[PreparedAsset, ...]:
        """Known logical names, including those sharing content but prepared under different keys.

        These are local *candidates*. Dispatch must still resolve each one in the selected key's bucket.
        """
        return await self._run(self._choices)

    def _choices(self) -> tuple[PreparedAsset, ...]:
        if not self.path.exists():
            return ()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM assets WHERE kind='lora' AND state='confirmed' AND remote_id IS NOT NULL"
            ).fetchall()
            by_name = {(row["asset_id"], row["identifier"]): self._prepared(row) for row in rows}
            if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
                for row in db.execute(
                    "SELECT a.*, k.identifier AS logical_name, k.remote_id AS logical_remote, "
                    "k.confirmed_at_utc AS logical_confirmed FROM assets a "
                    "JOIN asset_key_receipts k ON a.asset_id=k.asset_id "
                    "WHERE a.kind='lora' AND k.state='confirmed'"
                ):
                    from dataclasses import replace

                    by_name[(row["asset_id"], row["logical_name"])] = replace(
                        self._prepared(row),
                        identifier=row["logical_name"],
                        remote_id=row["logical_remote"],
                        confirmed_at_utc=row["logical_confirmed"],
                    )
            return tuple(by_name.values())

    async def get(self, asset_id: str) -> dict[str, Any] | None:
        return await self._run(self._get, asset_id)

    def _get(self, asset_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
            if row is None:
                return None
            record = _record(row)
            record["operations"] = [
                _record(x)
                for x in db.execute(
                    "SELECT operation_id, action, state, evidence, started_at_utc, updated_at_utc "
                    "FROM asset_operations WHERE asset_id=? ORDER BY started_at_utc, rowid",
                    (asset_id,),
                )
            ]
            return record

    async def list(self) -> list[dict[str, Any]]:
        return await self._run(self._list)

    def _list(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(self._connect()) as db:
            return [_record(x) for x in db.execute("SELECT * FROM assets ORDER BY created_at_utc, asset_id")]

    async def prepared(self, asset_id: str) -> PreparedAsset:
        """The immutable reference of a *confirmed* local receipt; anything else is refused before dispatch."""
        record = await self.get(asset_id)
        if record is None:
            raise ValidationError("No local asset receipt has that identifier.")
        if record["state"] != "confirmed" or not record["remote_id"]:
            # Earlier schema-2 versions could mark the shared content row unknown after a
            # different API key failed, despite preserving another key's confirmed receipt.
            # Recover that *candidate* without guessing a key; dispatch resolves its scope.
            alternatives = [item for item in await self.choices() if item.receipt_id == asset_id]
            if alternatives:
                return alternatives[0]
            raise ValidationError(f"Asset receipt is {record['state']}, not confirmed; run `dtflow assets reconcile`.")
        return PreparedAsset(
            record["backend_id"],
            record["origin"],
            record["identifier"],
            record["remote_id"],
            record["sha256"],
            record["size"],
            record["asset_id"],
            record["confirmed_at_utc"],
        )

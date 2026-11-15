"""Independent durable registry of locally known cloud assets (``<state>/assets.sqlite3``, schema version 6).

A receipt row is a *local candidate*, not remote inventory. The content-keyed
``lora_records`` table owns file-detected and user-declared exact architecture plus local
editable metadata. Account/name-scoped DT+ receipts remain separate. No legacy schema
migration is provided for disposable pre-release state; incompatible state is never deleted.

Writers (``prepare`` and ``reconcile``) hold an exclusive ``assets.lock`` for their lifetime; opening as a writer
turns any leftover ``intent``/``transferring`` operation of an interrupted process into explicit ``unknown`` evidence.
Readers (``list``/``show``) take no lock and change nothing. Connections are short and per operation, so reading never
blocks behind a long transfer.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dtflow.core.models import (
    accepted_name_changes,
    PREPARATION_STATES,
    AssetContent,
    NameAlreadyAcceptedError,
    PreparedAsset,
    ValidationError,
)
from dtflow.persistence import lora_sources as _sources
from dtflow.persistence import store
from dtflow.persistence.store import PersistenceError

if TYPE_CHECKING:
    from dtflow.services.lora_naming import LoraPrepareMetadata

SCHEMA_VERSION = 6
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
    """CREATE TABLE IF NOT EXISTS lora_records (
        sha256 TEXT NOT NULL,
        size INTEGER NOT NULL,
        detected_version TEXT,
        detected_source TEXT NOT NULL DEFAULT 'unknown',
        declared_version TEXT,
        short_name TEXT,
        managed_identifier TEXT COLLATE NOCASE UNIQUE,
        format_code TEXT,
        format_source TEXT,
        trigger_words TEXT NOT NULL DEFAULT '',
        updated_at_utc TEXT NOT NULL,
        PRIMARY KEY (sha256, size),
        CHECK (detected_source IN ('unknown', 'file')),
        CHECK (format_code IS NULL OR format_code IN ('F16', 'Q8P')),
        CHECK ((format_code IS NULL) = (format_source IS NULL))
    )""",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def newest_per_name(choices, current=None) -> tuple:
    """One receipt per LoRA name.

    ``current`` maps a name to the (sha256, size) most recently prepared on purpose; that content wins, so preparing
    older bytes again is honoured. Otherwise the content first seen last wins (``choices`` come oldest first)."""
    newest = {}
    for item in choices:
        newest[item.identifier] = item
    for item in choices:
        if current and current.get(item.identifier) == (item.sha256, item.size):
            newest[item.identifier] = item
    return tuple(newest.values())


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


_LORA_COLUMNS = (
    "r.detected_version, r.detected_source, r.declared_version, r.short_name, r.managed_identifier, "
    "r.format_code, r.format_source, r.trigger_words"
)
_LORA_JOIN = " LEFT JOIN lora_records r ON r.sha256=a.sha256 AND r.size=a.size"


def _effective_version(row) -> tuple[str | None, str]:
    """An explicit user declaration wins without replacing file-derived evidence."""
    if row["declared_version"]:
        return row["declared_version"], "user"
    if row["detected_version"]:
        return row["detected_version"], "file"
    return None, "unknown"


def _record(row: sqlite3.Row) -> dict[str, Any]:
    value = {key: row[key] for key in row.keys()}
    if "detected_version" not in value:
        return value  # Operation rows contain no LoRA metadata.
    value["family"], value["family_source"] = _effective_version(value)
    value["architecture_conflict"] = bool(
        value["declared_version"]
        and value["detected_version"]
        and value["declared_version"] != value["detected_version"]
    )
    return value


class AssetRegistry:
    """Open with ``writer=True`` to prepare/reconcile (exclusive), or read-only for inspection."""

    def __init__(self, directory: Path, *, writer: bool = False, recover: bool = True):
        self.directory = Path(directory).resolve()
        self.writer = writer
        # ``recover=False`` is a scoped writer: it takes the lock but leaves other operations' states alone.
        self.recover = recover
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
                    for table in ("asset_operations", "assets") if self.recover else ():
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
        empty_database = not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        if version != SCHEMA_VERSION and not (create and version == 0 and empty_database):
            raise PersistenceError(
                "This pre-release asset database uses an incompatible schema. "
                "Close DTFlow and manually reset the .dtflow state directory before continuing; "
                "no state was deleted or migrated."
            )
        if create:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                for statement in (*SCHEMA, *_sources.SCHEMA_STATEMENTS):  # additive tables: no version bump
                    db.execute(statement)
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
        self,
        content: AssetContent,
        backend_id: str,
        origin: str,
        action: str = "prepare",
        *,
        family: str | None = None,
        lora_metadata: LoraPrepareMetadata | None = None,
    ) -> tuple[str, str]:
        """Atomically accept a managed name and persist intent before provider negotiation."""
        if lora_metadata is not None and (action != "prepare" or content.kind != "lora"):
            raise ValidationError("Managed LoRA metadata can only be accepted during explicit Prepare.")
        return await self._run(self._write, self._begin, content, backend_id, origin, action, family, lora_metadata)

    @staticmethod
    def _begin(
        db,
        content: AssetContent,
        backend_id: str,
        origin: str,
        action: str,
        family: str | None,
        lora_metadata: LoraPrepareMetadata | None,
    ) -> tuple[str, str]:
        now, operation = _now(), uuid4().hex
        if content.kind == "lora":
            # Content metadata and the preparation intent commit together. Refresh
            # file evidence on re-Prepare without resetting user edits.
            db.execute(
                "INSERT INTO lora_records "
                "(sha256, size, detected_version, detected_source, updated_at_utc) "
                "VALUES (?,?,?,?,?) ON CONFLICT(sha256, size) DO UPDATE SET "
                "detected_version=CASE WHEN ? THEN excluded.detected_version "
                "ELSE lora_records.detected_version END, "
                "detected_source=CASE WHEN ? THEN excluded.detected_source "
                "ELSE lora_records.detected_source END, "
                "updated_at_utc=CASE WHEN ? THEN excluded.updated_at_utc "
                "ELSE lora_records.updated_at_utc END",
                (
                    content.sha256,
                    content.size,
                    family,
                    "file" if family else "unknown",
                    now,
                    action == "prepare",
                    action == "prepare",
                    action == "prepare",
                ),
            )
            if lora_metadata is not None:
                # This is the authoritative local check. The same BEGIN IMMEDIATE
                # transaction commits the accepted managed identifier and Prepare
                # intent. Account-scoped receipt names are not naming authority.
                try:
                    identifier = lora_metadata.identifier
                except ValueError as exc:
                    raise ValidationError(str(exc)) from exc
                if content.identifier != identifier:
                    raise ValidationError("Prepare must use the accepted managed technical filename.")
                if lora_metadata.format_source not in ("converter_default", "filename_hint", "user"):
                    raise ValidationError("Choose a valid LoRA format provenance.")
                if lora_metadata.trigger_words is not None and not isinstance(lora_metadata.trigger_words, str):
                    raise ValidationError("LoRA trigger words must be text.")
                declared = lora_metadata.declared_version
                if declared is not None and declared != lora_metadata.exact_architecture:
                    raise ValidationError("Declared architecture must match the selected exact architecture.")
                existing = db.execute(
                    "SELECT managed_identifier, declared_version FROM lora_records WHERE sha256=? AND size=?",
                    (content.sha256, content.size),
                ).fetchone()
                effective = declared or existing["declared_version"] or family
                if effective != lora_metadata.exact_architecture:
                    raise ValidationError("Choose a verified or explicitly declared exact LoRA architecture.")
                previous = existing["managed_identifier"]
                if accepted_name_changes(previous, identifier) and not lora_metadata.replace_existing:
                    raise NameAlreadyAcceptedError(
                        "This content already has an accepted managed filename. "
                        "Choose explicit Re-Prepare to change it."
                    )
                conflict = db.execute(
                    "SELECT 1 FROM lora_records WHERE managed_identifier=? COLLATE NOCASE "
                    "AND NOT (sha256=? AND size=?)",
                    (identifier, content.sha256, content.size),
                ).fetchone()
                if conflict is not None:
                    raise ValidationError(
                        "That managed LoRA filename is already assigned to different content. "
                        "Edit the short name before preparing."
                    )
                db.execute(
                    "UPDATE lora_records SET short_name=?, managed_identifier=?, "
                    "declared_version=COALESCE(?, declared_version), "
                    "format_code=?, format_source=?, trigger_words=COALESCE(?, trigger_words), updated_at_utc=? "
                    "WHERE sha256=? AND size=?",
                    (
                        lora_metadata.short_name,
                        identifier,
                        declared,
                        lora_metadata.format_code,
                        lora_metadata.format_source,
                        lora_metadata.trigger_words,
                        now,
                        content.sha256,
                        content.size,
                    ),
                )
        row = db.execute(
            "SELECT asset_id, state FROM assets WHERE kind=? AND backend_id=? AND origin=? AND sha256=? AND size=?",
            (content.kind, backend_id, origin, content.sha256, content.size),
        ).fetchone()
        if row is None:
            asset_id = uuid4().hex
            db.execute(
                "INSERT INTO assets (asset_id, kind, identifier, sha256, size, "
                "backend_id, origin, state, evidence, provenance, created_at_utc, updated_at_utc)"
                " VALUES (?,?,?,?,?,?,?,'intent','intent_recorded',?,?,?)",
                (asset_id, content.kind, content.identifier, content.sha256, content.size,
                 backend_id, origin, content.provenance, now, now),
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

    async def mark(
        self,
        asset_id: str,
        operation_id: str,
        state: str,
        evidence: str,
        *,
        asset: bool = True,
        protect_confirmed: bool = False,
    ) -> None:
        """Record a non-confirmed state (``transferring``, ``unknown`` or ``failed``) for an operation.

        ``asset=False`` keeps the asset row as it is: a failed metadata check says nothing about an existing receipt.
        """
        if state not in PREPARATION_STATES or state == "confirmed":
            raise ValidationError("Use confirm() to record a confirmed receipt.")
        await self._run(self._write, self._mark, asset_id, operation_id, state, evidence, asset, protect_confirmed)

    @staticmethod
    def _mark(
        db,
        asset_id: str,
        operation_id: str,
        state: str,
        evidence: str,
        asset: bool,
        protect_confirmed: bool,
    ) -> None:
        now = _now()
        db.execute(
            "UPDATE asset_operations SET state=?, evidence=?, updated_at_utc=? WHERE operation_id=?",
            (state, evidence, now, operation_id),
        )
        if asset:
            # Preserve a confirmed historical receipt while recording this key's failed operation.
            where = "WHERE asset_id=? AND state!='confirmed'" if protect_confirmed else "WHERE asset_id=?"
            db.execute(
                "UPDATE assets SET state=?, evidence=?, updated_at_utc=? " + where,
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
        # Key-scoped references are stored separately; a confirmed historical row
        # is never rewritten by another key or a later recheck.
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
        row = db.execute(
            f"SELECT a.*, {_LORA_COLUMNS} FROM assets a{_LORA_JOIN} WHERE a.asset_id=?", (asset_id,)
        ).fetchone()
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
            row = db.execute(
                f"SELECT a.*, {_LORA_COLUMNS}, k.identifier AS scoped_identifier, "
                "k.remote_id AS scoped_remote, k.confirmed_at_utc AS scoped_confirmed "
                f"FROM assets a{_LORA_JOIN} JOIN asset_key_receipts k ON k.asset_id=a.asset_id "
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

    async def confirmed_scope_references(self, credential_id: str) -> tuple[tuple[str, str, str], ...]:
        """``(asset_id, identifier, remote_id)`` of this key's confirmed receipts, read-only."""
        return await self._run(self._confirmed_scope_references, credential_id)

    def _confirmed_scope_references(self, credential_id: str) -> tuple[tuple[str, str, str], ...]:
        if not self.path.exists():
            return ()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT asset_id, identifier, remote_id FROM asset_key_receipts "
                "WHERE credential_id=? AND state='confirmed'",
                (credential_id,),
            ).fetchall()
        return tuple((row["asset_id"], row["identifier"], row["remote_id"]) for row in rows)

    async def confirmed_scope_rows(self, credential_id: str) -> tuple[tuple[str, str, str, str], ...]:
        """``(asset_id, identifier, remote_id, last_confirmed_utc)`` of this key's confirmed receipts, read-only."""
        return await self._run(self._confirmed_scope_rows, credential_id)

    def _confirmed_scope_rows(self, credential_id: str) -> tuple[tuple[str, str, str, str], ...]:
        if not self.path.exists():
            return ()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT asset_id, identifier, remote_id, MAX(confirmed_at_utc, updated_at_utc) AS seen "
                "FROM asset_key_receipts WHERE credential_id=? AND state='confirmed'",
                (credential_id,),
            ).fetchall()
        return tuple((row["asset_id"], row["identifier"], row["remote_id"], row["seen"] or "") for row in rows)

    async def invalidate_scope(self, asset_id: str, credential_id: str, identifier: str) -> None:
        """Invalidate only this key/name receipt; shared bytes and other key records remain valid."""
        return await self._run(self._write, self._invalidate_scope, asset_id, credential_id, identifier)

    async def invalidate_observed(self, credential_id: str, observed: tuple[tuple[str, str, str, str], ...]) -> int:
        """Mark only the observed receipts of this key missing, in one transaction; returns how many changed.

        ``observed`` are ``(asset_id, identifier, remote_id, seen)`` as ``confirmed_scope_rows`` returned them before
        the list was requested. A receipt confirmed again since (any newer confirmation time, even with the same remote
        reference) or no longer confirmed is left alone, so a later Prepare is never undone by an older list."""
        return await self._run(self._write, self._invalidate_observed, credential_id, observed)

    @classmethod
    def _invalidate_observed(cls, db, credential_id: str, observed) -> int:
        changed = 0
        for asset_id, identifier, remote_id, seen in observed:
            current = db.execute(
                "SELECT 1 FROM asset_key_receipts WHERE asset_id=? AND credential_id=? AND identifier=? "
                "AND remote_id=? AND state='confirmed' AND MAX(confirmed_at_utc, updated_at_utc)=?",
                (asset_id, credential_id, identifier, remote_id, seen),
            ).fetchone()
            if current:
                cls._invalidate_scope(db, asset_id, credential_id, identifier)
                changed += 1
        return changed

    @staticmethod
    def _invalidate_scope(db, asset_id: str, credential_id: str, identifier: str) -> None:
        now = _now()
        affected = db.execute(
            "UPDATE asset_key_receipts SET state='missing', updated_at_utc=? "
            "WHERE asset_id=? AND credential_id=? AND identifier=? AND state='confirmed'",
            (now, asset_id, credential_id, identifier),
        )
        if not affected.rowcount:
            # A different key's absence cannot invalidate unassigned legacy receipt evidence.
            return
        # A historical content row is no longer locally confirmed when its final
        # key-scoped receipt disappears. Other keys retain their confirmed evidence.
        db.execute(
            "UPDATE assets SET state='unknown', evidence='scoped_receipt_missing', updated_at_utc=? "
            "WHERE asset_id=? AND state='confirmed' "
            "AND NOT EXISTS (SELECT 1 FROM asset_key_receipts "
            "WHERE asset_id=? AND state='confirmed')",
            (now, asset_id, asset_id),
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
            *_effective_version(row),
        )

    async def choices(self, *, include_missing: bool = False) -> tuple[PreparedAsset, ...]:
        """Known logical names, including those sharing content but prepared under different keys.

        These are local *candidates*. Dispatch must still resolve each one in the selected key's bucket.
        ``include_missing`` additionally exposes previously confirmed key receipts marked missing, solely as
        candidates for a fresh dispatch-time check/restoration; it does not treat them as currently confirmed.
        """
        return await self._run(self._choices, include_missing)

    def _choices(self, include_missing: bool = False) -> tuple[PreparedAsset, ...]:
        if not self.path.exists():
            return ()
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT a.*, {_LORA_COLUMNS} FROM assets a{_LORA_JOIN} "
                "WHERE a.kind='lora' AND a.state='confirmed' AND a.remote_id IS NOT NULL"
            ).fetchall()
            by_name = {(row["asset_id"], row["identifier"]): self._prepared(row) for row in rows}
            created = {row["asset_id"]: row["created_at_utc"] for row in rows}
            for row in db.execute(
                f"SELECT a.*, {_LORA_COLUMNS}, k.identifier AS logical_name, "
                "k.remote_id AS logical_remote, k.confirmed_at_utc AS logical_confirmed "
                f"FROM assets a{_LORA_JOIN} JOIN asset_key_receipts k ON a.asset_id=k.asset_id "
                "WHERE a.kind='lora' AND "
                + ("k.state IN ('confirmed','missing')" if include_missing else "k.state='confirmed'")
            ):
                from dataclasses import replace

                created[row["asset_id"]] = row["created_at_utc"]
                by_name[(row["asset_id"], row["logical_name"])] = replace(
                    self._prepared(row),
                    identifier=row["logical_name"],
                    remote_id=row["logical_remote"],
                    confirmed_at_utc=row["logical_confirmed"],
                )
            # Oldest first by when the file's content was first seen. A relink or key switch re-stamps
            # ``confirmed_at_utc`` on old content and must never make it look newer (#361).
            return tuple(sorted(by_name.values(), key=lambda item: (created.get(item.receipt_id, ""), item.identifier)))

    async def lora_metadata(self, sha256: str, size: int) -> dict[str, Any] | None:
        """Read canonical local content metadata; no provider or filesystem operation."""
        return await self._run(self._lora_metadata, sha256, size)

    def _lora_metadata(self, sha256: str, size: int) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM lora_records WHERE sha256=? AND size=?", (sha256, size)).fetchone()
            if row is None:
                return None
            return _record(row)

    async def lora_name_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Read accepted content-level managed names once for a Prepare batch.

        Historical asset identifiers and account receipts are intentionally not
        reservations. Preview users should fetch this once per editor batch;
        begin() repeats the collision check inside the writer transaction.
        """
        return await self._run(self._lora_name_snapshot)

    def _lora_name_snapshot(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT sha256, size, managed_identifier FROM lora_records WHERE managed_identifier IS NOT NULL"
            ).fetchall()
        return tuple(dict(row) for row in rows)

    async def lora_records(self) -> tuple[dict[str, Any], ...]:
        """Read local content identities and metadata without provider or filesystem work."""
        return await self._run(self._lora_records)

    def _lora_records(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM lora_records ORDER BY sha256, size").fetchall()
        return tuple(_record(row) for row in rows)

    # -- downloaded LoRA sources (additive table; readers treat a missing table as "no sources") ----------------------

    async def sources(self) -> tuple[dict[str, Any], ...]:
        """All recorded download sources, oldest first; empty before any writer created the table."""
        return await self._run(self._read_sources)

    def _read_sources(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        with closing(self._connect()) as db:
            return _sources.select_all(db)

    async def source(self, sha256: str) -> dict[str, Any] | None:
        return await self._run(self._read_source, sha256)

    def _read_source(self, sha256: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as db:
            return _sources.select_one(db, sha256)

    async def record_source(self, record: _sources.SourceRecord) -> None:
        """Insert or refresh provenance for verified downloaded bytes (keeps a user correction and the state)."""
        await self._run(self._write, lambda db: _sources.upsert(db, record, _now()))

    async def update_source(
        self,
        sha256: str,
        *,
        state: str | None = None,
        detail: str | None = None,
        file_state: str | None = None,
        converted: tuple[str, int] | None = None,
    ) -> bool:
        return await self._run(
            self._write,
            lambda db: _sources.set_state(
                db, sha256, _now(), state=state, detail=detail, file_state=file_state, converted=converted
            ),
        )

    async def declare_source_family(self, sha256: str, family: str | None) -> bool:
        """Record a user correction (``None`` clears it); the original label and classification stay."""
        return await self._run(self._write, lambda db: _sources.set_declared_family(db, sha256, family, _now()))

    async def delete_source(self, sha256: str) -> bool:
        return await self._run(self._write, lambda db: _sources.delete(db, sha256))

    async def accept_local_managed_name(self, sha256: str, size: int, metadata: LoraPrepareMetadata) -> bool:
        """Accept a first managed name for previously prepared content, with no Prepare or provider call."""
        return await self._run(self._write, self._accept_local_managed_name, sha256, size, metadata)

    @staticmethod
    def _accept_local_managed_name(db, sha256: str, size: int, metadata: LoraPrepareMetadata) -> bool:
        from dtflow.services.lora_naming import architecture_shortcode, managed_filename

        row = db.execute("SELECT * FROM lora_records WHERE sha256=? AND size=?", (sha256, size)).fetchone()
        if row is None:
            raise ValidationError("The measured LoRA record no longer exists.")
        expected_version = row["declared_version"] or row["detected_version"]
        if expected_version != metadata.exact_architecture or architecture_shortcode(expected_version) is None:
            raise ValidationError("The LoRA has no recognized exact architecture for automatic naming.")
        if row["short_name"] is not None and row["short_name"] != metadata.short_name:
            raise ValidationError("The saved LoRA short name cannot be replaced automatically.")
        if row["format_code"] is not None and row["format_code"] != metadata.format_code:
            raise ValidationError("The saved LoRA format cannot be replaced automatically.")
        if metadata.format_source not in ("converter_default", "filename_hint", "user"):
            raise ValidationError("Invalid LoRA format provenance.")
        try:
            identifier = managed_filename(
                metadata.short_name, architecture_shortcode(expected_version), metadata.format_code
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        previous = row["managed_identifier"]
        if previous is not None:
            if accepted_name_changes(previous, identifier):
                raise ValidationError("The already accepted LoRA identifier cannot change automatically.")
            return False
        if db.execute(
            "SELECT 1 FROM lora_records WHERE managed_identifier=? COLLATE NOCASE AND NOT (sha256=? AND size=?)",
            (identifier, sha256, size),
        ).fetchone():
            raise ValidationError("Another LoRA already owns that managed filename.")
        db.execute(
            "UPDATE lora_records SET short_name=?, managed_identifier=?, format_code=?, format_source=?, "
            "updated_at_utc=? WHERE sha256=? AND size=?",
            (metadata.short_name, identifier, metadata.format_code, metadata.format_source, _now(), sha256, size),
        )
        return True

    async def update_lora_metadata(self, sha256: str, size: int, **changes) -> dict[str, Any]:
        """Atomically change local LoRA metadata without renaming, uploading, or altering receipts.

        Accepted keys: declared_version, short_name, format_code, format_source, trigger_words.
        Omitted values are unchanged; None explicitly clears optional metadata.
        """
        allowed = {"declared_version", "short_name", "format_code", "format_source", "trigger_words"}
        if not changes or any(key not in allowed for key in changes):
            raise ValidationError("Only canonical LoRA metadata fields may be edited.")
        for key, value in changes.items():
            if (
                key == "declared_version"
                and value is not None
                and (not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_.-]+", value))
            ):
                raise ValidationError("Choose an exact Draw Things architecture version.")
            if (
                key == "short_name"
                and value is not None
                and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,20}", value))
            ):
                raise ValidationError("Short name must be 1-20 ASCII letters, digits, or underscores.")
            if key == "format_code" and value not in (None, "F16", "Q8P"):
                raise ValidationError("LoRA format must be F16 or Q8P.")
            if key == "format_source" and value not in (None, "converter_default", "filename_hint", "user"):
                raise ValidationError("LoRA format source must be a known provenance.")
            if key == "trigger_words" and not isinstance(value, str):
                raise ValidationError("LoRA trigger words must be text.")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValidationError("A measured SHA-256 is required.")
        if not isinstance(size, int) or size < 1:
            raise ValidationError("A measured positive byte size is required.")
        return await self._run(self._write, self._update_lora_metadata, sha256, size, changes)

    @staticmethod
    def _update_lora_metadata(db, sha256: str, size: int, changes: dict) -> dict[str, Any]:
        row = db.execute("SELECT * FROM lora_records WHERE sha256=? AND size=?", (sha256, size)).fetchone()
        if row is None:
            raise ValidationError("No measured LoRA content record exists; Prepare the file first.")
        updated = {key: row[key] for key in row.keys()}
        updated.update(changes)
        if (updated["format_code"] is None) != (updated["format_source"] is None):
            raise ValidationError("LoRA format and provenance must be set or cleared together.")
        assignments = ", ".join(f"{key}=?" for key in changes)
        db.execute(
            f"UPDATE lora_records SET {assignments}, updated_at_utc=? WHERE sha256=? AND size=?",
            (*changes.values(), _now(), sha256, size),
        )
        record = db.execute("SELECT * FROM lora_records WHERE sha256=? AND size=?", (sha256, size)).fetchone()
        return _record(record)

    async def resolve_prepared(self, prepared: tuple[PreparedAsset, ...]) -> tuple[PreparedAsset, ...]:
        """Enrich selected receipts from one local canonical read, preserving IDs and order."""
        if not prepared:
            return prepared
        return await self._run(self._resolve_prepared, prepared)

    def _resolve_prepared(self, prepared: tuple[PreparedAsset, ...]) -> tuple[PreparedAsset, ...]:
        if not self.path.exists():
            return prepared
        hashes = tuple(dict.fromkeys(item.sha256 for item in prepared))
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM lora_records WHERE sha256 IN (" + ",".join("?" for _ in hashes) + ")",
                hashes,
            ).fetchall()
        records = {(row["sha256"], row["size"]): row for row in rows}
        from dataclasses import replace

        resolved = []
        for item in prepared:
            row = records.get((item.sha256, item.size))
            if row is None:
                resolved.append(item)
            else:
                version, source = _effective_version(row)
                resolved.append(replace(item, family=version, family_source=source))
        return tuple(resolved)

    async def get(self, asset_id: str) -> dict[str, Any] | None:
        return await self._run(self._get, asset_id)

    def _get(self, asset_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as db:
            row = db.execute(
                f"SELECT a.*, {_LORA_COLUMNS} FROM assets a{_LORA_JOIN} WHERE a.asset_id=?", (asset_id,)
            ).fetchone()
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
            return [
                _record(x)
                for x in db.execute(
                    f"SELECT a.*, {_LORA_COLUMNS} FROM assets a{_LORA_JOIN} ORDER BY a.created_at_utc, a.asset_id"
                )
            ]

    async def prepared(self, asset_id: str) -> PreparedAsset:
        """The immutable reference of a *confirmed* local receipt; anything else is refused before dispatch."""
        record = await self.get(asset_id)
        if record is None:
            raise ValidationError("No local asset receipt has that identifier.")
        if record["state"] != "confirmed" or not record["remote_id"]:
            # Another key's validation can leave a shared row unconfirmed.
            # Keep valid key-scoped candidates without pretending to know the active key.
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
            record.get("family"),
            record.get("family_source", "unknown"),
        )

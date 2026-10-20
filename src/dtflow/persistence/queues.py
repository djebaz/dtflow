"""Durable queue, item and control state stored beside the attempts in the single-owner SQLite database.

The dispatch owner is the process that holds the state directory's ``owner.lock`` through ``SQLiteAttempts``; every
queue mutation that dispatches work runs on that owned connection in a short ``BEGIN IMMEDIATE`` transaction and is never
held open while generating. Other processes never dispatch: they only append control requests with
``post_control``, add a new paused queue with ``insert_queue``, remove idle queue records or read with ``read_queues``
and ``read_queue_summaries``, each over their own short connection, and the owner polls the controls. A new paused
queue is a pure insert that no dispatcher reads until Start.

*Up next* (schema 4): a ``queued`` queue waits, in ``up_next`` order, for the live dispatch owner to finish its current
queue; the owner then claims and starts it on the same ownership (auto-play). The owner advertises itself with the
single ``queue_dispatch`` row; joining the order requires that row inside the same transaction, and the owner removes it
in the transaction that finds the order empty or releases it. Whenever a new owner opens the state, and whenever an
owner stops, every queued queue is restored paused, so nothing auto-starts after a restart or a failure.

An item is *reserved* the moment it is claimed (state ``running``), before its attempt exists or anything is
submitted. A reservation ends when the item settles. An ``unknown`` outcome is treated as an aborted request: its
reservation is released, it never blocks other work, and the attempt is never replayed automatically.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import (
    MAX_QUEUE_ITEMS,
    ResolvedGenerationRequest,
    ValidationError,
)
from dtflow.core.request_records import request_from_record as decode_request

QUEUE_STATES = ("paused", "queued", "running", "cancelled")
UP_NEXT_ACTIONS = ("enqueue", "dequeue", "cancel", "earlier", "later")
ITEM_STATES = ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")
ACTIONS = ("pause", "resume", "cancel", "cap")
MAX_ITEMS = MAX_QUEUE_ITEMS

QUEUE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS queues (
        queue_id TEXT PRIMARY KEY,
        backend_key TEXT NOT NULL,
        backend_json TEXT NOT NULL,
        state TEXT NOT NULL,
        cap INTEGER NOT NULL,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        up_next INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS queue_items (
        item_id TEXT PRIMARY KEY,
        queue_id TEXT NOT NULL REFERENCES queues(queue_id),
        position INTEGER NOT NULL,
        request_json TEXT NOT NULL,
        state TEXT NOT NULL,
        overridden INTEGER NOT NULL DEFAULT 0,
        attempt_id TEXT,
        failure_json TEXT,
        updated_at_utc TEXT NOT NULL,
        UNIQUE (queue_id, position)
    )""",
    "CREATE INDEX IF NOT EXISTS queue_items_by_state ON queue_items (queue_id, state)",
    """CREATE TABLE IF NOT EXISTS queue_controls (
        control_id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_id TEXT NOT NULL REFERENCES queues(queue_id),
        action TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at_utc TEXT NOT NULL,
        applied_at_utc TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS queue_dispatch (
        owner INTEGER PRIMARY KEY CHECK (owner = 1),
        started_at_utc TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS queue_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_id TEXT NOT NULL,
        at_utc TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}'
    )""",
)

# An attempt's final state decides its item's state; an item never invents an outcome the attempt does not have.
ATTEMPT_TO_ITEM = {
    "succeeded": "succeeded",
    "unknown": "unknown",
    "save_failed": "save_failed",
    "cancelled": "cancelled",
    "failed": "failed",
    "interrupted_before_submission": "failed",
}


def add_up_next(db: sqlite3.Connection) -> None:
    """Schema 4 migration step: the up-next order column on a queues table created by schema 2 or 3."""
    if "up_next" not in {row[1] for row in db.execute("PRAGMA table_info(queues)")}:
        db.execute("ALTER TABLE queues ADD COLUMN up_next INTEGER")


def check_queueable(request: ResolvedGenerationRequest) -> None:
    """Text-to-image, or a staged single-output edit that carries its recorded per-item owner-probe consent."""
    if request.operation == "txt2img":
        return
    evidence = dict(request.resolution_evidence)
    if (
        request.image_inputs is None
        or request.batch_size * request.batch_count != 1
        or evidence.get("image_permit_status") != "owner_probe"
        or not evidence.get("image_permit_evidence")
    ):
        raise ValidationError(
            "Queued image operations need staged inputs, one output and recorded owner-probe consent; "
            "nothing was dispatched."
        )


def request_from_record(record: dict[str, Any]) -> ResolvedGenerationRequest:
    """Rebuild an immutable request from its stored ``asdict`` form, revalidating every field."""
    request = decode_request(record)
    check_queueable(request)
    return request


def backend_key(backend: dict[str, Any]) -> str:
    """Accounting identity of one backend: reservations are shared by every queue using it."""
    return f"{backend['name']}:{backend.get('endpoint') or '-'}"


def _check_new_queue(requests: Sequence[ResolvedGenerationRequest]) -> None:
    if not requests or len(requests) > MAX_ITEMS:
        raise QueueError(f"A queue needs 1..{MAX_ITEMS} items.")
    if len({request.request_id for request in requests}) != len(requests):
        raise QueueError("Queue items need distinct request identities.")
    try:
        for request in requests:
            check_queueable(request)
    except ValidationError as exc:
        raise QueueError(str(exc)) from exc


def _insert_paused(db: sqlite3.Connection, backend, cap, requests, now: str, *, up_next: bool = False) -> str:
    """Insert one paused (or, behind a live owner, queued) queue with pending items in the caller's transaction."""
    queue_id = uuid4().hex
    db.execute(
        "INSERT INTO queues (queue_id, backend_key, backend_json, state, cap, created_at_utc, updated_at_utc, up_next)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            queue_id,
            backend_key(backend),
            json.dumps(backend),
            "queued" if up_next else "paused",
            cap,
            now,
            now,
            _last_up_next(db) + 1 if up_next else None,
        ),
    )
    db.executemany(
        "INSERT INTO queue_items (item_id, queue_id, position, request_json, state, updated_at_utc)"
        " VALUES (?,?,?,?,'pending',?)",
        [
            (uuid4().hex, queue_id, index, json.dumps(asdict(request), allow_nan=False), now)
            for index, request in enumerate(requests, 1)
        ],
    )
    QueueMixin._event(db, queue_id, "created", now, {"items": len(requests), "cap": cap})
    if up_next:
        QueueMixin._event(db, queue_id, "enqueued", now)
    return queue_id


def _last_up_next(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COALESCE(MAX(up_next), 0) FROM queues WHERE state='queued'").fetchone()[0]


class QueueError(ValidationError):
    """A queue request that cannot be honored; nothing was dispatched."""


class QueueMixin:
    """Queue operations on the owning ``SQLiteAttempts``: one connection, one worker thread."""

    # Provided by SQLiteAttempts.
    _run: Any
    _db: Any
    _now: Any

    @contextmanager
    def _txn(self):
        db = self._db()
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    @staticmethod
    def _event(db: sqlite3.Connection, queue_id: str, kind: str, now: str, detail: dict | None = None) -> None:
        db.execute(
            "INSERT INTO queue_events (queue_id, at_utc, kind, detail_json) VALUES (?,?,?,?)",
            (queue_id, now, kind, json.dumps(detail or {}, allow_nan=False)),
        )

    async def record_queue_event(
        self,
        queue_id: str,
        kind: str,
        detail: dict[str, Any] | None = None,
        *,
        at_utc: str | None = None,
    ) -> None:
        """Append sanitized diagnostic evidence without changing queue/item scheduling state."""
        await self._run(self._record_queue_event, queue_id, kind, detail, at_utc)

    def _record_queue_event(self, queue_id: str, kind: str, detail: dict[str, Any] | None, at_utc: str | None) -> None:
        with self._txn() as db:
            if db.execute("SELECT 1 FROM queues WHERE queue_id=?", (queue_id,)).fetchone() is None:
                raise QueueError("Unknown queue.")
            self._event(db, queue_id, kind, at_utc or self._now(), detail)

    # -- creation -------------------------------------------------------------------------------------------------

    async def create_queue(
        self, backend: dict[str, Any], cap: int, requests: Sequence[ResolvedGenerationRequest]
    ) -> str:
        return await self._run(self._create_queue, backend, cap, tuple(requests))

    def _create_queue(self, backend, cap, requests) -> str:
        _check_new_queue(requests)
        with self._txn() as db:
            return _insert_paused(db, backend, cap, requests, self._now())

    # -- controls -------------------------------------------------------------------------------------------------

    async def apply_controls(self, queue_id: str) -> tuple[str, ...]:
        """Apply posted control requests in order; returns the actions that took effect."""
        return await self._run(self._apply_controls, queue_id)

    def _apply_controls(self, queue_id: str) -> tuple[str, ...]:
        applied: list[str] = []
        with self._txn() as db:
            queue = db.execute("SELECT * FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if queue is None:
                raise QueueError("Unknown queue.")
            now = self._now()
            state = queue["state"]
            for control in db.execute(
                "SELECT * FROM queue_controls WHERE queue_id=? AND applied_at_utc IS NULL ORDER BY control_id",
                (queue_id,),
            ).fetchall():
                action, payload = control["action"], json.loads(control["payload_json"])
                if action == "pause" and state == "running":
                    state = "paused"
                elif action == "resume" and state == "paused":
                    state = "running"
                elif action == "cancel" and state != "cancelled":
                    state = "cancelled"
                    db.execute(
                        "UPDATE queue_items SET state='cancelled', updated_at_utc=? WHERE queue_id=? AND state='pending'",
                        (now, queue_id),
                    )
                elif action == "cap" and type(payload.get("cap")) is int and 1 <= payload["cap"] <= 4:
                    db.execute("UPDATE queues SET cap=? WHERE queue_id=?", (payload["cap"], queue_id))
                elif action == "override":
                    # A control posted by an older version: unknown outcomes no longer block, so consume it silently.
                    db.execute(
                        "UPDATE queue_controls SET applied_at_utc=? WHERE control_id=?", (now, control["control_id"])
                    )
                    continue
                else:
                    self._event(db, queue_id, "control_ignored", now, {"action": action, "state": state})
                    db.execute(
                        "UPDATE queue_controls SET applied_at_utc=? WHERE control_id=?", (now, control["control_id"])
                    )
                    continue
                applied.append(action)
                self._event(db, queue_id, action, now, payload if action == "cap" else {})
                db.execute(
                    "UPDATE queue_controls SET applied_at_utc=? WHERE control_id=?", (now, control["control_id"])
                )
            if state != queue["state"]:
                db.execute("UPDATE queues SET state=?, updated_at_utc=? WHERE queue_id=?", (state, now, queue_id))
        return tuple(applied)

    # -- dispatch -------------------------------------------------------------------------------------------------

    async def set_running(self, queue_id: str) -> None:
        """Explicit start: a paused queue becomes eligible; cancelled queues stay cancelled."""
        await self._run(self._set_running, queue_id)

    def _set_running(self, queue_id: str) -> None:
        with self._txn() as db:
            now = self._now()
            cursor = db.execute(
                "UPDATE queues SET state='running', updated_at_utc=? WHERE queue_id=? AND state='paused'",
                (now, queue_id),
            )
            if cursor.rowcount:
                self._event(db, queue_id, "started", now)
            elif db.execute("SELECT 1 FROM queues WHERE queue_id=?", (queue_id,)).fetchone() is None:
                raise QueueError("Unknown queue.")

    async def open_up_next(self) -> None:
        """Advertise this owner as live, so other connections may queue behind it until it drains or stops."""
        await self._run(self._open_up_next)

    def _open_up_next(self) -> None:
        with self._txn() as db:
            db.execute("INSERT OR REPLACE INTO queue_dispatch (owner, started_at_utc) VALUES (1, ?)", (self._now(),))

    async def claim_queued(self) -> str | None:
        """Auto-play: atomically start the first up-next queue, or close the order when none waits.

        Picking and starting happen in one transaction, so a Pause, Cancel or reorder made before it is respected, and
        a queue can never join after the owner found the order empty.
        """
        return await self._run(self._claim_queued)

    def _claim_queued(self) -> str | None:
        with self._txn() as db:
            row = db.execute(
                "SELECT queue_id FROM queues WHERE state='queued' ORDER BY up_next, created_at_utc LIMIT 1"
            ).fetchone()
            if row is None:
                db.execute("DELETE FROM queue_dispatch")
                return None
            now = self._now()
            db.execute(
                "UPDATE queues SET state='running', up_next=NULL, updated_at_utc=? WHERE queue_id=?",
                (now, row["queue_id"]),
            )
            self._event(db, row["queue_id"], "started", now, {"by": "up_next"})
            return row["queue_id"]

    async def release_queued(self, reason: str, current: str | None = None) -> int:
        """An owner that stops restores every queued queue paused (and ``current``, an auto-played queue it had not
        finished, when it is still running), so nothing starts without a live owner."""
        return await self._run(self._release_queued, reason, current)

    def _release_queued(self, reason: str, current: str | None) -> int:
        with self._txn() as db:
            now = self._now()
            if current is not None:
                db.execute(
                    "UPDATE queues SET state='paused', updated_at_utc=? WHERE queue_id=? AND state='running'",
                    (now, current),
                )
            return _release_queued(db, now, reason)

    async def claim_next(self, queue_id: str, limit: int) -> tuple[str, ResolvedGenerationRequest] | None:
        """Reserve the next eligible item, or None when paused, capped or exhausted."""
        return await self._run(self._claim_next, queue_id, limit)

    def _claim_next(self, queue_id: str, limit: int):
        with self._txn() as db:
            queue = db.execute("SELECT * FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if queue is None:
                raise QueueError("Unknown queue.")
            if queue["state"] != "running":
                return None
            if self._reserved(db, queue["backend_key"]) >= min(queue["cap"], limit):
                return None
            # Bound one claim transaction even if externally corrupted rows exceed
            # the normal queue creation limit. Never reserve an invalid request.
            for _ in range(MAX_ITEMS):
                row = db.execute(
                    "SELECT item_id, request_json, attempt_id FROM queue_items WHERE queue_id=? AND state='pending'"
                    " ORDER BY position LIMIT 1",
                    (queue_id,),
                ).fetchone()
                if row is None:
                    return None
                record = None
                try:
                    record = json.loads(row["request_json"])
                    request = request_from_record(record)
                except (TypeError, ValueError):
                    request_id = record.get("request_id") if isinstance(record, dict) else None
                    associated = (
                        db.execute("SELECT 1 FROM attempts WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
                        if isinstance(request_id, str)
                        else None
                    )
                    if row["attempt_id"] is not None or associated is not None:
                        raise QueueError(
                            "Invalid pending request has attempt evidence; owner inspection is required."
                        ) from None
                    now = self._now()
                    failure = {
                        "category": "validation",
                        "code": "stored_request_invalid",
                        "message": "Stored queue request is malformed or unsupported; not dispatched by this claim.",
                    }
                    # Retain the original request for inspection, without copying
                    # prompts, paths or decoder exception details into diagnostics.
                    db.execute(
                        "UPDATE queue_items SET state='failed', failure_json=?, updated_at_utc=? WHERE item_id=?",
                        (json.dumps(failure), now, row["item_id"]),
                    )
                    self._event(
                        db,
                        queue_id,
                        "request_rejected",
                        now,
                        {
                            "item_id": row["item_id"],
                            "code": "stored_request_invalid",
                        },
                    )
                    continue
                db.execute(
                    "UPDATE queue_items SET state='running', updated_at_utc=? WHERE item_id=?",
                    (self._now(), row["item_id"]),
                )
                return row["item_id"], request
            return None

    @staticmethod
    def _reserved(db: sqlite3.Connection, key: str) -> int:
        """Reservations in flight across queues sharing a backend. An ``unknown`` outcome holds none: it is treated as
        an aborted request that is never re-sent, and never blocks other work."""
        row = db.execute(
            """SELECT COALESCE(SUM(i.state='running'), 0) AS running
            FROM queue_items i JOIN queues q USING (queue_id) WHERE q.backend_key=?""",
            (key,),
        ).fetchone()
        return row["running"]

    async def settle_item(self, item_id: str, failure: dict[str, Any] | None = None) -> str:
        """Record an item's end state from its attempt; without an attempt it was definitely never submitted."""
        return await self._run(self._settle_item, item_id, failure)

    def _settle_item(self, item_id: str, failure) -> str:
        with self._txn() as db:
            item = db.execute("SELECT request_json FROM queue_items WHERE item_id=?", (item_id,)).fetchone()
            if item is None:
                raise QueueError("Unknown queue item.")
            request_id = json.loads(item["request_json"])["request_id"]
            attempt = db.execute(
                "SELECT attempt_id, state, failure_json FROM attempts WHERE request_id=?", (request_id,)
            ).fetchone()
            if attempt is None:
                state, attempt_id, failure_json = "failed", None, json.dumps(failure or {"code": "not_submitted"})
            else:
                state = ATTEMPT_TO_ITEM.get(attempt["state"], "unknown")
                attempt_id, failure_json = attempt["attempt_id"], attempt["failure_json"]
            db.execute(
                "UPDATE queue_items SET state=?, attempt_id=?, failure_json=?, updated_at_utc=? WHERE item_id=?",
                (state, attempt_id, failure_json, self._now(), item_id),
            )
        return state

    async def image_scopes(self, queue_id: str) -> tuple[tuple[str, str, str], ...]:
        """Distinct (checkpoint, operation, consent evidence id) of the queue's pending image items."""
        return await self._run(self._image_scopes, queue_id)

    def _image_scopes(self, queue_id: str) -> tuple[tuple[str, str, str], ...]:
        scopes: dict[tuple[str, str], str] = {}
        for row in self._db().execute(
            "SELECT request_json FROM queue_items WHERE queue_id=? AND state='pending' ORDER BY position", (queue_id,)
        ):
            try:
                record = json.loads(row["request_json"])
                if record.get("operation") in (None, "txt2img"):
                    continue
                evidence = {str(k): str(v) for k, v in record["resolution_evidence"]}
                scopes.setdefault((record["model_id"], record["operation"]), evidence["image_permit_evidence"])
            except (TypeError, ValueError, KeyError):
                continue  # claim_next rejects a malformed item without reserving it
        return tuple((model, operation, evidence) for (model, operation), evidence in scopes.items())

    async def inspect_queue(self, queue_id: str) -> dict[str, Any]:
        return await self._run(lambda: _read(self._db(), queue_id)[0])

    # -- restart reconciliation -----------------------------------------------------------------------------------

    @staticmethod
    def reconcile(db: sqlite3.Connection, now: str) -> None:
        """Run once by the new owner, inside the open transaction, after attempts were reconciled.

        Reserved items take their attempt's end state (never replayed). A reservation with no attempt crashed before
        anything could be submitted and returns to pending. Every live queue is restored paused.
        """
        for item in db.execute("SELECT item_id, queue_id, request_json FROM queue_items WHERE state='running'"):
            request_id = json.loads(item["request_json"])["request_id"]
            attempt = db.execute(
                "SELECT attempt_id, state, failure_json FROM attempts WHERE request_id=?", (request_id,)
            ).fetchone()
            if attempt is None:
                db.execute(
                    "UPDATE queue_items SET state='pending', updated_at_utc=? WHERE item_id=?", (now, item["item_id"])
                )
            else:
                db.execute(
                    "UPDATE queue_items SET state=?, attempt_id=?, failure_json=?, updated_at_utc=? WHERE item_id=?",
                    (
                        ATTEMPT_TO_ITEM.get(attempt["state"], "unknown"),
                        attempt["attempt_id"],
                        attempt["failure_json"],
                        now,
                        item["item_id"],
                    ),
                )
            QueueMixin._event(db, item["queue_id"], "reconciled", now, {"item_id": item["item_id"]})
        db.execute("UPDATE queues SET state='paused', updated_at_utc=? WHERE state='running'", (now,))
        _release_queued(db, now, "restart")


def _release_queued(db: sqlite3.Connection, now: str, reason: str) -> int:
    db.execute("DELETE FROM queue_dispatch")
    queue_ids = [row["queue_id"] for row in db.execute("SELECT queue_id FROM queues WHERE state='queued'")]
    for queue_id in queue_ids:
        db.execute("UPDATE queues SET state='paused', up_next=NULL, updated_at_utc=? WHERE queue_id=?", (now, queue_id))
        QueueMixin._event(db, queue_id, "up_next_released", now, {"reason": reason})
    return len(queue_ids)


# -- access without ownership (control and read paths) ----------------------------------------------------------


def _shared_connection(directory: Path, *, readonly: bool = False) -> sqlite3.Connection:
    from dtflow.persistence.store import SCHEMA_VERSION, PersistenceError, patched_sqlite

    if not patched_sqlite(sqlite3.sqlite_version):
        raise PersistenceError("SQLite needs an official WAL-reset fix (3.51.3+ or a supported backport).")
    path = Path(directory) / "attempts.sqlite3"
    if not path.is_file():
        raise QueueError("No execution state exists in this directory; create a queue first.")
    connection = (
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
        if readonly
        else sqlite3.connect(path, timeout=2.0)
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=2000")
    supported = (2, 3, SCHEMA_VERSION) if readonly else (SCHEMA_VERSION,)
    if connection.execute("PRAGMA user_version").fetchone()[0] not in supported:
        connection.close()
        raise QueueError(
            "Execution state has no queue schema yet or is unsupported; run a queue command to prepare it."
        )
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    return connection


def post_control(directory: Path, queue_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    """Append one control request for the dispatcher to poll; safe while another process owns dispatch.

    A queue waiting up next holds no reservation, so Pause (leave the order) and Cancel apply to it at once instead.
    """
    if action not in ACTIONS:
        raise QueueError("Unknown queue action.")
    payload = payload or {}
    if action == "cap" and not (type(payload.get("cap")) is int and 1 <= payload["cap"] <= 4):
        raise QueueError("Queue concurrency must be an integer in 1..4.")
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT state FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if row is None:
                raise QueueError("Unknown queue.")
            if row["state"] == "queued" and action in ("pause", "cancel"):
                _leave_up_next(connection, queue_id, datetime.now(UTC).isoformat(), cancel=action == "cancel")
                connection.execute("COMMIT")
                return
            connection.execute(
                "INSERT INTO queue_controls (queue_id, action, payload_json, created_at_utc) VALUES (?,?,?,datetime('now'))",
                (queue_id, action, json.dumps(payload)),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()


def insert_queue(
    directory: Path,
    backend: dict[str, Any],
    cap: int,
    requests: Sequence[ResolvedGenerationRequest],
    *,
    up_next: bool = False,
) -> str:
    """Add one queue while another owner holds the state (a running dispatch or generation).

    It stays paused until an explicit Start; with ``up_next`` it is queued last behind a live dispatch owner, which
    starts it after its current queue (and restores it paused if it stops first). Without a live owner it is saved
    paused: read its status to tell the user which happened.
    """
    requests = tuple(requests)
    _check_new_queue(requests)
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            live = up_next and _dispatch_live(connection)
            queue_id = _insert_paused(connection, backend, cap, requests, datetime.now(UTC).isoformat(), up_next=live)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return queue_id


def change_up_next(directory: Path, queue_id: str, action: str) -> None:
    """Enqueue a paused queue last, return a queued one to paused, or move it one place earlier or later.

    Safe beside a live dispatch owner: it only reorders queues that owner has not started yet.
    """
    if action not in UP_NEXT_ACTIONS:
        raise QueueError("Unknown up-next action.")
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT state, up_next FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if row is None:
                raise QueueError("Unknown queue.")
            now = datetime.now(UTC).isoformat()
            if action == "enqueue":
                pending = connection.execute(
                    "SELECT 1 FROM queue_items WHERE queue_id=? AND state='pending' LIMIT 1", (queue_id,)
                ).fetchone()
                if row["state"] != "paused" or pending is None:
                    raise QueueError("Only a paused queue with pending items can wait up next.")
                if not _dispatch_live(connection):
                    raise QueueError("No queue is dispatching any more, so nothing would start it. Press Start.")
                connection.execute(
                    "UPDATE queues SET state='queued', up_next=?, updated_at_utc=? WHERE queue_id=?",
                    (_last_up_next(connection) + 1, now, queue_id),
                )
                QueueMixin._event(connection, queue_id, "enqueued", now)
            elif row["state"] != "queued":
                raise QueueError("This queue is not waiting up next.")
            elif action in ("dequeue", "cancel"):
                _leave_up_next(connection, queue_id, now, cancel=action == "cancel")
            else:
                earlier = action == "earlier"
                neighbour = connection.execute(
                    "SELECT queue_id, up_next FROM queues WHERE state='queued' AND up_next "
                    + ("< ? ORDER BY up_next DESC" if earlier else "> ? ORDER BY up_next")
                    + " LIMIT 1",
                    (row["up_next"],),
                ).fetchone()
                if neighbour is not None:
                    connection.execute("UPDATE queues SET up_next=? WHERE queue_id=?", (neighbour["up_next"], queue_id))
                    connection.execute(
                        "UPDATE queues SET up_next=? WHERE queue_id=?", (row["up_next"], neighbour["queue_id"])
                    )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()


def _dispatch_live(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM queue_dispatch").fetchone() is not None


def _leave_up_next(db: sqlite3.Connection, queue_id: str, now: str, *, cancel: bool) -> None:
    """Take a waiting queue out of the order: paused, or cancelled with its pending items (it holds no reservation)."""
    db.execute(
        "UPDATE queues SET state=?, up_next=NULL, updated_at_utc=? WHERE queue_id=?",
        ("cancelled" if cancel else "paused", now, queue_id),
    )
    if cancel:
        db.execute(
            "UPDATE queue_items SET state='cancelled', updated_at_utc=? WHERE queue_id=? AND state='pending'",
            (now, queue_id),
        )
    QueueMixin._event(
        db, queue_id, "cancel" if cancel else "up_next_released", now, {} if cancel else {"reason": "user"}
    )


def _removable(connection: sqlite3.Connection, where: str, params: Sequence[Any], keep: Sequence[str]) -> list[str]:
    """Queues matching ``where`` that no dispatcher can be working on: not running or waiting up next with work left,
    no running reservation, and not named in ``keep`` (the caller's own dispatch)."""
    return [
        row["queue_id"]
        for row in connection.execute(
            f"""SELECT q.queue_id FROM queues q
            WHERE ({where})
                AND (q.state IN ('paused','cancelled') OR NOT EXISTS (
                    SELECT 1 FROM queue_items i WHERE i.queue_id=q.queue_id AND i.state IN ('pending','running')))
                AND NOT EXISTS (SELECT 1 FROM queue_items i WHERE i.queue_id=q.queue_id AND i.state='running')""",
            tuple(params),
        )
        if row["queue_id"] not in keep
    ]


def _delete(connection: sqlite3.Connection, queue_ids: Sequence[str]) -> None:
    for table in ("queue_controls", "queue_events", "queue_items", "queues"):
        connection.executemany(f"DELETE FROM {table} WHERE queue_id=?", [(queue_id,) for queue_id in queue_ids])


def delete_idle_queues(directory: Path, keep: Sequence[str] = ()) -> int:
    """Clear history: delete finished and cancelled queues with their items, controls and events; return how many went.

    Paused queues are kept (they still have work to start), and so is a queue that is running, waiting up next or
    holds a running reservation. Attempts and saved outputs are never touched. ``keep`` names queues the caller is
    dispatching.
    """
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            queue_ids = _removable(
                connection,
                "q.state='cancelled' OR NOT EXISTS (SELECT 1 FROM queue_items i WHERE i.queue_id=q.queue_id "
                "AND i.state IN ('pending','running'))",
                (),
                keep,
            )
            _delete(connection, queue_ids)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return len(queue_ids)


def delete_queue(directory: Path, queue_id: str, keep: Sequence[str] = ()) -> None:
    """Remove one queue record (a paused, finished or cancelled one) with its items, controls and events.

    Attempts and saved outputs are never touched. A queue that is running, waiting up next, holds a running
    reservation or is named in ``keep`` is refused, and nothing changes.
    """
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if connection.execute("SELECT 1 FROM queues WHERE queue_id=?", (queue_id,)).fetchone() is None:
                raise QueueError("Unknown queue.")
            if not _removable(connection, "q.queue_id=? AND q.state != 'queued'", (queue_id,), keep):
                raise QueueError("A running or up-next queue cannot be removed; cancel or pause it first.")
            _delete(connection, (queue_id,))
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {state: 0 for state in ITEM_STATES}
    for item in items:
        counts[item["state"]] += 1
    return counts


def _status(state: str, summary: dict[str, int]) -> str:
    if state == "cancelled":
        return "cancelled"
    if not (summary["pending"] or summary["running"]):
        return "finished"
    return state


# What a queue's items can differ in, read straight from each stored request (``asdict`` field names).
_VARIED = {
    "prompt": "json_extract(request_json, '$.prompt')",
    "model": "json_extract(request_json, '$.model_id')",
    "seed": "json_extract(request_json, '$.seed')",
    "steps": "json_extract(request_json, '$.steps')",
    "guidance": "json_extract(request_json, '$.guidance')",
    "sampler": "json_extract(request_json, '$.sampler_id')",
    "size": "json_extract(request_json, '$.width_px') || 'x' || json_extract(request_json, '$.height_px')",
    "strength": "json_extract(request_json, '$.strength')",
    "loras": "json_extract(request_json, '$.loras')",
}


def read_queue_summaries(directory: Path, *, described: Sequence[str] = ()) -> tuple[dict[str, Any], ...]:
    """List every queue without its items or events: status, item counts and what the first item asks for.

    Counts come from the state index and the description from the first item only, so a poll stays light however
    large the queues are. ``varied`` names what the items differ in (with how many distinct values); items never
    change after creation, so queues in ``described`` skip that scan and report ``varied`` as None.
    """
    connection = _shared_connection(directory, readonly=True)
    try:
        counts: dict[str, dict[str, int]] = {}
        for row in connection.execute(
            "SELECT queue_id, state, COUNT(*) AS n FROM queue_items GROUP BY queue_id, state"
        ):
            counts.setdefault(row["queue_id"], {state: 0 for state in ITEM_STATES})[row["state"]] = row["n"]
        queues = []
        for row in connection.execute("SELECT * FROM queues ORDER BY created_at_utc, queue_id").fetchall():
            queue = {key: row[key] for key in row.keys() if key not in ("backend_json",)}
            queue["backend"] = json.loads(row["backend_json"])
            queue.setdefault("up_next", None)  # a schema 2 or 3 database read without migrating
            summary = counts.get(row["queue_id"], {state: 0 for state in ITEM_STATES})
            queue["summary"] = summary
            queue["status"] = _status(row["state"], summary)
            first = connection.execute(
                "SELECT request_json FROM queue_items WHERE queue_id=? ORDER BY position LIMIT 1", (row["queue_id"],)
            ).fetchone()
            try:
                request = json.loads(first["request_json"]) if first else {}
            except (TypeError, ValueError):
                request = {}
            request = request if isinstance(request, dict) else {}
            queue["first"] = {
                "prompt": request.get("prompt") if isinstance(request.get("prompt"), str) else "",
                "model": request.get("model_id") if isinstance(request.get("model_id"), str) else "",
                "operation": request.get("operation") or "txt2img",
            }
            queue["varied"] = None
            if row["queue_id"] not in described:
                columns = ", ".join(
                    f"COUNT(DISTINCT CASE WHEN json_valid(request_json) THEN {expression} END) AS {name}"
                    for name, expression in _VARIED.items()
                )
                distinct = connection.execute(
                    f"SELECT {columns} FROM queue_items WHERE queue_id=?", (row["queue_id"],)
                ).fetchone()
                queue["varied"] = {name: distinct[name] for name in _VARIED if distinct[name] > 1}
            queues.append(queue)
        return tuple(queues)
    finally:
        connection.close()


def read_queues(directory: Path, queue_id: str | None = None) -> tuple[dict[str, Any], ...]:
    """Inspect queues and items in their original order without owning or modifying state."""
    connection = _shared_connection(directory, readonly=True)
    try:
        return _read(connection, queue_id)
    finally:
        connection.close()


def _read(connection: sqlite3.Connection, queue_id: str | None) -> tuple[dict[str, Any], ...]:
    queues = []
    rows = (
        connection.execute("SELECT * FROM queues WHERE queue_id=?", (queue_id,))
        if queue_id
        else connection.execute("SELECT * FROM queues ORDER BY created_at_utc, queue_id")
    ).fetchall()
    if queue_id and not rows:
        raise QueueError("Unknown queue.")
    for row in rows:
        queue = {key: row[key] for key in row.keys() if key not in ("backend_json",)}
        queue["backend"] = json.loads(row["backend_json"])
        queue.setdefault("up_next", None)  # a schema 2 or 3 database read without migrating
        items = []
        for item in connection.execute(
            """SELECT i.item_id, i.position, i.state, i.attempt_id, i.failure_json, i.request_json,
                a.state AS attempt_state, a.artifacts_json
            FROM queue_items i LEFT JOIN attempts a ON a.attempt_id = i.attempt_id
            WHERE i.queue_id=? ORDER BY i.position""",
            (row["queue_id"],),
        ):
            try:
                request = json.loads(item["request_json"])
            except (TypeError, ValueError):
                request = {}
            if not isinstance(request, dict):
                request = {}
            items.append(
                {
                    "position": item["position"],
                    "item_id": item["item_id"],
                    "state": item["state"],
                    "attempt_id": item["attempt_id"],
                    "request_id": request.get("request_id"),
                    "seed": request.get("seed"),
                    "operation": request.get("operation") or "txt2img",
                    "strength": request.get("strength"),
                    "failure": json.loads(item["failure_json"]) if item["failure_json"] else None,
                    "artifacts": json.loads(item["artifacts_json"]) if item["artifacts_json"] else [],
                }
            )
        queue["summary"] = _summary(items)
        queue["status"] = _status(row["state"], queue["summary"])
        queue["items"] = items
        queue["pending_controls"] = [
            c["action"]
            for c in connection.execute(
                "SELECT action FROM queue_controls WHERE queue_id=? AND applied_at_utc IS NULL ORDER BY control_id",
                (row["queue_id"],),
            )
        ]
        if queue_id:
            queue["events"] = [
                {"at_utc": e["at_utc"], "kind": e["kind"], "detail": json.loads(e["detail_json"])}
                for e in connection.execute(
                    "SELECT at_utc, kind, detail_json FROM queue_events WHERE queue_id=? ORDER BY event_id",
                    (queue_id,),
                )
            ]
        queues.append(queue)
    return tuple(queues)

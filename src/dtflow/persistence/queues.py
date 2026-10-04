"""Durable queue, item and control state stored beside the attempts in the single-owner SQLite database.

The dispatch owner is the process that holds the state directory's ``owner.lock`` through ``SQLiteAttempts``; every
queue mutation that dispatches work runs on that owned connection in a short ``BEGIN IMMEDIATE`` transaction and is never
held open while generating. Other processes never dispatch: they only append control requests with
``post_control`` or read with ``read_queues``, both over their own short connection, and the owner polls the controls.

An item is *reserved* the moment it is claimed (state ``running``), before its attempt exists or anything is
submitted. A reservation ends only with a known outcome; ``unknown`` items stay reserved and block their backend until an
explicit override, which is recorded and never resolves or replays the attempt.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import LoRASelection, ResolvedGenerationRequest, Sampler, ValidationError

QUEUE_STATES = ("paused", "running", "cancelled")
ITEM_STATES = ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")
ACTIONS = ("pause", "resume", "cancel", "override", "cap")
MAX_ITEMS = 1000

QUEUE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS queues (
        queue_id TEXT PRIMARY KEY,
        backend_key TEXT NOT NULL,
        backend_json TEXT NOT NULL,
        state TEXT NOT NULL,
        cap INTEGER NOT NULL,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL
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


def request_from_record(record: dict[str, Any]) -> ResolvedGenerationRequest:
    """Rebuild an immutable request from its stored ``asdict`` form, revalidating every field."""
    try:
        return ResolvedGenerationRequest(
            **{
                **record,
                "sampler_id": Sampler(record["sampler_id"]),
                "loras": tuple(LoRASelection(**item) for item in record["loras"]),
                "resolution_evidence": tuple(tuple(pair) for pair in record["resolution_evidence"]),
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("A stored queue request is malformed.") from exc


def backend_key(backend: dict[str, Any]) -> str:
    """Accounting identity of one backend: reservations and uncertainty are shared by every queue using it."""
    return f"{backend['name']}:{backend.get('endpoint') or '-'}"


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

    # -- creation -------------------------------------------------------------------------------------------------

    async def create_queue(
        self, backend: dict[str, Any], cap: int, requests: Sequence[ResolvedGenerationRequest]
    ) -> str:
        return await self._run(self._create_queue, backend, cap, tuple(requests))

    def _create_queue(self, backend, cap, requests) -> str:
        if not requests or len(requests) > MAX_ITEMS:
            raise QueueError(f"A queue needs 1..{MAX_ITEMS} items.")
        if len({request.request_id for request in requests}) != len(requests):
            raise QueueError("Queue items need distinct request identities.")
        queue_id, now = uuid4().hex, self._now()
        with self._txn() as db:
            db.execute(
                "INSERT INTO queues VALUES (?,?,?,?,?,?,?)",
                (queue_id, backend_key(backend), json.dumps(backend), "paused", cap, now, now),
            )
            db.executemany(
                "INSERT INTO queue_items (item_id, queue_id, position, request_json, state, updated_at_utc)"
                " VALUES (?,?,?,?,'pending',?)",
                [
                    (uuid4().hex, queue_id, index, json.dumps(asdict(request), allow_nan=False), now)
                    for index, request in enumerate(requests, 1)
                ],
            )
            self._event(db, queue_id, "created", now, {"items": len(requests), "cap": cap})
        return queue_id

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
                    self._override(db, queue["backend_key"], queue_id, now)
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

    @staticmethod
    def _override(db: sqlite3.Connection, key: str, queue_id: str, now: str) -> None:
        """Acknowledge unresolved unknown reservations on this backend; the attempts stay unknown and unreplayed."""
        rows = db.execute(
            """SELECT i.item_id, i.queue_id, i.attempt_id FROM queue_items i JOIN queues q USING (queue_id)
            WHERE q.backend_key=? AND i.state='unknown' AND i.overridden=0""",
            (key,),
        ).fetchall()
        for row in rows:
            db.execute("UPDATE queue_items SET overridden=1, updated_at_utc=? WHERE item_id=?", (now, row["item_id"]))
            QueueMixin._event(
                db,
                row["queue_id"],
                "override_acknowledged",
                now,
                {"item_id": row["item_id"], "attempt_id": row["attempt_id"], "invoked_on": queue_id},
            )

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

    async def claim_next(self, queue_id: str, limit: int) -> tuple[str, ResolvedGenerationRequest] | None:
        """Reserve the next eligible item, or None when paused, blocked, capped or exhausted."""
        return await self._run(self._claim_next, queue_id, limit)

    def _claim_next(self, queue_id: str, limit: int):
        with self._txn() as db:
            queue = db.execute("SELECT * FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if queue is None:
                raise QueueError("Unknown queue.")
            if queue["state"] != "running":
                return None
            blocked, reserved = self._accounting(db, queue["backend_key"])
            if blocked or reserved >= min(queue["cap"], limit):
                return None
            row = db.execute(
                "SELECT item_id, request_json FROM queue_items WHERE queue_id=? AND state='pending'"
                " ORDER BY position LIMIT 1",
                (queue_id,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE queue_items SET state='running', updated_at_utc=? WHERE item_id=?",
                (self._now(), row["item_id"]),
            )
            return row["item_id"], request_from_record(json.loads(row["request_json"]))

    @staticmethod
    def _accounting(db: sqlite3.Connection, key: str) -> tuple[bool, int]:
        """(blocked by unacknowledged uncertainty, reservations in flight) across queues sharing a backend."""
        row = db.execute(
            """SELECT
                COALESCE(SUM(i.state='unknown' AND i.overridden=0), 0) AS uncertain,
                COALESCE(SUM(i.state='running'), 0) AS running
            FROM queue_items i JOIN queues q USING (queue_id) WHERE q.backend_key=?""",
            (key,),
        ).fetchone()
        return bool(row["uncertain"]), row["running"]

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


# -- access without ownership (control and read paths) ----------------------------------------------------------


def _shared_connection(directory: Path) -> sqlite3.Connection:
    from dtflow.persistence.store import SCHEMA_VERSION, PersistenceError, patched_sqlite

    if not patched_sqlite(sqlite3.sqlite_version):
        raise PersistenceError("SQLite needs an official WAL-reset fix (3.51.3+ or a supported backport).")
    path = Path(directory) / "attempts.sqlite3"
    if not path.is_file():
        raise QueueError("No execution state exists in this directory; create a queue first.")
    connection = sqlite3.connect(path, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=2000")
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        connection.close()
        raise QueueError(
            "Execution state has no queue schema yet or is unsupported; run a queue command to prepare it."
        )
    return connection


def post_control(directory: Path, queue_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
    """Append one control request for the dispatcher to poll; safe while another process owns dispatch."""
    if action not in ACTIONS:
        raise QueueError("Unknown queue action.")
    payload = payload or {}
    if action == "cap" and not (type(payload.get("cap")) is int and 1 <= payload["cap"] <= 4):
        raise QueueError("Queue concurrency must be an integer in 1..4.")
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if connection.execute("SELECT 1 FROM queues WHERE queue_id=?", (queue_id,)).fetchone() is None:
                raise QueueError("Unknown queue.")
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


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {state: 0 for state in ITEM_STATES}
    for item in items:
        counts[item["state"]] += 1
    return counts


def read_queues(directory: Path, queue_id: str | None = None) -> tuple[dict[str, Any], ...]:
    """Inspect queues and items in their original order without owning or modifying state."""
    connection = _shared_connection(directory)
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
        items = []
        for item in connection.execute(
            """SELECT i.item_id, i.position, i.state, i.overridden, i.attempt_id, i.failure_json, i.request_json,
                a.state AS attempt_state, a.artifacts_json
            FROM queue_items i LEFT JOIN attempts a ON a.attempt_id = i.attempt_id
            WHERE i.queue_id=? ORDER BY i.position""",
            (row["queue_id"],),
        ):
            request = json.loads(item["request_json"])
            items.append(
                {
                    "position": item["position"],
                    "item_id": item["item_id"],
                    "state": item["state"],
                    "overridden": bool(item["overridden"]),
                    "attempt_id": item["attempt_id"],
                    "request_id": request["request_id"],
                    "seed": request["seed"],
                    "failure": json.loads(item["failure_json"]) if item["failure_json"] else None,
                    "artifacts": json.loads(item["artifacts_json"]) if item["artifacts_json"] else [],
                }
            )
        queue["summary"] = _summary(items)
        # Uncertainty on any queue using the same backend blocks this one too.
        queue["blocked_by_uncertainty"] = QueueMixin._accounting(connection, row["backend_key"])[0]
        summary = queue["summary"]
        if row["state"] == "cancelled":
            queue["status"] = "cancelled"
        elif not (summary["pending"] or summary["running"]):
            queue["status"] = "finished"
        elif queue["blocked_by_uncertainty"]:
            queue["status"] = "blocked"
        else:
            queue["status"] = row["state"]
        queue["items"] = items
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

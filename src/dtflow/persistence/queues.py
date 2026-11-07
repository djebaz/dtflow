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
import threading
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import (
    MAX_QUEUE_CONCURRENCY,
    MAX_QUEUE_ITEMS,
    ResolvedGenerationRequest,
    ValidationError,
)
from dtflow.core.request_records import request_from_record as decode_request
from dtflow.persistence.script_runs import read_open_prompt
from dtflow.persistence.script_items import (
    KIND_SCRIPT,
    TERMINAL_ATTEMPTS,
    child_states,
    retry_of,
    is_script_record,
    item_outcome,
    read_run_snapshot,
    script_request_from_record,
)
from dtflow.persistence.workflow_items import (
    KIND_WORKFLOW,
    interrupted_run,
    is_workflow_record,
    workflow_request_from_record,
)
from dtflow.persistence.workflow_items import item_outcome as workflow_outcome

QUEUE_STATES = ("paused", "queued", "running", "cancelled")
UP_NEXT_ACTIONS = ("enqueue", "dequeue", "cancel", "earlier", "later")
ITEM_STATES = ("pending", "running", "succeeded", "failed", "unknown", "save_failed", "cancelled")
ACTIONS = ("pause", "resume", "cancel", "cap")
# Workflow queues only (#223): answered by the workflow runner, which takes them with their payloads
# (``take_workflow_controls``); ``apply_controls`` leaves them pending for it.
WORKFLOW_ACTIONS = ("workflow_branch", "workflow_retry")
BRANCH_DECISIONS = ("continue", "stop")
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
        up_next INTEGER,
        kind TEXT NOT NULL DEFAULT 'generation'
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
    "CREATE INDEX IF NOT EXISTS queue_events_by_queue_kind ON queue_events (queue_id, kind)",
    """CREATE TABLE IF NOT EXISTS script_child_slots (
        attempt_id TEXT PRIMARY KEY,
        queue_id TEXT NOT NULL REFERENCES queues(queue_id)
    )""",
    "CREATE INDEX IF NOT EXISTS script_child_slots_by_queue ON script_child_slots (queue_id)",
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


def add_kind(db: sqlite3.Connection) -> None:
    """Schema 5 migration step: the queue kind (``generation`` or ``script``) on a queues table from schema 2 to 4."""
    if "kind" not in {row[1] for row in db.execute("PRAGMA table_info(queues)")}:
        db.execute("ALTER TABLE queues ADD COLUMN kind TEXT NOT NULL DEFAULT 'generation'")


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


def _check_script_record(record: Any) -> None:
    try:
        script_request_from_record(record)
    except ValidationError as exc:
        raise QueueError(str(exc)) from exc


def _check_workflow_record(record: Any) -> None:
    try:
        workflow_request_from_record(record)
    except ValidationError as exc:
        raise QueueError(str(exc)) from exc


def _insert_paused(
    db: sqlite3.Connection,
    backend,
    cap,
    requests,
    now: str,
    *,
    up_next: bool = False,
    composer_source=None,
    kind: str = "generation",
    records: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Insert one paused (or, behind a live owner, queued) queue with pending items in the caller's transaction."""
    queue_id = uuid4().hex
    db.execute(
        "INSERT INTO queues (queue_id, backend_key, backend_json, state, cap, created_at_utc, updated_at_utc, up_next,"
        " kind) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            queue_id,
            backend_key(backend),
            json.dumps(backend),
            "queued" if up_next else "paused",
            cap,
            now,
            now,
            _last_up_next(db) + 1 if up_next else None,
            kind,
        ),
    )
    stored = (
        [json.dumps(record, allow_nan=False) for record in records]
        if records is not None
        else [json.dumps(asdict(request), allow_nan=False) for request in requests]
    )
    db.executemany(
        "INSERT INTO queue_items (item_id, queue_id, position, request_json, state, updated_at_utc)"
        " VALUES (?,?,?,?,'pending',?)",
        [(uuid4().hex, queue_id, index, request_json, now) for index, request_json in enumerate(stored, 1)],
    )
    QueueMixin._event(db, queue_id, "created", now, {"items": len(stored), "cap": cap})
    if composer_source is not None:
        if not isinstance(composer_source, dict):
            raise QueueError("Composer source must be an object.")
        encoded = json.dumps(composer_source, allow_nan=False)
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise QueueError("Composer source exceeds the 1 MiB limit; nothing was created.")
        # Queue-only evidence, committed atomically with its items. Never copied into image metadata or logs.
        QueueMixin._event(db, queue_id, "composer_source", now, composer_source)
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

    async def queue_timing(self, queue_id: str) -> dict[str, Any]:
        """The few rows a timing snapshot needs, without reading every item and event of the queue."""
        return await self._run(self._queue_timing, queue_id)

    def _queue_timing(self, queue_id: str) -> dict[str, Any]:
        db = self._db()
        queue = db.execute("SELECT cap FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
        if queue is None:
            raise QueueError("Unknown queue.")
        started = db.execute(
            "SELECT at_utc FROM queue_events WHERE queue_id=? AND kind='started' ORDER BY event_id DESC LIMIT 1",
            (queue_id,),
        ).fetchone()
        counts = {state: 0 for state in ITEM_STATES}
        for row in db.execute(
            "SELECT state, COUNT(*) AS n FROM queue_items WHERE queue_id=? GROUP BY state", (queue_id,)
        ):
            counts[row["state"]] = row["n"]
        return {
            "cap": queue["cap"],
            "started_at_utc": started["at_utc"] if started else None,
            "summary": counts,
            "running_started_at_utc": [
                row["updated_at_utc"]
                for row in db.execute(
                    "SELECT updated_at_utc FROM queue_items WHERE queue_id=? AND state='running' ORDER BY item_id",
                    (queue_id,),
                )
            ],
            "successful_durations": _successful_durations(db, self.directory, queue_id),
        }

    # -- creation -------------------------------------------------------------------------------------------------

    async def create_queue(
        self, backend: dict[str, Any], cap: int, requests: Sequence[ResolvedGenerationRequest], *, composer_source=None
    ) -> str:
        return await self._run(self._create_queue, backend, cap, tuple(requests), composer_source)

    def _create_queue(self, backend, cap, requests, composer_source=None) -> str:
        _check_new_queue(requests)
        with self._txn() as db:
            return _insert_paused(db, backend, cap, requests, self._now(), composer_source=composer_source)

    async def create_script_queue(self, backend: dict[str, Any], record: dict[str, Any]) -> str:
        return await self._run(self._create_script_queue, backend, record)

    def _create_script_queue(self, backend, record) -> str:
        _check_script_record(record)
        with self._txn() as db:
            return _insert_paused(db, backend, 1, (), self._now(), kind=KIND_SCRIPT, records=(record,))

    async def create_workflow_queue(self, backend: dict[str, Any], record: dict[str, Any]) -> str:
        return await self._run(self._create_workflow_queue, backend, record)

    def _create_workflow_queue(self, backend, record) -> str:
        _check_workflow_record(record)
        with self._txn() as db:
            return _insert_paused(db, backend, 1, (), self._now(), kind=KIND_WORKFLOW, records=(record,))

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
                if action in WORKFLOW_ACTIONS and queue["kind"] == KIND_WORKFLOW:
                    continue  # left pending for the workflow runner (take_workflow_controls)
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
                elif (
                    action == "cap" and type(payload.get("cap")) is int and 1 <= payload["cap"] <= MAX_QUEUE_CONCURRENCY
                ):
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

    async def take_workflow_controls(self, queue_id: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Consume a workflow queue's pending branch decisions and Retry requests, in order, with their payloads."""
        return await self._run(self._take_workflow_controls, queue_id)

    def _take_workflow_controls(self, queue_id: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        taken: list[tuple[str, dict[str, Any]]] = []
        marks = ",".join("?" * len(WORKFLOW_ACTIONS))
        with self._txn() as db:
            now = self._now()
            for control in db.execute(
                f"SELECT * FROM queue_controls WHERE queue_id=? AND applied_at_utc IS NULL AND action IN ({marks})"
                " ORDER BY control_id",
                (queue_id, *WORKFLOW_ACTIONS),
            ).fetchall():
                payload = json.loads(control["payload_json"])
                taken.append((control["action"], payload))
                self._event(db, queue_id, control["action"], now, payload)
                db.execute(
                    "UPDATE queue_controls SET applied_at_utc=? WHERE control_id=?", (now, control["control_id"])
                )
        return tuple(taken)

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

    async def overlap_ready(self, tail_id: str) -> bool:
        """Cheap read-only look: has ``tail_id`` handed out its last item while a same-backend queue waits?"""
        return await self._run(self._overlap_ready, tail_id)

    def _overlap_ready(self, tail_id: str) -> bool:
        db = self._db()
        tail = db.execute(
            "SELECT backend_key, state, (SELECT COUNT(*) FROM queue_items WHERE queue_id=q.queue_id"
            " AND state='pending') AS pending FROM queues q WHERE queue_id=?",
            (tail_id,),
        ).fetchone()
        row = db.execute(
            "SELECT backend_key FROM queues WHERE state='queued' ORDER BY up_next, created_at_utc LIMIT 1"
        ).fetchone()
        return bool(
            tail is not None
            and row is not None
            and tail["state"] == "running"
            and not tail["pending"]
            and tail["backend_key"] == row["backend_key"]
        )

    async def claim_queued(self, overlap_after: str | None = None, limit: int | None = None) -> str | None:
        """Auto-play: atomically start the first up-next queue, or close the order when none waits.

        Picking and starting happen in one transaction, so a Pause, Cancel or reorder made before it is respected, and
        a queue can never join after the owner found the order empty.

        With ``overlap_after`` (a running queue whose items are all claimed) the first waiting queue starts early, only
        while a slot is free on their shared backend (reservations below ``min(cap, limit)``, ``limit`` being the
        backend's own maximum); otherwise nothing changes and the order stays open.
        """
        return await self._run(self._claim_queued, overlap_after, limit)

    def _claim_queued(self, overlap_after: str | None = None, limit: int | None = None) -> str | None:
        with self._txn() as db:
            row = db.execute(
                "SELECT queue_id, backend_key, cap FROM queues WHERE state='queued' ORDER BY up_next, created_at_utc"
                " LIMIT 1"
            ).fetchone()
            if overlap_after is None:
                if row is None:
                    db.execute("DELETE FROM queue_dispatch")
                    return None
            else:
                if row is None:
                    return None
                tail = db.execute(
                    "SELECT backend_key, state, (SELECT COUNT(*) FROM queue_items WHERE queue_id=q.queue_id"
                    " AND state='pending') AS pending FROM queues q WHERE queue_id=?",
                    (overlap_after,),
                ).fetchone()
                if (
                    tail is None
                    or tail["state"] != "running"
                    or tail["pending"]
                    or tail["backend_key"] != row["backend_key"]
                    or limit is None
                    or self._reserved(db, row["backend_key"]) >= min(row["cap"], limit)
                ):
                    return None
            now = self._now()
            db.execute(
                "UPDATE queues SET state='running', up_next=NULL, updated_at_utc=? WHERE queue_id=?",
                (now, row["queue_id"]),
            )
            detail = {"by": "up_next"} if overlap_after is None else {"by": "up_next", "overlap_after": overlap_after}
            self._event(db, row["queue_id"], "started", now, detail)
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

    async def claim_next(self, queue_id: str, limit: int) -> tuple[str, Any] | None:
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
            decode = {KIND_SCRIPT: script_request_from_record, KIND_WORKFLOW: workflow_request_from_record}.get(
                queue["kind"], request_from_record
            )
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
                    request = decode(record)
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
        """Shared reservations: ordinary queue items plus each running script's active child slots.

        A script parent already occupies one reservation. Its first child uses that reservation;
        every additional active child consumes one more, transactionally visible to all queues."""
        parents = db.execute(
            """SELECT COUNT(*) FROM queue_items i JOIN queues q USING (queue_id)
            WHERE q.backend_key=? AND i.state='running'""",
            (key,),
        ).fetchone()[0]
        children = db.execute(
            """SELECT COUNT(*) AS total, COUNT(DISTINCT s.queue_id) AS parents
            FROM script_child_slots s JOIN queues q ON q.queue_id=s.queue_id WHERE q.backend_key=?""",
            (key,),
        ).fetchone()
        return parents + children["total"] - children["parents"]

    async def reserve_script_child(
        self, queue_id: str, attempt_id: str, max_concurrent: int, backend_limit: int
    ) -> bool:
        """Atomically claim a script child lane without bypassing normal queue reservations.

        False means the lane is busy; the caller may poll, but must stop polling on Stop.
        Child work is never submitted before the caller also links its attempt to the ScriptRun."""
        return await self._run(self._reserve_script_child, queue_id, attempt_id, max_concurrent, backend_limit)

    def _reserve_script_child(self, queue_id: str, attempt_id: str, max_concurrent: int, backend_limit: int) -> bool:
        if not 1 <= max_concurrent <= 10 or not 1 <= backend_limit <= MAX_QUEUE_CONCURRENCY:
            raise QueueError("Invalid script concurrency bound.")
        with self._txn() as db:
            queue = db.execute(
                "SELECT queue_id, backend_key, kind, state FROM queues WHERE queue_id=?", (queue_id,)
            ).fetchone()
            # A workflow run (#223) shares the script child lanes: its first submission uses the item's reservation.
            if queue is None or queue["kind"] not in (KIND_SCRIPT, KIND_WORKFLOW) or queue["state"] != "running":
                raise QueueError("The script queue is not running; no child generation was submitted.")
            if (
                db.execute("SELECT 1 FROM queue_items WHERE queue_id=? AND state='running'", (queue_id,)).fetchone()
                is None
            ):
                raise QueueError("The script item no longer holds its reservation.")
            active = db.execute("SELECT COUNT(*) FROM script_child_slots WHERE queue_id=?", (queue_id,)).fetchone()[0]
            # The first child uses the parent reservation; further children need a free global lane.
            if active >= min(max_concurrent, backend_limit) or (
                active > 0 and self._reserved(db, queue["backend_key"]) >= backend_limit
            ):
                return False
            db.execute("INSERT INTO script_child_slots (attempt_id, queue_id) VALUES (?,?)", (attempt_id, queue_id))
            return True

    async def release_script_child(self, queue_id: str, attempt_id: str) -> None:
        """Release one child lane only after its generation has settled, including failed saves."""
        await self._run(self._release_script_child, queue_id, attempt_id)

    def _release_script_child(self, queue_id: str, attempt_id: str) -> None:
        with self._txn() as db:
            db.execute("DELETE FROM script_child_slots WHERE queue_id=? AND attempt_id=?", (queue_id, attempt_id))

    async def settle_item(self, item_id: str, failure: dict[str, Any] | None = None) -> str:
        """Record an item's end state from its attempt; without an attempt it was definitely never submitted."""
        return await self._run(self._settle_item, item_id, failure)

    def _settle_item(self, item_id: str, failure) -> str:
        with self._txn() as db:
            item = db.execute("SELECT request_json FROM queue_items WHERE item_id=?", (item_id,)).fetchone()
            if item is None:
                raise QueueError("Unknown queue item.")
            record = json.loads(item["request_json"])
            if is_workflow_record(record):
                state, failure_json = workflow_outcome(record.get("run"))
                if failure is not None and state == "failed":
                    failure_json = json.dumps(failure)  # the runner's own reason (a run that could not start)
                db.execute(
                    "UPDATE queue_items SET state=?, failure_json=?, updated_at_utc=? WHERE item_id=?",
                    (state, failure_json, self._now(), item_id),
                )
                return state
            if is_script_record(record):
                state, failure_json = self._script_outcome(db, record, failure)
                db.execute(
                    "UPDATE queue_items SET state=?, failure_json=?, updated_at_utc=? WHERE item_id=?",
                    (state, failure_json, self._now(), item_id),
                )
                return state
            request_id = record["request_id"]
            attempt = db.execute(
                "SELECT attempt_id, state, failure_json FROM attempts WHERE request_id=?", (request_id,)
            ).fetchone()
            if attempt is None:
                state, attempt_id, failure_json = (
                    "failed",
                    None,
                    json.dumps(failure or {"code": "not_submitted", "message": "Not submitted; nothing was sent."}),
                )
            else:
                state = ATTEMPT_TO_ITEM.get(attempt["state"], "unknown")
                attempt_id, failure_json = attempt["attempt_id"], attempt["failure_json"]
            db.execute(
                "UPDATE queue_items SET state=?, attempt_id=?, failure_json=?, updated_at_utc=? WHERE item_id=?",
                (state, attempt_id, failure_json, self._now(), item_id),
            )
        return state

    def _script_outcome(self, db: sqlite3.Connection, record: dict, failure) -> tuple[str, str | None]:
        """A script item's end state comes from its run row and children, never from a lone attempt."""
        run = read_run_snapshot(self.directory, record["run_id"])
        if run is None:
            return "failed", json.dumps(
                failure or {"code": "script_not_started", "message": "The script did not start."}
            )
        state, failure_json = item_outcome(run, child_states(db, run.children))
        return state, failure_json

    async def update_workflow_run(self, item_id: str, run: dict[str, Any]) -> None:
        """Replace a reserved workflow item's run state in one short transaction (dispatch owner only).

        The state is encoded on the caller's loop, so later in-memory changes never race the write."""
        encoded = json.dumps(run, allow_nan=False)
        await self._run(self._update_workflow_run, item_id, encoded)

    def _update_workflow_run(self, item_id: str, encoded: str) -> None:
        with self._txn() as db:
            item = db.execute("SELECT request_json, state FROM queue_items WHERE item_id=?", (item_id,)).fetchone()
            if item is None:
                raise QueueError("Unknown queue item.")
            record = json.loads(item["request_json"])
            if not is_workflow_record(record) or item["state"] != "running":
                raise QueueError("Only a running workflow item's run state can change.")
            record["run"] = json.loads(encoded)
            db.execute(
                "UPDATE queue_items SET request_json=? WHERE item_id=?", (json.dumps(record, allow_nan=False), item_id)
            )

    async def workflow_scopes(self, queue_id: str) -> tuple[tuple[str, str, str], ...]:
        """Distinct (checkpoint, operation, evidence id) of the image nodes of a workflow queue's pending item (D2)."""
        return await self._run(self._workflow_scopes, queue_id)

    def _workflow_scopes(self, queue_id: str) -> tuple[tuple[str, str, str], ...]:
        from dtflow.core.workflows import IMAGE_OPERATIONS, load_workflow

        scopes: dict[tuple[str, str], str] = {}
        for row in self._db().execute(
            "SELECT request_json FROM queue_items WHERE queue_id=? AND state='pending' ORDER BY position", (queue_id,)
        ):
            try:
                record = json.loads(row["request_json"])
                if not is_workflow_record(record):
                    continue
                graph = load_workflow(record["workflow"])
            except (TypeError, ValueError, KeyError):
                continue  # claim_next and the runner reject a malformed item without submitting anything
            for node in graph.nodes:
                if node.type in IMAGE_OPERATIONS:
                    key = (str(node.params["checkpoint"]), IMAGE_OPERATIONS[node.type])
                    scopes.setdefault(key, f"workflow-{record['run_id']}")
        return tuple((model, operation, evidence) for (model, operation), evidence in scopes.items())

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
    def reconcile(db: sqlite3.Connection, now: str, directory: Path | None = None) -> None:
        """Run once by the new owner, inside the open transaction, after attempts were reconciled.

        Reserved items take their attempt's end state (never replayed). A reservation with no attempt crashed before
        anything could be submitted and returns to pending. Every live queue is restored paused.
        """
        for item in db.execute(
            "SELECT item_id, queue_id, request_json FROM queue_items WHERE state='running'"
        ).fetchall():
            record = json.loads(item["request_json"])
            if is_workflow_record(record):
                _reconcile_workflow_item(db, item, record, now)
                continue
            if is_script_record(record):
                _reconcile_script_item(db, item, record, now, directory)
                continue
            request_id = record["request_id"]
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


def _reconcile_script_item(db: sqlite3.Connection, item, record: dict, now: str, directory: Path | None) -> None:
    """A reserved script item takes its run's end state. A run is never resumed; a missing run row means it never
    started, so the item returns to pending; an unreadable ledger leaves it ``unknown``."""
    state, failure_json = "pending", None
    try:
        if directory is not None:
            _recover_runs(directory)
            run = read_run_snapshot(directory, record["run_id"])
            if run is not None:
                state, failure_json = item_outcome(run, child_states(db, run.children))
    except Exception:
        state, failure_json = (
            "unknown",
            json.dumps({"code": "script_ledger_unreadable", "message": "The script run record could not be read."}),
        )
    db.execute(
        "UPDATE queue_items SET state=?, failure_json=?, updated_at_utc=? WHERE item_id=?",
        (state, failure_json, now, item["item_id"]),
    )
    QueueMixin._event(db, item["queue_id"], "reconciled", now, {"item_id": item["item_id"]})


def _reconcile_workflow_item(db: sqlite3.Connection, item, record: dict, now: str) -> None:
    """A reserved workflow item is never resumed or replayed: ``unknown`` if a step was running (it may have been
    submitted) or already unknown, otherwise ``failed``; its run state records the same."""
    try:
        record["run"] = interrupted_run(record["run"])
        state, failure_json = workflow_outcome(record["run"])
        if state != "unknown":
            state = "failed"
            failure_json = json.dumps({"code": "workflow_interrupted", "message": record["run"]["message"]})
        stored = json.dumps(record, allow_nan=False)
    except Exception:
        state, failure_json, stored = (
            "unknown",
            json.dumps({"code": "workflow_unreadable", "message": "The workflow run record could not be read."}),
            item["request_json"],
        )
    db.execute(
        "UPDATE queue_items SET state=?, failure_json=?, request_json=?, updated_at_utc=? WHERE item_id=?",
        (state, failure_json, stored, now, item["item_id"]),
    )
    QueueMixin._event(db, item["queue_id"], "reconciled", now, {"item_id": item["item_id"]})


def _recover_runs(directory: Path) -> None:
    """Opening the run store turns runs left ``running`` by a crash (free ownership lock) into ``interrupted``."""
    from dtflow.persistence.script_runs import ScriptRunStore

    if (Path(directory) / "script_runs.sqlite3").is_file():
        ScriptRunStore(directory)


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
    supported = (2, 3, 4, SCHEMA_VERSION) if readonly else (SCHEMA_VERSION,)
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
    if action not in ACTIONS and action not in WORKFLOW_ACTIONS:
        raise QueueError("Unknown queue action.")
    payload = payload or {}
    if action == "cap" and not (type(payload.get("cap")) is int and 1 <= payload["cap"] <= MAX_QUEUE_CONCURRENCY):
        raise QueueError(f"Queue concurrency must be an integer in 1..{MAX_QUEUE_CONCURRENCY}.")
    if action in WORKFLOW_ACTIONS:
        branch = payload.get("branch")
        if not (
            type(payload.get("node_id")) is int
            and isinstance(branch, (list, tuple))
            and len(branch) == 2
            and all(type(x) is int for x in branch)
            and (action != "workflow_branch" or payload.get("decision") in BRANCH_DECISIONS)
        ):
            raise QueueError("A workflow control needs a node id, a branch and (for a decision) continue or stop.")
        payload = {key: list(value) if key == "branch" else value for key, value in payload.items()}
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT state, kind FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
            if row is None:
                raise QueueError("Unknown queue.")
            if action in WORKFLOW_ACTIONS and row["kind"] != KIND_WORKFLOW:
                raise QueueError("Branch decisions and step Retry apply to workflow queues only.")
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
    composer_source: dict | None = None,
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
            queue_id = _insert_paused(
                connection,
                backend,
                cap,
                requests,
                datetime.now(UTC).isoformat(),
                up_next=live,
                composer_source=composer_source,
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return queue_id


def insert_script_queue(
    directory: Path, backend: dict[str, Any], record: dict[str, Any], *, up_next: bool = False
) -> str:
    """Add one paused script queue (one item, cap 1) while another owner holds the state; see ``insert_queue``."""
    _check_script_record(record)
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            live = up_next and _dispatch_live(connection)
            queue_id = _insert_paused(
                connection,
                backend,
                1,
                (),
                datetime.now(UTC).isoformat(),
                up_next=live,
                kind=KIND_SCRIPT,
                records=(record,),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return queue_id


def insert_workflow_queue(
    directory: Path, backend: dict[str, Any], record: dict[str, Any], *, up_next: bool = False
) -> str:
    """Add one paused workflow queue (one item, cap 1) while another owner holds the state; see ``insert_queue``."""
    _check_workflow_record(record)
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            live = up_next and _dispatch_live(connection)
            queue_id = _insert_paused(
                connection,
                backend,
                1,
                (),
                datetime.now(UTC).isoformat(),
                up_next=live,
                kind=KIND_WORKFLOW,
                records=(record,),
            )
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
                AND NOT EXISTS (SELECT 1 FROM queue_items i WHERE i.queue_id=q.queue_id AND i.state='running')
                AND NOT EXISTS (SELECT 1 FROM script_child_slots s WHERE s.queue_id=q.queue_id)""",
            tuple(params),
        )
        if row["queue_id"] not in keep
    ]


def _delete(connection: sqlite3.Connection, queue_ids: Sequence[str]) -> None:
    for table in ("script_child_slots", "queue_controls", "queue_events", "queue_items", "queues"):
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


def delete_paused_queues(directory: Path, queue_ids: Sequence[str], keep: Sequence[str] = ()) -> tuple[int, int]:
    """Bulk-remove the named queues that are still paused with work left; return ``(removed, skipped)``.

    Eligibility is checked again inside one write transaction: a queue that started, went up next, finished, was
    cancelled, already left, holds a running reservation or is named in ``keep`` is skipped and left untouched.
    Attempts and saved outputs are never touched.
    """
    wanted = list(dict.fromkeys(queue_ids))
    if not wanted:
        return 0, 0
    connection = _shared_connection(directory)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            marks = ",".join("?" * len(wanted))
            eligible = _removable(
                connection,
                f"q.queue_id IN ({marks}) AND q.state='paused' AND EXISTS "
                "(SELECT 1 FROM queue_items i WHERE i.queue_id=q.queue_id AND i.state='pending')",
                wanted,
                keep,
            )
            _delete(connection, eligible)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return len(eligible), len(wanted) - len(eligible)


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


# Successful durations per (database directory, queue), extended from the last settlement seen: queue events are
# append-only, so each poll or snapshot reads only new settlements instead of re-parsing the whole history.
_DURATIONS: dict[tuple[str, str], tuple[int, tuple]] = {}
_DURATIONS_LOCK = threading.Lock()


def _successful_durations(connection: sqlite3.Connection, directory: Path, queue_id: str) -> list:
    key = (str(Path(directory).resolve()), queue_id)
    with _DURATIONS_LOCK:
        last, durations = _DURATIONS.get(key, (0, ()))
    rows = connection.execute(
        "SELECT event_id, json_extract(detail_json, '$.state') AS state,"
        " json_extract(detail_json, '$.backend_elapsed_seconds') AS seconds"
        " FROM queue_events WHERE queue_id=? AND kind='item_settled' AND event_id>? ORDER BY event_id",
        (queue_id, last),
    ).fetchall()
    if rows:
        durations += tuple(row["seconds"] for row in rows if row["state"] == "succeeded")
        with _DURATIONS_LOCK:
            _DURATIONS[key] = (rows[-1]["event_id"], durations)
    return list(durations)


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
            queue.setdefault("kind", "generation")  # a schema 2 to 4 database read without migrating
            summary = counts.get(row["queue_id"], {state: 0 for state in ITEM_STATES})
            queue["summary"] = summary
            queue["status"] = _status(row["state"], summary)
            queue["started_at_utc"] = None
            queue["resume_event_id"] = 0
            started_event_id = 0
            if queue["status"] in ("running", "paused"):
                started = connection.execute(
                    "SELECT event_id, at_utc FROM queue_events WHERE queue_id=? AND kind='started' ORDER BY event_id DESC LIMIT 1",
                    (row["queue_id"],),
                ).fetchone()
                if started:
                    queue["started_at_utc"] = started["at_utc"]
                    started_event_id = started["event_id"]
                resumed = connection.execute(
                    "SELECT event_id FROM queue_events WHERE queue_id=? AND kind='resume' ORDER BY event_id DESC LIMIT 1",
                    (row["queue_id"],),
                ).fetchone()
                if resumed:
                    queue["resume_event_id"] = resumed["event_id"]
            queue["running_started_at_utc"] = (
                [
                    item["updated_at_utc"]
                    for item in connection.execute(
                        "SELECT updated_at_utc FROM queue_items WHERE queue_id=? AND state='running' ORDER BY item_id",
                        (row["queue_id"],),
                    )
                ]
                if queue["status"] == "running"
                else []
            )
            latest_timing = connection.execute(
                "SELECT at_utc, detail_json FROM queue_events WHERE queue_id=? AND kind='timing_snapshot' AND event_id>?"
                " ORDER BY event_id DESC LIMIT 1",
                # A snapshot from before the latest start or Resume aged through the pause; never surface it.
                (row["queue_id"], max(started_event_id, queue["resume_event_id"])),
            ).fetchone()
            if latest_timing:
                try:
                    queue["timing_snapshot"] = {
                        "at_utc": latest_timing["at_utc"],
                        **json.loads(latest_timing["detail_json"]),
                    }
                except (TypeError, ValueError):
                    pass
            queue["successful_durations"] = (
                _successful_durations(connection, directory, row["queue_id"])
                if queue["status"] == "running" and summary["succeeded"] >= 1
                else []
            )
            first = connection.execute(
                "SELECT request_json FROM queue_items WHERE queue_id=? ORDER BY position LIMIT 1", (row["queue_id"],)
            ).fetchone()
            try:
                request = json.loads(first["request_json"]) if first else {}
            except (TypeError, ValueError):
                request = {}
            request = request if isinstance(request, dict) else {}
            script_name = request.get("source_name") if is_script_record(request) else None
            workflow_name = request.get("name") if is_workflow_record(request) else None
            queue["script_elapsed"] = _script_queue_elapsed(directory, request) if script_name is not None else None
            queue["first"] = {
                "prompt": f"Script: {script_name}"
                if isinstance(script_name, str)
                else f"Workflow: {workflow_name}"
                if isinstance(workflow_name, str)
                else (request.get("prompt") if isinstance(request.get("prompt"), str) else ""),
                "prompt_runs": _item_request_fields(request)["prompt_runs"],
                "model": request.get("model_id") if isinstance(request.get("model_id"), str) else "",
                "operation": request.get("operation") or "txt2img",
                "loras": [
                    (lora["identifier"], lora.get("weight"))
                    for lora in request.get("loras") or ()
                    if isinstance(lora, dict) and isinstance(lora.get("identifier"), str)
                ],
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


def read_queue_requests(directory: Path, queue_id: str) -> tuple[dict[str, Any], int, tuple[tuple[int, str, Any], ...]]:
    """(backend, cap, (position, state, stored request record) per item in order) of one queue, for a relaunch.

    A read-only snapshot: the records are what each item recorded at creation and are decoded by the caller, so an
    item with a malformed request is refused there rather than silently dropped here.
    """
    connection = _shared_connection(directory, readonly=True)
    try:
        row = connection.execute("SELECT backend_json, cap FROM queues WHERE queue_id=?", (queue_id,)).fetchone()
        if row is None:
            raise QueueError("Unknown queue.")
        items = []
        for item in connection.execute(
            "SELECT position, state, request_json FROM queue_items WHERE queue_id=? ORDER BY position", (queue_id,)
        ):
            try:
                record = json.loads(item["request_json"])
            except (TypeError, ValueError):
                record = None
            items.append((item["position"], item["state"], record))
        return json.loads(row["backend_json"]), row["cap"], tuple(items)
    finally:
        connection.close()


def read_queue_composer_source(directory: Path, queue_id: str) -> dict | None:
    """Optional immutable composer input, separate from resolved item requests and image metadata."""
    connection = _shared_connection(directory, readonly=True)
    try:
        row = connection.execute(
            "SELECT detail_json FROM queue_events WHERE queue_id=? AND kind='composer_source' ORDER BY event_id LIMIT 1",
            (queue_id,),
        ).fetchone()
        if row is None:
            return None
        if len(row["detail_json"].encode("utf-8")) > 1_048_576:
            raise QueueError("Stored composer source exceeds the supported limit.")
        value = json.loads(row["detail_json"])
        if not isinstance(value, dict):
            raise QueueError("Stored composer source is unreadable.")
        return value
    finally:
        connection.close()


def read_queues(directory: Path, queue_id: str | None = None) -> tuple[dict[str, Any], ...]:
    """Inspect queues and items in their original order without owning or modifying state."""
    connection = _shared_connection(directory, readonly=True)
    try:
        return _read(connection, queue_id)
    finally:
        connection.close()


def _item_request_fields(request: dict) -> dict[str, Any]:
    """What an item asked for, read from its stored request for the items table (never inferred)."""

    def text(key: str) -> str:
        value = request.get(key)
        return value if isinstance(value, str) else ""

    def number(key: str):
        value = request.get(key)
        return value if type(value) in (int, float) else None

    evidence = request.get("resolution_evidence")
    runs = (
        next(
            (
                pair[1]
                for pair in evidence
                if isinstance(pair, list) and len(pair) == 2 and pair[0] == "prompt.variation_runs"
            ),
            "",
        )
        if isinstance(evidence, list)
        else ""
    )
    return {
        "prompt": text("prompt"),
        "prompt_runs": runs if isinstance(runs, str) else "",  # which parts a prompt variation produced
        "negative_prompt": text("negative_prompt"),
        "model": text("model_id"),
        "loras": [
            (lora["identifier"], lora.get("weight"))
            for lora in request.get("loras") or ()
            if isinstance(lora, dict) and isinstance(lora.get("identifier"), str)
        ],
        "steps": number("steps"),
        "guidance": number("guidance"),
        "sampler": text("sampler_id"),
        "width": number("width_px"),
        "height": number("height_px"),
    }


def _script_item_fields(request: dict, script: dict[str, Any]) -> dict[str, Any]:
    """Row fields of a script item: the run, the settings it started with, and its steps' files in call order.

    The first step's attempt stands for the row (preview, Open in Images); later steps contribute their images only, so
    the item counts one metadata file and one image per step.
    """
    start = (request.get("start") or {}).get("base")
    base = start if isinstance(start, dict) else {}
    children = script["children"]
    artifacts: list[Any] = []
    for index, child in enumerate(children):
        artifacts += [
            path
            for path in child["artifacts"]
            if index == 0 or not str(path).replace("\\", "/").endswith("/metadata.json")
        ]
    return {
        **_item_request_fields(base),
        "script": script,
        "prompt": f"Script: {request.get('source_name', '')}",
        "attempt_id": children[0]["attempt_id"] if children else None,
        "seed": base.get("seed"),
        "artifacts": artifacts,
    }


_FINISHED_ELAPSED: dict[str, float | None] = {}  # run id -> wall seconds of a finished run (it never changes)


def _script_queue_elapsed(directory: Path, request: dict) -> float | None:
    """Wall seconds of a script queue's run for the list; finished runs are read once."""
    run_id = str(request.get("run_id"))
    if run_id in _FINISHED_ELAPSED:
        return _FINISHED_ELAPSED[run_id]
    try:
        run = read_run_snapshot(Path(directory), run_id)
    except Exception:
        return None
    if run is None:
        return None
    seconds = _elapsed(run.created_at, run.finished_at)
    if run.finished_at is not None:
        _FINISHED_ELAPSED[run_id] = seconds
    return seconds


def _elapsed(start: str | None, end: str | None) -> float | None:
    """Wall seconds between two ISO timestamps; None while unfinished or unreadable (never zero by default)."""
    try:
        seconds = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _step_fields(artifacts: list) -> dict[str, Any]:
    """What one step actually asked for, from its attempt's saved metadata (empty when absent or unreadable)."""
    path = next((p for p in artifacts if isinstance(p, str) and Path(p).name == "metadata.json"), None)
    if path is None:
        return {}
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        request = record.get("request") if isinstance(record, dict) else None
        if not isinstance(request, dict):
            return {}
        timing = record.get("timing")
        seconds = timing.get("backend_elapsed_seconds") if isinstance(timing, dict) else None
        return (
            _item_request_fields(request)
            | {"seed": request.get("seed")}
            | ({"duration": float(seconds)} if type(seconds) in (int, float) else {})
        )
    except (OSError, ValueError):
        return {}


def _script_view(connection: sqlite3.Connection, request: dict) -> dict[str, Any]:
    """A script item's run (name, SHA, run id, run state) and its ordered child steps, read through to their attempts.

    ``steps_failed`` is derived from the children (a script may swallow a failed generation and still complete).
    """
    path = connection.execute("PRAGMA database_list").fetchone()["file"]
    view: dict[str, Any] = {
        "name": request.get("source_name"),
        "expected_steps": request.get("expected_steps") if type(request.get("expected_steps")) is int else None,
        "source_sha256": request.get("source_sha256"),
        "run_id": request.get("run_id"),
        "run_state": None,
        "error": None,
        "children": [],
        "steps_failed": 0,
        "elapsed": None,
        "waiting_for_input": False,
        "prompt": None,
    }
    try:
        run = read_run_snapshot(Path(path).parent, str(request.get("run_id")))
    except Exception:
        return view | {"run_state": "unreadable"}
    if run is None:
        return view
    view["run_state"], view["error"] = run.state, run.error
    view["elapsed"] = _elapsed(run.created_at, run.finished_at)
    if run.state == "running":  # an open form keeps the item running; the view only flags it (no new item state)
        try:
            prompt = read_open_prompt(Path(path).parent, str(request.get("run_id")))
        except Exception:
            prompt = None
        if prompt is not None:
            view["waiting_for_input"] = True
            view["prompt"] = {
                "ordinal": prompt.ordinal,
                "title": prompt.title,
                "confirm": prompt.confirm,
                "components": prompt.components,
            }
    steps: dict[str, dict[str, Any]] = {}  # attempt id -> its child entry
    for ordinal, attempt_id in enumerate(run.children, 1):
        row = connection.execute(
            "SELECT state, artifacts_json, failure_json, request_json FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        state = row["state"] if row else None
        artifacts = json.loads(row["artifacts_json"]) if row and row["artifacts_json"] else []
        failure = json.loads(row["failure_json"]) if row and row["failure_json"] else None
        # A retry keeps the step number of the try it replaces; that earlier try keeps its own state, marked retried.
        earlier = steps.get(retry_of(row["request_json"]) if row else None)
        if earlier is not None:
            earlier["retried"] = True
        child = {
            "ordinal": ordinal,
            "step": earlier["step"] if earlier is not None else 1 + sum(1 for c in steps.values() if not c["retry"]),
            "retry": earlier is not None,
            "retried": False,
            "attempt_id": attempt_id,
            "state": state,
            "artifacts": artifacts,
            "fields": _step_fields(artifacts),
            "failure": failure if isinstance(failure, dict) else None,
        }
        steps[attempt_id] = child
        view["children"].append(child)
    view["steps_failed"] = sum(
        1
        for child in view["children"]
        if not child["retried"] and child["state"] in TERMINAL_ATTEMPTS and child["state"] != "succeeded"
    )
    return view


def _workflow_view(request: dict, item_state: str | None = None) -> dict[str, Any]:
    """A workflow item's run state plus its name and the stage of the step that is running now (for activity).

    The item settles without the runner when it never started (Stop or Clear while it waited behind another queue);
    its stored run then still says pending, so the view reports the item's end instead of a run that never ends."""
    run = request.get("run") if isinstance(request.get("run"), dict) else {}
    view = {**run, "name": request.get("name"), "run_id": request.get("run_id")}
    if item_state in ("cancelled", "failed", "unknown", "save_failed") and run.get("state") in ("pending", "running"):
        view["state"] = "cancelled" if item_state == "cancelled" else item_state.replace("save_failed", "failed")
        view["message"] = "Stopped before it started; nothing was sent." if item_state == "cancelled" else None
        view["pauses"] = []
        view["steps"] = [
            {**step, "state": "cancelled", "stage": None}
            if isinstance(step, dict) and step.get("state") in ("pending", "running", "paused")
            else step
            for step in run.get("steps") or ()
        ]
        run = view
    running = [step for step in run.get("steps") or () if isinstance(step, dict) and step.get("state") == "running"]
    current = max(running, key=lambda step: str(step.get("started_at") or ""), default=None)
    view["stage"] = (
        f"{current.get('title')} · {current.get('branch_label')}: {current.get('stage') or 'Running'}"
        if current is not None
        else None
    )
    return view


def _workflow_item_fields(request: dict, workflow: dict[str, Any]) -> dict[str, Any]:
    """Row fields of a workflow item: the run and the saved results' files (each step's image is in Images)."""
    return {
        "workflow": workflow,
        "prompt": f"Workflow: {request.get('name', '')}",
        "seed": None,  # the record's "seed" is a Retry seed, not a generation seed
        "artifacts": [result["path"] for result in workflow.get("results") or () if isinstance(result, dict)],
    }


def _item_durations(connection: sqlite3.Connection, queue_id: str) -> dict[str, float]:
    """Observed backend seconds per settled item; an item without a recorded duration is simply absent."""
    durations: dict[str, float] = {}
    for event in connection.execute(
        "SELECT json_extract(detail_json, '$.item_id') AS item_id,"
        " json_extract(detail_json, '$.backend_elapsed_seconds') AS seconds"
        " FROM queue_events WHERE queue_id=? AND kind='item_settled' ORDER BY event_id",
        (queue_id,),
    ):
        if isinstance(event["item_id"], str) and type(event["seconds"]) in (int, float):
            durations[event["item_id"]] = float(event["seconds"])
    return durations


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
        queue.setdefault("kind", "generation")  # a schema 2 to 4 database read without migrating
        items = []
        for item in connection.execute(
            """SELECT i.item_id, i.position, i.state, i.attempt_id, i.updated_at_utc, i.failure_json, i.request_json,
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
            script = _script_view(connection, request) if is_script_record(request) else None
            workflow = _workflow_view(request, item["state"]) if is_workflow_record(request) else None
            items.append(
                {
                    "position": item["position"],
                    "item_id": item["item_id"],
                    "state": item["state"],
                    "updated_at_utc": item["updated_at_utc"],
                    "attempt_id": item["attempt_id"],
                    "request_id": request.get("run_id")
                    if script is not None or workflow is not None
                    else request.get("request_id"),
                    "seed": request.get("seed"),
                    "operation": request.get("operation") or "txt2img",
                    "strength": request.get("strength"),
                    **_item_request_fields(request),
                    "failure": json.loads(item["failure_json"]) if item["failure_json"] else None,
                    "artifacts": json.loads(item["artifacts_json"]) if item["artifacts_json"] else [],
                    **(_script_item_fields(request, script) if script else {}),
                    **(_workflow_item_fields(request, workflow) if workflow is not None else {}),
                }
            )
        durations = _item_durations(connection, row["queue_id"])
        for item in items:
            item["duration"] = durations.get(item["item_id"])
            if item["duration"] is None and item.get("script"):
                item["duration"] = item["script"].get("elapsed")  # a script's wall time, start to finish
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

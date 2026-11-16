"""Shared queue session functions (used by the CLI and the desktop): creation, validation and control posting.

Scheduler, reservation and no-replay semantics are owned by test_queue.py; this covers only the extracted orchestration.
"""

import json
import sqlite3

import pytest

from dtflow.core.models import ResolvedGenerationRequest, ValidationError
from dtflow.persistence.queues import request_from_record
from dtflow.persistence.store import SQLiteAttempts, StateBusyError
from dtflow.services.planning import Plan, expand_plan
from dtflow.services.queue_session import (
    create_queue,
    dispatch_queue,
    expand_items,
    post_queue_control,
    read_composer_source,
    read_queue_list,
    read_queue_views,
)

BACKEND = {"name": "fake", "endpoint": None, "scenario": "success"}


def base(seed=5):
    return ResolvedGenerationRequest("base", "fixture", "fake-fixture", seed)


@pytest.mark.asyncio
async def test_composer_source_is_atomic_and_shared_owner_safe(tmp_path):
    source = {"version": 1, "base": {"prompt": "a {cat|dog}"}, "form": {"variation": "Every combination"}}
    queue_id = await create_queue(tmp_path, BACKEND, expand_items(base(), 1), composer_source=source)
    assert await read_composer_source(tmp_path, queue_id) == source
    assert await read_composer_source(tmp_path, "missing") is None
    owner = SQLiteAttempts(tmp_path)
    await owner.open()
    try:
        shared_id = await create_queue(tmp_path, BACKEND, expand_items(base(), 1), composer_source=source)
        assert await read_composer_source(tmp_path, shared_id) == source
        with pytest.raises(ValidationError, match="1 MiB"):
            await create_queue(tmp_path, BACKEND, expand_items(base(), 1), composer_source={"text": "x" * 1_048_577})
    finally:
        await owner.aclose()
    assert len(await read_queue_list(tmp_path)) == 2
    with pytest.raises(ValidationError, match="1 MiB"):
        await create_queue(tmp_path, BACKEND, expand_items(base(), 1), composer_source={"text": "x" * 1_048_577})
    assert len(await read_queue_list(tmp_path)) == 2
    with sqlite3.connect(tmp_path / "attempts.sqlite3") as db:
        records = [row[0] for row in db.execute("SELECT request_json FROM queue_items")]
    assert len(records) == 2 and all("composer_source" not in record and "cat|dog" not in record for record in records)


def test_expand_items_numbers_seeds_and_validates_before_anything_is_created():
    items = expand_items(base(), 3)
    assert [item.seed for item in items] == [5, 6, 7] and len({item.request_id for item in items}) == 3
    assert dict(items[2].resolution_evidence)["queue_position"] == "3"
    for bad in (0, 1001, True, "2"):
        with pytest.raises(ValidationError):
            expand_items(base(), bad)
    with pytest.raises(ValidationError, match="32-bit"):
        expand_items(base(4294967295), 2)


@pytest.mark.asyncio
async def test_create_read_dispatch_and_controls_round_trip(tmp_path):
    assert await read_queue_views(tmp_path) == ()  # no database yet: no queues, no error
    queue_id = await create_queue(tmp_path, BACKEND, expand_items(base(), 2))
    (view,) = await read_queue_views(tmp_path)
    assert view["status"] == "paused" and [item["seed"] for item in view["items"]] == [5, 6]
    await post_queue_control(tmp_path, queue_id, "pause")
    final = await dispatch_queue(tmp_path, queue_id)  # a posted control never blocks the explicit start
    assert final["status"] == "finished" and final["summary"]["succeeded"] == 2
    assert all(item["artifacts"] for item in (await read_queue_views(tmp_path, queue_id))[0]["items"])


@pytest.mark.asyncio
async def test_local_without_endpoint_creates_nothing(tmp_path):
    with pytest.raises(ValidationError, match="endpoint"):
        await create_queue(tmp_path, {"name": "local", "endpoint": None}, expand_items(base(), 1))
    assert not (tmp_path / "attempts.sqlite3").exists()


@pytest.mark.asyncio
async def test_a_second_dispatch_owner_is_refused_before_dispatching(tmp_path):
    queue_id = await create_queue(tmp_path, BACKEND, expand_items(base(), 1))
    owner = SQLiteAttempts(tmp_path)
    await owner.open()
    try:
        with pytest.raises(StateBusyError):
            await dispatch_queue(tmp_path, queue_id)
    finally:
        await owner.aclose()
    assert (await read_queue_views(tmp_path))[0]["summary"]["pending"] == 1


@pytest.mark.asyncio
async def test_summary_timing_reads_successful_settlements_without_loading_items(tmp_path):
    queue_id = await create_queue(tmp_path, BACKEND, expand_items(base(), 3))
    owner = SQLiteAttempts(tmp_path)
    await owner.open()
    try:
        await owner.set_running(queue_id)
        with sqlite3.connect(tmp_path / "attempts.sqlite3") as db:
            db.execute("UPDATE queue_items SET state='succeeded' WHERE queue_id=? AND position=1", (queue_id,))
            db.execute("UPDATE queue_items SET state='running' WHERE queue_id=? AND position=2", (queue_id,))
        for state, seconds in (("succeeded", 30), ("failed", 900), ("unknown", 800)):
            await owner.record_queue_event(
                queue_id, "item_settled", {"state": state, "backend_elapsed_seconds": seconds}
            )
        (summary,) = await read_queue_list(tmp_path)
        assert summary["successful_durations"] == [30]
        assert summary["started_at_utc"] and "items" not in summary and "events" not in summary
        assert summary["owner_active"]
        for action in ("pause", "resume"):
            await post_queue_control(tmp_path, queue_id, action)
        assert await owner.apply_controls(queue_id) == ("pause", "resume")
        (resumed,) = await read_queue_list(tmp_path)
        assert resumed["resume_event_id"] > summary["resume_event_id"]
        assert resumed["started_at_utc"] == summary["started_at_utc"]
        assert resumed["summary"] == summary["summary"]
        assert resumed["successful_durations"] == summary["successful_durations"]
    finally:
        await owner.aclose()


@pytest.mark.asyncio
async def test_planned_requests_enter_the_queue_unchanged_and_are_restored_from_storage(tmp_path):
    """6-O4: the persisted items equal the planner output; scheduling is inherited, not retested."""
    spec = Plan("cross", (("prompt", ("a", "b")), ("steps", (4, 8))), seed_policy="increment")
    requests = expand_plan(base(), spec)
    queue_id = await create_queue(tmp_path, BACKEND, requests)
    with sqlite3.connect(tmp_path / "attempts.sqlite3") as db:
        stored = [
            request_from_record(json.loads(row[0]))
            for row in db.execute(
                "SELECT request_json FROM queue_items WHERE queue_id=? ORDER BY position", (queue_id,)
            )
        ]
    assert stored == requests and [item.seed for item in stored] == [5, 6, 7, 8]
    final = await dispatch_queue(tmp_path, queue_id)
    assert final["summary"]["succeeded"] == 4

"""Shared queue session functions (used by the CLI and the desktop): creation, validation and control posting.

Scheduler, reservation and no-replay semantics are owned by test_queue.py; this covers only the extracted orchestration.
"""

import json
import sqlite3

import pytest

from dtflow.core.models import ResolvedGenerationRequest, ValidationError
from dtflow.persistence.settings import SettingsError, load_cloud_concurrency
from dtflow.persistence.store import SQLiteAttempts, StateBusyError
from dtflow.services.queue_session import (
    create_queue,
    default_cap,
    dispatch_queue,
    expand_items,
    post_queue_control,
    read_queue_views,
    set_concurrency,
)

BACKEND = {"name": "fake", "endpoint": None, "scenario": "success"}


def base(seed=5):
    return ResolvedGenerationRequest("base", "fixture", "fake-fixture", seed)


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
    queue_id, cap = await create_queue(tmp_path, BACKEND, expand_items(base(), 2))
    assert cap == default_cap(tmp_path, "fake") == 4
    (view,) = await read_queue_views(tmp_path)
    assert view["status"] == "paused" and [item["seed"] for item in view["items"]] == [5, 6]
    await post_queue_control(tmp_path, queue_id, "pause")
    final = await dispatch_queue(tmp_path, queue_id)  # a posted control never blocks the explicit start
    assert final["status"] == "finished" and final["summary"]["succeeded"] == 2
    assert all(item["artifacts"] for item in (await read_queue_views(tmp_path, queue_id))[0]["items"])


@pytest.mark.asyncio
async def test_invalid_cap_and_local_without_endpoint_create_nothing(tmp_path):
    with pytest.raises(ValidationError):
        await create_queue(tmp_path, BACKEND, expand_items(base(), 1), 9)
    with pytest.raises(ValidationError, match="endpoint"):
        await create_queue(tmp_path, {"name": "local", "endpoint": None}, expand_items(base(), 1))
    assert not (tmp_path / "attempts.sqlite3").exists()


@pytest.mark.asyncio
async def test_concurrency_is_validated_before_anything_is_saved_or_posted(tmp_path):
    queue_id, _ = await create_queue(tmp_path, BACKEND, expand_items(base(), 1))
    for bad in (0, 5, True):
        with pytest.raises(SettingsError):
            await set_concurrency(tmp_path, bad, queue_id=queue_id, save_shared=True)
    assert not (tmp_path / "settings.json").exists()
    assert await set_concurrency(tmp_path, 3, queue_id=queue_id, save_shared=True) == 3
    assert load_cloud_concurrency(tmp_path) == 3
    with sqlite3.connect(tmp_path / "attempts.sqlite3") as db:
        assert db.execute("SELECT action, payload_json FROM queue_controls").fetchall() == [
            ("cap", json.dumps({"cap": 3}))
        ]


@pytest.mark.asyncio
async def test_a_second_dispatch_owner_is_refused_before_dispatching(tmp_path):
    queue_id, _ = await create_queue(tmp_path, BACKEND, expand_items(base(), 1))
    owner = SQLiteAttempts(tmp_path)
    await owner.open()
    try:
        with pytest.raises(StateBusyError):
            await dispatch_queue(tmp_path, queue_id)
    finally:
        await owner.aclose()
    assert (await read_queue_views(tmp_path))[0]["summary"]["pending"] == 1

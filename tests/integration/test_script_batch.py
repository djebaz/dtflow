"""Deterministic script batch contracts: shared reservations, ordered outcomes and no unbounded dispatch.

These tests never contact a provider or execute billable image generation.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dtflow.core.models import ResolvedGenerationRequest
from dtflow.persistence.script_items import script_record
from dtflow.persistence.store import SQLiteAttempts
from dtflow.scripting.host import ScriptHost, baseline_configuration


BACKEND_KEY = "fake:-"


async def _populate_running_queues(store: SQLiteAttempts, *, ordinary: int = 0) -> None:
    """Insert controlled, already-claimed queue items to exercise atomic reservation accounting."""

    def insert() -> None:
        now = store._now()
        with store._txn() as db:
            for index, kind in enumerate(("script", *(("generation",) * ordinary))):
                queue_id = "script-queue" if index == 0 else f"ordinary-queue-{index}"
                request = asdict(ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", 50))
                if kind == "script":
                    request = script_record(
                        run_id=uuid4().hex,
                        source_name="fixture.js",
                        source_sha256="0" * 64,
                        base=request,
                        backend="fake",
                        max_generations=4,
                        answers=None,
                    )
                db.execute(
                    "INSERT INTO queues (queue_id, backend_key, backend_json, state, cap, "
                    "created_at_utc, updated_at_utc, kind) VALUES (?, ?, ?, 'running', 4, ?, ?, ?)",
                    (queue_id, BACKEND_KEY, json.dumps({"name": "fake", "endpoint": None}), now, now, kind),
                )
                db.execute(
                    "INSERT INTO queue_items (item_id, queue_id, position, request_json, state, "
                    "updated_at_utc) VALUES (?, ?, 1, ?, 'running', ?)",
                    (f"item-{index}", queue_id, json.dumps(request), now),
                )

    await store._run(insert)


@pytest.mark.asyncio
async def test_script_children_are_counted_with_ordinary_queue_slots(tmp_path):
    """First child reuses parent reservation; further children compete with ordinary queue items."""
    store = SQLiteAttempts(tmp_path / "state")
    await store.open()
    try:
        await _populate_running_queues(store, ordinary=1)

        def used():
            return store._run(lambda: store._reserved(store._db(), BACKEND_KEY))

        assert await used() == 2  # one running script parent, one ordinary queue item

        assert await store.reserve_script_child("script-queue", "child-a", 4, 4)
        assert await used() == 2
        assert await store.reserve_script_child("script-queue", "child-b", 4, 4)
        assert await store.reserve_script_child("script-queue", "child-c", 4, 4)
        assert await used() == 4
        assert not await store.reserve_script_child("script-queue", "child-d", 4, 4)

        await store.release_script_child("script-queue", "child-b")
        assert await used() == 3
        assert await store.reserve_script_child("script-queue", "child-d", 4, 4)
        assert await used() == 4

        # A second script's requested limit still applies even when global capacity is free.
        assert not await store.reserve_script_child("script-queue", "child-e", 2, 4)
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_child_slot_recovery_discards_stale_lanes_not_history(tmp_path):
    """A restarted owner never treats an old child slot as a running remote generation."""
    directory = tmp_path / "state"
    store = SQLiteAttempts(directory)
    await store.open()
    await _populate_running_queues(store)
    assert await store.reserve_script_child("script-queue", "old-child", 4, 4)
    await store.aclose()

    recovered = SQLiteAttempts(directory)
    await recovered.open()
    try:
        assert (
            await recovered._run(
                lambda: (
                    recovered._db()
                    .execute("SELECT COUNT(*) FROM queue_items WHERE lanes_json IS NOT NULL")
                    .fetchone()[0]
                )
            )
            == 0
        )
        assert (
            await recovered._run(
                lambda: (
                    recovered._db().execute("SELECT COUNT(*) FROM queues WHERE queue_id='script-queue'").fetchone()[0]
                )
            )
            == 1
        )
    finally:
        await recovered.aclose()


@pytest.mark.asyncio
async def test_batch_workers_overlap_and_return_in_input_order(tmp_path, monkeypatch):
    """Workers overlap at the desired limit even when the second generation finishes first."""
    from dtflow.scripting import host as host_module
    from dtflow.services.session import GenerationOutcome

    loop = asyncio.get_running_loop()
    linked = []
    started = []
    completed = []
    active = 0
    peak = 0
    slots = set()
    base = ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", 50)

    async def reserve_child(attempt_id: str, limit: int) -> bool:
        if len(slots) >= limit:
            return False
        slots.add(attempt_id)
        return True

    async def release_child(attempt_id: str) -> None:
        slots.remove(attempt_id)

    async def fake_generation(service, request, *, prefix=False, attempt_id, layout=None):
        nonlocal active, peak
        assert attempt_id in linked
        active += 1
        peak = max(peak, active)
        started.append(request.seed)
        try:
            # Force the second request to finish ahead of the first.
            await asyncio.sleep(0.045 if request.seed % 2 else 0.01)
            completed.append(request.seed)
            return GenerationOutcome(
                attempt_id=attempt_id,
                backend_id="fake",
                output_paths=(f"/fake/output-{request.seed}.png",),
                metadata_path=f"/fake/meta-{request.seed}.json",
                warnings=(),
                diagnostics=(),
                request=request,
            )
        finally:
            active -= 1

    monkeypatch.setattr(host_module, "run_generation", fake_generation)
    host = ScriptHost(
        service=SimpleNamespace(backend=SimpleNamespace(capabilities=SimpleNamespace(max_concurrency=4))),
        base=base,
        loop=loop,
        link_child=linked.append,
        console=lambda text, kind: None,
        cancel=threading.Event(),
        sandbox=tmp_path / "script",
        reserve_child=reserve_child,
        release_child=release_child,
    )
    jobs = [ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", seed) for seed in (1, 2, 3, 4)]
    results = await host._run_batch(jobs, [None] * len(jobs), 2)
    assert peak == 2
    assert started == [1, 2, 3, 4] or set(started) == {1, 2, 3, 4}
    assert completed[0] == 2
    assert [result["status"] for result in results] == ["succeeded"] * 4
    assert [result["outputPaths"] for result in results] == [[f"/fake/output-{seed}.png"] for seed in (1, 2, 3, 4)]
    assert len(set(linked)) == 4
    assert slots == set()


@pytest.mark.asyncio
async def test_batch_ceiling_rejects_before_any_child_is_linked(tmp_path):
    base = ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", 50)
    linked = []
    host = ScriptHost(
        service=SimpleNamespace(),
        base=base,
        loop=asyncio.get_running_loop(),
        link_child=linked.append,
        console=lambda text, kind: None,
        cancel=threading.Event(),
        sandbox=tmp_path,
        max_generations=30,
    )
    valid = {"configuration": baseline_configuration(base), "prompt": "fixture"}
    with pytest.raises(ValueError, match="1..max_generations"):
        host.generate_batch([valid] * 31, 1)
    assert not linked
    assert host.generations == 0


@pytest.mark.asyncio
async def test_batch_continues_after_confirmed_backend_failure(tmp_path, monkeypatch):
    """One definitive failure must not turn the remaining jobs into unsubmitted work."""
    from dtflow.core.models import BackendError, BackendFailure, ErrorCategory, RemoteOutcome
    from dtflow.scripting import host as host_module
    from dtflow.services.session import GenerationOutcome

    base = ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", 12)
    linked = []
    submitted = []

    async def fake_generation(service, request, *, prefix=False, attempt_id, layout=None):
        assert attempt_id in linked
        submitted.append(request.seed)
        if request.seed == 1:
            raise BackendError(
                BackendFailure(
                    category=ErrorCategory.REMOTE_REJECTION,
                    code="fixture_rejected",
                    message="Confirmed fixture rejection.",
                    remote_outcome=RemoteOutcome.FAILED,
                )
            )
        return GenerationOutcome(
            attempt_id=attempt_id,
            backend_id="fake",
            output_paths=(f"/fake/{request.seed}.png",),
            metadata_path=f"/fake/{request.seed}.json",
            warnings=(),
            diagnostics=(),
            request=request,
        )

    monkeypatch.setattr(host_module, "run_generation", fake_generation)
    host = ScriptHost(
        service=SimpleNamespace(backend=SimpleNamespace(capabilities=SimpleNamespace(max_concurrency=2))),
        base=base,
        loop=asyncio.get_running_loop(),
        link_child=linked.append,
        console=lambda text, kind: None,
        cancel=threading.Event(),
        sandbox=tmp_path / "script",
    )
    jobs = [ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", seed) for seed in (1, 2, 3)]
    results = await host._run_batch(jobs, [None] * len(jobs), 2)
    assert [r["status"] for r in results] == ["failed", "succeeded", "succeeded"]
    assert results[0]["error"] == "fixture_rejected"
    assert set(submitted) == {1, 2, 3}
    assert len(linked) == 3
    assert host._blocked is None


@pytest.mark.asyncio
async def test_batch_unknown_outcome_does_not_stop_later_jobs(tmp_path, monkeypatch):
    """An unknown remote result keeps its own status; later jobs still run (#220)."""
    from dtflow.core.models import BackendError, BackendFailure, ErrorCategory, RemoteOutcome
    from dtflow.scripting import host as host_module

    base = ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", 12)
    linked = []
    submitted = []

    async def fake_generation(service, request, *, prefix=False, attempt_id, layout=None):
        assert attempt_id in linked
        submitted.append(request.seed)
        raise BackendError(
            BackendFailure(
                category=ErrorCategory.TIMEOUT,
                code="fixture_uncertain",
                message="Fixture submission outcome is unknown.",
                remote_outcome=RemoteOutcome.UNKNOWN,
            )
        )

    monkeypatch.setattr(host_module, "run_generation", fake_generation)
    host = ScriptHost(
        service=SimpleNamespace(backend=SimpleNamespace(capabilities=SimpleNamespace(max_concurrency=4))),
        base=base,
        loop=asyncio.get_running_loop(),
        link_child=linked.append,
        console=lambda text, kind: None,
        cancel=threading.Event(),
        sandbox=tmp_path / "script",
    )
    jobs = [ResolvedGenerationRequest(uuid4().hex, "fixture", "fake-fixture", seed) for seed in (1, 2, 3)]
    results = await host._run_batch(jobs, [None] * len(jobs), 1)
    assert [r["status"] for r in results] == ["unknown", "unknown", "unknown"]
    assert results[0]["error"] == "fixture_uncertain"
    assert submitted == [1, 2, 3]
    assert len(linked) == 3
    assert host._blocked is None


def test_parallel_child_ordinal_allocation_is_atomic(tmp_path):
    """Concurrent workers must not reuse an ordinal or lose an attempt identity."""
    from concurrent.futures import ThreadPoolExecutor

    from dtflow.scripting.run_state import ScriptRun, new_run

    written = []
    run = ScriptRun(
        uuid4().hex,
        new_run(
            source_name="parallel-fixture.js", source_sha256="0" * 64, api_version="1.0", vendored_js_sha256="0" * 64
        ),
        lambda state, recall: written.append(list(state["children"])),
    )
    attempt_ids = [uuid4().hex for _ in range(16)]
    barrier = threading.Barrier(4)

    def link(index_and_id):
        index, attempt_id = index_and_id
        if index < 4:
            barrier.wait(timeout=5)
        return run.link_child(attempt_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        ordinals = list(pool.map(link, enumerate(attempt_ids)))
    assert sorted(ordinals) == list(range(1, len(attempt_ids) + 1))
    assert len(run.children) == len(attempt_ids)
    assert set(run.children) == set(attempt_ids)
    assert written[-1] == list(run.children)  # each link was written before it took effect
    run.finish("completed")

"""End-to-end fake services, SQLite and saved outputs without provider traffic."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from dtflow.backends.fake import FakeBackend
from dtflow.core.models import (
    BackendError,
    CompletionEvidence,
    Designation,
    ErrorCategory,
    RemoteOutcome,
    ResolvedGenerationRequest,
    TimeoutPolicy,
)
from dtflow.persistence.store import PersistenceError, SQLiteAttempts
from dtflow.services.generation import GenerationService


def request(identifier="request"):
    return ResolvedGenerationRequest(identifier, "fixture", "fake-fixture", 0)


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


@pytest.mark.asyncio
async def test_connect_inventory_and_saved_success(state):
    store = SQLiteAttempts(state)
    backend = FakeBackend()
    try:
        await store.open()
        await backend.open()
        service = GenerationService(backend, store, state / "outputs")
        health = await service.check_connection()
        assert (health.transport, health.authentication, health.inventory) == ("ok", "not_applicable", "ok")
        inventory = await service.get_inventory(True)
        assert inventory.refreshed and inventory.entries[0].metadata_source == "bundled-fixture"
        assert await store.inspect() == ()
        result = await service.generate(request())
        metadata = json.loads(Path(result.metadata_path).read_text())
        assert metadata["request"]["seed"] == 0
        assert metadata["backend_id"] == "fake"
        assert metadata["artifacts"][0]["designation"] == "final"
        assert metadata["artifacts"][0]["metadata"]["fake"] == "true"
        record = (await store.inspect())[0]
        assert (record["state"], record["remote_outcome"]) == ("succeeded", "succeeded")
        assert Path(result.output_paths[0]).read_bytes().startswith(b"\x89PNG")
        assert record["artifacts"] == [*result.output_paths, result.metadata_path]
        with pytest.raises(PersistenceError):
            await service.generate(request())  # A repeated identity is never silently retried.
    finally:
        await backend.aclose()
        await store.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,outcome,state_name",
    [
        ("reject", "not_started", "failed"),
        ("lost", "unknown", "unknown"),
        ("terminal_error", "failed", "failed"),
        ("empty", "unknown", "unknown"),
    ],
)
async def test_failure_evidence_is_independent_from_error_category(state, scenario, outcome, state_name):
    store, backend = SQLiteAttempts(state), FakeBackend(scenario=scenario)
    try:
        await store.open()
        await backend.open()
        with pytest.raises(BackendError):
            await GenerationService(backend, store, state / "outputs").generate(request())
        row = (await store.inspect())[0]
        assert row["state"] == state_name and row["remote_outcome"] == outcome
        assert row["artifacts"] == []
        if scenario == "reject":
            assert row["submission_stage"] == "not_submitted"
    finally:
        await backend.aclose()
        await store.aclose()


@pytest.mark.asyncio
async def test_durable_marker_precedes_backend_call_and_failed_commit_prevents_submission(state, monkeypatch):
    store = SQLiteAttempts(state)
    calls = []

    class CheckingBackend(FakeBackend):
        async def generate(self, req, *, on_event):
            calls.append(req.request_id)
            row = (await store.inspect())[0]
            assert row["state"] == "possibly_submitted" and row["submission_stage"] == "possibly_submitted"
            return await super().generate(req, on_event=on_event)

    backend = CheckingBackend()
    try:
        await store.open()
        await backend.open()
        await GenerationService(backend, store, state / "outputs").generate(request("first"))

        async def refused(*args, **kwargs):
            raise PersistenceError("Fixture commit refusal.")

        monkeypatch.setattr(store, "create", refused)
        with pytest.raises(PersistenceError):
            await GenerationService(backend, store, state / "outputs").generate(request("second"))
        assert calls == ["first"]
    finally:
        await backend.aclose()
        await store.aclose()


@pytest.mark.asyncio
async def test_interruption_and_deadline_preserve_unknown_without_replay(state):
    store, gate = SQLiteAttempts(state), asyncio.Event()
    backend = FakeBackend(gate=gate)
    submitted = asyncio.Event()
    try:
        await store.open()
        await backend.open()
        service = GenerationService(backend, store, state / "outputs")
        task = asyncio.create_task(
            service.generate(
                request("cancel"), on_event=lambda e: submitted.set() if e.kind == "submission_possible" else None
            )
        )
        await asyncio.wait_for(submitted.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = (await store.inspect())[0]
        assert row["state"] == "unknown"
        assert row["failure"]["cancellation"]["local_stopped"]
        assert not row["failure"]["cancellation"]["remote_confirmed"]
        service.timeouts = TimeoutPolicy(total=0.01)
        with pytest.raises(BackendError) as exc:
            await service.generate(request("deadline"))
        assert exc.value.failure.category == ErrorCategory.TIMEOUT
        assert exc.value.failure.remote_outcome == RemoteOutcome.UNKNOWN
        assert backend.active == 0
    finally:
        await backend.aclose()
        await store.aclose()
    restarted = SQLiteAttempts(state)
    try:
        await restarted.open()
        assert all(row["state"] == "unknown" for row in await restarted.inspect())
    finally:
        await restarted.aclose()


@pytest.mark.asyncio
async def test_output_write_failure_preserves_remote_success_and_saved_evidence(state):
    from dtflow.services.artifacts import save_artifacts

    store, backend = SQLiteAttempts(state), FakeBackend()

    def fail_after_saving(*args):
        save_artifacts(*args)
        raise OSError("fixture failure after disk writes")

    try:
        await store.open()
        await backend.open()
        service = GenerationService(backend, store, state / "outputs", saver=fail_after_saving)
        with pytest.raises(BackendError) as exc:
            await service.generate(request())
        assert exc.value.failure.category == ErrorCategory.PERSISTENCE
        row = (await store.inspect())[0]
        assert row["state"] == "save_failed" and row["remote_outcome"] == "succeeded"
        assert list((state / "outputs" / row["attempt_id"]).glob("*.png"))
    finally:
        await backend.aclose()
        await store.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["preview", "truncated", "empty", "bad_status"])
async def test_malformed_backend_success_never_becomes_application_success(state, fault):
    class FaultyBackend(FakeBackend):
        async def generate(self, req, *, on_event):
            result = await super().generate(req, on_event=on_event)
            if fault == "empty":
                return replace(result, artifacts=())
            if fault == "bad_status":
                return replace(result, completion=CompletionEvidence("ERROR", True))
            artifact = result.artifacts[0]
            artifact = (
                replace(artifact, designation=Designation.PREVIEW)
                if fault == "preview"
                else replace(artifact, encoded=artifact.encoded[:-1])
            )
            return replace(result, artifacts=(artifact,))

    store, backend = SQLiteAttempts(state), FaultyBackend()
    try:
        await store.open()
        await backend.open()
        with pytest.raises(BackendError):
            await GenerationService(backend, store, state / "outputs").generate(request())
        assert (await store.inspect())[0]["state"] != "succeeded"
        assert not (state / "outputs").exists()
    finally:
        await backend.aclose()
        await store.aclose()


@pytest.mark.asyncio
async def test_fake_concurrency_is_configurable_and_bounded():
    gate = asyncio.Event()
    backend = FakeBackend(max_concurrency=2, gate=gate)
    await backend.open()
    both = asyncio.Event()
    submitted = []

    def event(value):
        if value.kind == "submission_possible":
            submitted.append(value.request_id)
            if len(submitted) == 2:
                both.set()

    tasks = [asyncio.create_task(backend.generate(request(str(i)), on_event=event)) for i in range(3)]
    try:
        await asyncio.wait_for(both.wait(), 1)
        assert backend.active == backend.peak_active == 2 and len(submitted) == 2
        gate.set()
        results = await asyncio.gather(*tasks)
        assert len(results) == 3 and len(submitted) == 3 and backend.peak_active == 2
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await backend.aclose()

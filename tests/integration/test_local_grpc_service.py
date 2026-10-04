"""One real end-to-end path: GenerationService + DrawThingsBackend + scripted server + SQLite attempts."""

import pytest
from support.dt_server import Drop, Image, Signpost, pixel_tensor, scripted_server
from support.network_guard import endpoint
from test_local_grpc_connection import UNREACHABLE_FAILURES, opened
from test_local_grpc_generate import ECHO, SIZE, make_request

from dtflow.core.models import BackendError, TimeoutPolicy
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.generation import GenerationService


async def service_run(tmp_path, steps, request):
    store = SQLiteAttempts(tmp_path / "state")
    await store.open()
    try:
        async with scripted_server(steps, ECHO) as server:
            timeouts = TimeoutPolicy(connect=1.0, total=5.0)
            adapter = await opened(server.host, server.port, timeouts=timeouts)
            try:
                service = GenerationService(adapter, store, tmp_path / "out", timeouts=timeouts)
                await service.get_inventory(refresh=True)
                try:
                    saved, error = await service.generate(request), None
                except BackendError as exc:
                    saved, error = None, exc
                (attempt,) = await store.inspect()
                return saved, error, attempt, server.seen
            finally:
                await adapter.aclose()
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_success_is_saved_with_durable_state(tmp_path):
    saved, error, attempt, seen = await service_run(
        tmp_path, (Signpost(), Image(pixel_tensor(SIZE, SIZE))), make_request(seed=0)
    )
    assert error is None and attempt["state"] == "succeeded" and attempt["remote_outcome"] == "succeeded"
    assert saved.output_paths and open(saved.output_paths[0], "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    assert seen.generate_requests[0].prompt == "a prompt"


@pytest.mark.asyncio
async def test_pre_submission_failure_is_recorded_as_not_started(tmp_path):
    store = SQLiteAttempts(tmp_path / "state")
    await store.open()
    try:
        async with scripted_server((), ECHO) as server:
            host, port = server.host, server.port
            timeouts = TimeoutPolicy(connect=1.0, total=5.0)
            adapter = await opened(host, port, timeouts=timeouts)
            service = GenerationService(adapter, store, tmp_path / "out", timeouts=timeouts)
            await service.get_inventory(refresh=True)
        try:
            with endpoint(host, port), pytest.raises(BackendError) as caught:  # the server is gone: connection refused
                await service.generate(make_request())
        finally:
            await adapter.aclose()
        (attempt,) = await store.inspect()
        failure = caught.value.failure
        assert (failure.category, failure.code) in UNREACHABLE_FAILURES  # refused, or a connect deadline on Windows
        assert not (tmp_path / "out").exists()  # no outputs
        assert attempt["state"] == "failed" and attempt["remote_outcome"] == "not_started"
        assert attempt["submission_stage"] == "not_submitted"
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_disconnect_after_dispatch_is_recorded_unknown(tmp_path):
    _, error, attempt, _ = await service_run(tmp_path, (Signpost(), Drop()), make_request())
    assert error.failure.code == "connection_lost"
    assert attempt["state"] == "unknown" and attempt["remote_outcome"] == "unknown"

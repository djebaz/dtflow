"""Five cloud acceptance groups; HTTP mocks compose with the existing TLS/gRPC server."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest
from support.dt_server import Drop, Image, Signpost, client_context, pixel_tensor, scripted_server
from test_local_grpc_connection import Secret
from test_local_grpc_generate import ECHO, SIZE, make_request

from dtflow.backends.drawthings.backend import DrawThingsEndpoint
from dtflow.backends.drawthings.cloud import DirectCloudBackend
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.backends.drawthings.tls import TlsPolicy
from dtflow.core.models import BackendError, TimeoutPolicy, ValidationError
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services.generation import GenerationService


@asynccontextmanager
async def cloud(server, handler):
    auth = CloudAuth(Secret("FAKE-API-SECRET"), transport=httpx.MockTransport(handler))
    adapter = DirectCloudBackend(
        auth,
        endpoint=DrawThingsEndpoint(server.host, server.port, TlsPolicy(context=client_context())),
        timeouts=TimeoutPolicy(connect=1.0, total=5.0),
    )
    await adapter.open()
    try:
        yield adapter
    finally:
        await adapter.aclose()


def test_cloud_accepts_ten_lanes_without_network():
    auth = CloudAuth(Secret("FAKE-API-SECRET"), transport=httpx.MockTransport(success))
    assert DirectCloudBackend(auth, max_concurrency=10).capabilities.max_concurrency == 10
    with pytest.raises(ValidationError, match="1..10"):
        DirectCloudBackend(auth, max_concurrency=11)


def success(request):
    if request.url.path == "/sdk/token":
        return httpx.Response(200, json={"shortTermToken": "FAKE-SHORT-TOKEN", "expiresIn": 3600})
    return httpx.Response(200, json={"gRPCToken": "FAKE-GRPC-TOKEN"})


@pytest.mark.asyncio
async def test_success_health_inventory_and_per_generation_authorization(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return success(request)

    store = SQLiteAttempts(tmp_path / "state")
    await store.open()
    try:
        async with scripted_server((Signpost(), Image(pixel_tensor(SIZE, SIZE))), ECHO) as server:
            async with cloud(server, respond) as adapter:
                status = await adapter.check_connection()
                assert (status.transport, status.authentication, status.inventory) == ("ok", "ok", "ok")
                inventory = await adapter.get_inventory(refresh=True)
                assert inventory.backend_id == adapter.backend_id == "drawthings-cloud"
                assert [r.url.path for r in calls] == ["/sdk/token"]
                assert not server.seen.generate_requests
                service = GenerationService(adapter, store, tmp_path / "out")
                for index in range(2):
                    saved = await service.generate(replace(make_request(seed=0), request_id=f"cloud-{index}"))
                    assert saved.backend_result.backend_id == "drawthings-cloud"
                    assert saved.backend_result.completion.terminal_status == "OK"
                    assert saved.output_paths
                assert adapter.capabilities.max_concurrency == 4
                assert [r.url.path for r in calls] == ["/sdk/token", "/authenticate", "/authenticate"]
                assert "authorization" not in calls[0].headers
                assert json.loads(calls[0].content) == {
                    "apiKey": "FAKE-API-SECRET",
                    "appCheckType": "none",
                    "appCheckToken": None,
                }
                for auth, wire, metadata in zip(
                    calls[1:], server.seen.generate_requests, server.seen.metadata, strict=True
                ):
                    payload = json.loads(auth.content)
                    assert set(payload) == {"blob", "fromBridge", "attestationSupported", "isSandbox"}
                    assert (payload["fromBridge"], payload["attestationSupported"], payload["isSandbox"]) == (
                        True,
                        False,
                        False,
                    )
                    wire.contents = []
                    assert base64.b64decode(payload["blob"]) == bytes(wire)
                    assert auth.headers["authorization"] == "FAKE-SHORT-TOKEN"
                    assert auth.headers["content-type"] == "application/json"
                    assert metadata["authorization"] == "bearer FAKE-GRPC-TOKEN"
                assert all(attempt["state"] == "succeeded" for attempt in await store.inspect())
    finally:
        await store.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["token", "authorization", "after_send"])
async def test_failure_boundary_no_retry_and_sanitization(failure_at, caplog):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        fail = "/sdk/token" if failure_at == "token" else "/authenticate"
        if failure_at != "after_send" and request.url.path == fail:
            return httpx.Response(403, text="FAKE-API-SECRET FAKE-SHORT-TOKEN FAKE-GRPC-TOKEN")
        return success(request)

    async with scripted_server((Drop(),), ECHO) as server:
        async with cloud(server, respond) as adapter:
            if failure_at == "token":
                status = await adapter.check_connection()
                assert status.authentication == "failed" and status.transport == "unknown"
                assert not server.seen.echo_requests and not server.seen.generate_requests
                assert calls == ["/sdk/token"]
                public = repr(status)
            else:
                await adapter.get_inventory(refresh=True)
                with pytest.raises(BackendError) as caught:
                    await adapter.generate(make_request(), on_event=lambda _: None)
                problem = caught.value.failure
                public = repr(problem) + str(caught.value)
                assert problem.remote_outcome == ("unknown" if failure_at == "after_send" else "not_started")
                assert problem.submission_stage == (
                    "possibly_submitted" if failure_at == "after_send" else "not_submitted"
                )
                assert len(server.seen.generate_requests) == (1 if failure_at == "after_send" else 0)
                assert calls == ["/sdk/token", "/authenticate"]
            for secret in ("FAKE-API-SECRET", "FAKE-SHORT-TOKEN", "FAKE-GRPC-TOKEN"):
                assert secret not in public + caplog.text


@pytest.mark.asyncio
async def test_cancelled_authorization_late_response_cannot_dispatch():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/authenticate":
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()  # deliberately ill-behaved transport; late response must not revive dispatch
        return success(request)

    async with scripted_server((Image(pixel_tensor(SIZE, SIZE)),), ECHO) as server:
        async with cloud(server, respond) as adapter:
            await adapter.get_inventory(refresh=True)
            events = []
            task = asyncio.create_task(adapter.generate(make_request(), on_event=events.append))
            await entered.wait()
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not server.seen.generate_requests
            assert "submission_possible" not in [event.kind for event in events]
            assert calls == ["/sdk/token", "/authenticate"]

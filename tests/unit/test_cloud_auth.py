"""Cloud-only token lifetime, parser and HTTP ownership boundaries (no gRPC duplication)."""

import asyncio
import json

import httpx
import pytest

from dtflow.backends.drawthings.cloud_auth import CloudAuth, CloudAuthPolicy, parse_response
from dtflow.core.models import BackendError, Credential, ErrorCategory


class Keys:
    def __init__(self, value="FAKE-API-SECRET"):
        self.value, self.reads = value, 0

    async def get(self):
        self.reads += 1
        return Credential("fixture", self.value) if self.value is not None else None


@pytest.mark.parametrize(
    "body,exchange",
    [
        (b"not JSON", True),
        (b"[]", True),
        (b"{}", False),
        (b'{"gRPCToken": ""}', False),
        (b'{"gRPCToken": 7}', False),
        (b'{"shortTermToken":"x","expiresIn":true}', True),
        (b'{"shortTermToken":"x","expiresIn":0}', True),
        (b'{"shortTermToken":"x","expiresIn":"3600"}', True),
        (b'{"shortTermToken":"x"}', True),
        (b'{"gRPCToken":"x\\r\\nheader"}', False),
    ],
)
def test_invalid_success_fields_are_sanitized(body, exchange):
    with pytest.raises(BackendError) as caught:
        parse_response(body, token_exchange=exchange)
    assert caught.value.failure.code == "cloud_response_invalid"


@pytest.mark.asyncio
async def test_reuse_expiry_single_refresh_cancelled_waiter_and_immutable_credential():
    now = [0.0]
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    keys = Keys()

    async def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 2:
            entered.set()
            await release.wait()
        if len(calls) == 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"shortTermToken": f"token-{len(calls)}", "expiresIn": 100})

    auth = CloudAuth(keys, transport=httpx.MockTransport(respond), clock=lambda: now[0])
    try:
        assert await auth.token() == "token-1"
        now[0] = 49
        assert await auth.token() == "token-1"  # half-life margin; no expiry sleeps
        now[0] = 51
        cancelled = asyncio.create_task(auth.token())
        await entered.wait()
        other = asyncio.create_task(auth.token())
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        keys.value = "CHANGED-KEY-MUST-NOT-BE-READ"
        release.set()
        assert await other == "token-2"
        assert await auth.token() == "token-2"
        assert len(calls) == 2 and keys.reads == 1
        assert calls[0] == calls[1]
        now[0] = 200  # expired token; failed refresh is not silently retried
        with pytest.raises(BackendError):
            await auth.token()
        assert len(calls) == 3
        assert await auth.token() == "token-4"  # a new explicit call may refresh
        assert keys.reads == 1
    finally:
        await auth.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["rejection", "oversize", "timeout", "network", "expired", "missing", "total_deadline"]
)
async def test_http_failures_and_invalid_credentials_never_cache_or_retry(kind, caplog):
    calls = []
    now = [0.0]

    async def respond(request):
        calls.append(request)
        if kind == "total_deadline":
            await asyncio.Event().wait()
        if kind == "timeout":
            raise httpx.ReadTimeout("FAKE-SECRET", request=request)
        if kind == "network":
            raise httpx.ConnectError("FAKE-SECRET", request=request)
        if kind == "expired":
            now[0] = 101
        if kind == "rejection":
            return httpx.Response(403, text="FAKE-SECRET")
        if kind == "oversize":
            return httpx.Response(200, content=b"x" * 129)
        return httpx.Response(200, json={"shortTermToken": "FAKE-SECRET", "expiresIn": 100})

    auth = CloudAuth(
        Keys(None if kind == "missing" else "FAKE-SECRET"),
        transport=httpx.MockTransport(respond),
        policy=CloudAuthPolicy(response_bytes=128, total=0.02 if kind == "total_deadline" else 30.0),
        clock=lambda: now[0],
    )
    try:
        with pytest.raises(BackendError) as caught:
            await auth.token()
        assert caught.value.failure.remote_outcome == "not_started"
        assert "FAKE-SECRET" not in repr(caught.value.failure) + str(caught.value) + caplog.text
        assert len(calls) == (0 if kind == "missing" else 1)
        assert auth._token is None
    finally:
        await auth.aclose()


@pytest.mark.asyncio
async def test_shutdown_cancels_owned_refresh_and_closes_client(monkeypatch):
    for slow in (False, True):
        entered, stopped, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def respond(request, entered=entered, stopped=stopped, slow=slow, release=release):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stopped.set()
                if slow:
                    await release.wait()
                raise

        auth = CloudAuth(Keys(), transport=httpx.MockTransport(respond))
        waiter = asyncio.create_task(auth.token())
        await entered.wait()
        try:
            if slow:

                async def expired_wait(tasks, *, timeout, auth=auth, stopped=stopped):
                    assert tasks == (auth._refresh,) and timeout == auth._policy.cleanup
                    await stopped.wait()
                    return set(), set(tasks)  # controlled expiry; no wall-clock wait

                with monkeypatch.context() as patch:
                    patch.setattr(asyncio, "wait", expired_wait)
                    with pytest.raises(TimeoutError):
                        await auth.aclose()
            else:
                await auth.aclose()
            assert stopped.is_set() and auth._client.is_closed
            await auth.aclose()  # a repeated close does not restart cleanup or authentication
            with pytest.raises(BackendError) as caught:
                await auth.token()
            assert caught.value.failure.category == ErrorCategory.CONNECTIVITY
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await waiter

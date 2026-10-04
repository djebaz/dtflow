"""Scoped DT+ authentication; tokens stay in memory and errors never carry response bodies."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from dtflow.backends.drawthings.errors import diagnostics, error
from dtflow.core.models import BackendError, CredentialProvider, ErrorCategory, ValidationError


@dataclass(frozen=True, slots=True)
class CloudAuthPolicy:
    total: float = 30.0
    refresh_margin: float = 300.0
    response_bytes: int = 1024 * 1024
    cleanup: float = 5.0

    def __post_init__(self):
        import math

        if any(not math.isfinite(x) or x <= 0 for x in (self.total, self.cleanup)):
            raise ValidationError("HTTP deadlines must be positive and finite.")
        if not math.isfinite(self.refresh_margin) or self.refresh_margin < 0:
            raise ValidationError("Refresh margin must be nonnegative and finite.")
        if type(self.response_bytes) is not int or self.response_bytes <= 0:
            raise ValidationError("HTTP response limit must be a positive integer.")


def parse_response(body: bytes, *, token_exchange: bool) -> tuple[str, int | None]:
    """Validate just the documented success fields, without exposing malformed body contents."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        data = None
    field = "shortTermToken" if token_exchange else "gRPCToken"
    token = data.get(field) if isinstance(data, dict) else None
    lifetime = data.get("expiresIn") if isinstance(data, dict) and token_exchange else None
    if (
        not isinstance(token, str)
        or not token.strip()
        or any(ord(char) < 32 or ord(char) > 126 for char in token)
        or (token_exchange and (type(lifetime) is not int or lifetime <= 0))
    ):
        raise error(ErrorCategory.PROTOCOL, "cloud_response_invalid", "Cloud authentication response is invalid.")
    return token, lifetime


class CloudAuth:
    """One immutable credential/origin scope, one shared short-token refresh, no generation-token cache.

    Cancelling a refresh waiter leaves the owned refresh alive. ``aclose`` owns its final cancellation and closes
    the HTTP pool. HTTP authentication is not evidence that a generation RPC was submitted.
    """

    def __init__(
        self,
        credentials: CredentialProvider,
        *,
        origin: str = "https://api.drawthings.ai",
        transport: httpx.AsyncBaseTransport | None = None,
        policy: CloudAuthPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        url = httpx.URL(origin)
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValidationError("Cloud API origin must be a credential-free HTTPS origin.")
        self._credentials = credentials
        self._origin = str(url).rstrip("/")
        self._policy = policy or CloudAuthPolicy()
        self._clock = clock
        self._client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0),
        )
        self._key: str | None = None
        self._token: str | None = None
        self._expires = 0.0
        self._margin = 0.0
        self._lock = asyncio.Lock()
        self._refresh: asyncio.Task[str] | None = None
        self._closed = False

    async def token(self) -> str:
        if self._closed:
            raise error(ErrorCategory.CONNECTIVITY, "cloud_auth_closed", "Cloud authentication client is closed.")
        async with self._lock:
            if self._token is not None and self._expires - self._clock() > self._margin:
                return self._token
            if self._refresh is None or self._refresh.done():
                self._refresh = asyncio.create_task(self._exchange())
                # Retrieve abandoned-waiter failures without logging tokens or response details.
                self._refresh.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            refresh = self._refresh
        return await asyncio.shield(refresh)

    async def _exchange(self) -> str:
        # The credential is resolved only once, inside the shared refresh task.
        async with asyncio.timeout(self._policy.total):
            if self._key is None:
                credential = await self._credentials.get()
                if credential is None or not credential.secret.strip():
                    raise error(ErrorCategory.AUTHENTICATION, "missing_cloud_credentials", "Cloud API key is missing.")
                self._key = credential.secret.strip()
            started = self._clock()
            body = await self._post("/sdk/token", {"apiKey": self._key, "appCheckType": "none", "appCheckToken": None})
            token, lifetime = parse_response(body, token_exchange=True)
            expires = started + lifetime
            if expires <= self._clock():
                raise error(ErrorCategory.AUTHENTICATION, "cloud_token_expired", "Cloud token arrived expired.")
            if self._closed:
                raise error(ErrorCategory.CONNECTIVITY, "cloud_auth_closed", "Cloud authentication client is closed.")
            self._token, self._expires = token, expires
            self._margin = min(self._policy.refresh_margin, lifetime / 2)
            return token

    async def authorize(self, message) -> str:
        token = await self.token()
        auth_message = copy.deepcopy(message)
        auth_message.contents = []
        body = await self._post(
            "/authenticate",
            {
                "blob": base64.b64encode(bytes(auth_message)).decode("ascii"),
                "fromBridge": True,
                "attestationSupported": False,
                "isSandbox": False,
            },
            token=token,
        )
        return parse_response(body, token_exchange=False)[0]

    async def _post(self, path: str, payload: dict, *, token: str | None = None) -> bytes:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = token
        try:
            async with asyncio.timeout(self._policy.total):
                async with self._client.stream("POST", self._origin + path, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        category = (
                            ErrorCategory.AUTHENTICATION
                            if response.status_code in (401, 403)
                            else ErrorCategory.REMOTE_REJECTION
                        )
                        raise error(
                            category,
                            "cloud_http_rejected",
                            "Cloud authentication HTTP request was rejected.",
                            diagnostics=diagnostics(http_status=response.status_code, operation=path),
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self._policy.response_bytes:
                            raise error(
                                ErrorCategory.PROTOCOL,
                                "cloud_response_limit",
                                "Cloud authentication response exceeds its limit.",
                            )
                        body.extend(chunk)
                    return bytes(body)
        except (TimeoutError, httpx.TimeoutException):
            raise error(
                ErrorCategory.TIMEOUT,
                "cloud_http_deadline",
                "Cloud authentication deadline exceeded.",
                timeout_phase="authentication",
            ) from None
        except httpx.HTTPError:
            raise error(
                ErrorCategory.CONNECTIVITY, "cloud_http_failed", "Cloud authentication HTTP connection failed."
            ) from None

    async def aclose(self) -> None:
        self._closed = True
        self._token = self._key = None
        async with asyncio.timeout(self._policy.cleanup):
            if self._refresh is not None and not self._refresh.done():
                self._refresh.cancel()
                try:
                    await self._refresh
                except (asyncio.CancelledError, BackendError):
                    pass
            await self._client.aclose()

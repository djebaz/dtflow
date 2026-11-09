"""Scoped DT+ authentication; tokens stay in memory, errors retain only safe response projections."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from dtflow.backends.drawthings.errors import api_error, diagnostics, error
from dtflow.core.models import BackendError, CredentialProvider, ErrorCategory, ValidationError
from dtflow.diagnostics import api_error_data, cloud_key_data, event
from dtflow.services.cloud_usage import key_identity

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _HTTPReply:
    body: bytes = field(repr=False)
    headers: tuple[tuple[str, str], ...]
    elapsed_ms: float


class CloudJSONResponse(dict):
    """Adapter-local JSON plus a bounded, already-redacted projection for schema failures."""

    def __init__(self, data: dict, error_data: dict):
        super().__init__(data)
        self.error_data = error_data


@dataclass(frozen=True, slots=True)
class CloudAuthPolicy:
    total: float = 30.0
    refresh_margin: float = 300.0
    response_bytes: int = 1024 * 1024
    cleanup: float = 5.0

    def __post_init__(self):
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
    except (ValueError, UnicodeError, RecursionError):
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
        self._identity: tuple[str, str | None] | None = None
        self._source: str | None = None
        self._token: str | None = None
        self._expires = 0.0
        self._margin = 0.0
        self._lock = asyncio.Lock()
        self._refresh: asyncio.Task[str] | None = None
        self._closed = False
        self._scope = uuid.uuid4().hex

    @property
    def origin(self) -> str:
        return self._origin

    @property
    def scope(self) -> str:
        """Opaque identity of this client's credential/origin scope; carries nothing derived from the credential."""
        return self._scope

    @property
    def key_identity(self) -> tuple[str, str | None] | None:
        """Opaque credential identity and optional four-character hint once the key was resolved; never the key."""
        return self._identity

    @property
    def key_source(self) -> str | None:
        """Source pinned when this client resolved the key; never follows later UI selection."""
        return self._source

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
        token = await asyncio.shield(refresh)
        return token

    async def _exchange(self) -> str:
        try:
            async with asyncio.timeout(self._policy.total):
                return await self._load_token()
        except TimeoutError:
            raise api_error(
                ErrorCategory.TIMEOUT,
                "cloud_http_deadline",
                "Cloud authentication deadline exceeded.",
                timeout_phase="authentication",
                operation="/sdk/token",
                transport="http",
            ) from None

    async def _load_token(self) -> str:
        # The credential is resolved only once, inside the shared refresh task.
        if self._key is None:
            credential = await self._credentials.get()
            if credential is None or not credential.secret.strip():
                raise error(ErrorCategory.AUTHENTICATION, "missing_cloud_credentials", "Cloud API key is missing.")
            self._key = credential.secret.strip()
            self._identity = key_identity(self._key)
            self._source = credential.source
        started = self._clock()
        reply = await self._post("/sdk/token", {"apiKey": self._key, "appCheckType": "none", "appCheckToken": None})
        try:
            token, lifetime = parse_response(reply.body, token_exchange=True)
        except BackendError as exc:
            raise self._invalid_reply("/sdk/token", reply, exc.failure.code) from None
        expires = started + lifetime
        if expires <= self._clock():
            raise api_error(
                ErrorCategory.AUTHENTICATION,
                "cloud_token_expired",
                "Cloud token arrived expired.",
                operation="/sdk/token",
                transport="http",
                elapsed_ms=reply.elapsed_ms,
                diagnostics=diagnostics(http_status=200),
                response=self._response_data(reply),
            )
        if self._closed:
            raise error(ErrorCategory.CONNECTIVITY, "cloud_auth_closed", "Cloud authentication client is closed.")
        self._token, self._expires = token, expires
        self._margin = min(self._policy.refresh_margin, lifetime / 2)
        return token

    async def authorize(self, message) -> str:
        token = await self.token()
        auth_message = copy.deepcopy(message)
        auth_message.contents = []
        reply = await self._post(
            "/authenticate",
            {
                "blob": base64.b64encode(bytes(auth_message)).decode("ascii"),
                "fromBridge": True,
                "attestationSupported": False,
                "isSandbox": False,
            },
            token=token,
            private=(message.prompt, message.negative_prompt),
        )
        if self._closed:
            raise error(ErrorCategory.CONNECTIVITY, "cloud_auth_closed", "Cloud authentication client is closed.")
        try:
            return parse_response(reply.body, token_exchange=False)[0]
        except BackendError as exc:
            raise self._invalid_reply(
                "/authenticate", reply, exc.failure.code, private=(message.prompt, message.negative_prompt)
            ) from None

    async def authenticated_json(self, path: str, payload: dict) -> dict:
        """POST authenticated JSON and return a validated object without exposing response contents."""
        token = await self.token()
        private = tuple(
            item[key]
            for item in payload.get("files", ())
            if isinstance(item, dict)
            for key in ("file", "sha256")
            if isinstance(item.get(key), str)
        )
        reply = await self._post(path, payload, token=token, private=private)
        try:
            decoded = json.loads(reply.body)
        except (ValueError, UnicodeError, RecursionError):
            raise self._invalid_reply(path, reply, "cloud_response_invalid", private=private) from None
        if not isinstance(decoded, dict):
            raise self._invalid_reply(path, reply, "cloud_response_invalid", private=private)
        data = decoded.get("data")
        already = data.get("alreadyExists") if isinstance(data, dict) else None
        if isinstance(already, dict):
            private += tuple(value for value in already.values() if isinstance(value, str))
        return CloudJSONResponse(
            decoded, {**self._response_data(reply, private=private), "elapsed_ms": round(reply.elapsed_ms)}
        )

    def _response_data(self, reply: _HTTPReply, *, private=()) -> dict:
        return api_error_data(body=reply.body, headers=reply.headers, private=(*private, self._key, self._token))

    def _invalid_reply(self, path: str, reply: _HTTPReply, code: str, *, private=()) -> BackendError:
        return api_error(
            ErrorCategory.PROTOCOL,
            code,
            "Cloud response is invalid.",
            operation=path,
            transport="http",
            elapsed_ms=reply.elapsed_ms,
            response=self._response_data(reply, private=private),
            diagnostics=diagnostics(http_status=200),
        )

    async def _post(self, path: str, payload: dict, *, token: str | None = None, private=()) -> _HTTPReply:
        if self._closed:
            raise error(ErrorCategory.CONNECTIVITY, "cloud_auth_closed", "Cloud authentication client is closed.")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = token
        started = self._clock()
        observed_status = None
        observed_headers = ()

        def elapsed():
            return (self._clock() - started) * 1000

        try:
            async with asyncio.timeout(self._policy.total):
                async with self._client.stream("POST", self._origin + path, json=payload, headers=headers) as response:
                    observed_status = response.status_code
                    observed_headers = tuple(response.headers.items())
                    if response.status_code != 200:
                        body = bytearray()
                        truncated = False
                        try:
                            async for chunk in response.aiter_bytes():
                                remaining = 8192 - len(body)
                                body.extend(chunk[:remaining])
                                if len(chunk) > remaining:
                                    truncated = True
                                    break
                        except (httpx.HTTPError, TimeoutError):
                            truncated = True  # Preserve observed HTTP status even if the error body fails.
                        category = (
                            ErrorCategory.AUTHENTICATION
                            if response.status_code in (401, 403)
                            else ErrorCategory.REMOTE_REJECTION
                        )
                        raise api_error(
                            category,
                            "cloud_http_rejected",
                            "Cloud authentication HTTP request was rejected.",
                            diagnostics=diagnostics(http_status=response.status_code, operation=path),
                            operation=path,
                            transport="http",
                            elapsed_ms=elapsed(),
                            response=api_error_data(
                                body=bytes(body),
                                headers=response.headers,
                                private=(*private, self._key, token),
                                truncated=truncated,
                            ),
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self._policy.response_bytes:
                            raise api_error(
                                ErrorCategory.PROTOCOL,
                                "cloud_response_limit",
                                "Cloud authentication response exceeds its limit.",
                                operation=path,
                                transport="http",
                                elapsed_ms=elapsed(),
                                diagnostics=diagnostics(http_status=200),
                                response=api_error_data(headers=response.headers, truncated=True),
                            )
                        body.extend(chunk)
                    event(
                        log,
                        "backend.api_completed",
                        level=logging.DEBUG,
                        data={
                            "transport": "http",
                            "operation": path.strip("/").replace("/", "."),
                            "http_status": 200,
                            "elapsed_ms": elapsed(),
                            **cloud_key_data(self._identity, self._source),
                        },
                    )
                    return _HTTPReply(bytes(body), tuple(response.headers.items()), elapsed())
        except (TimeoutError, httpx.TimeoutException):
            raise api_error(
                ErrorCategory.TIMEOUT,
                "cloud_http_deadline",
                "Cloud authentication deadline exceeded.",
                timeout_phase="authentication",
                operation=path,
                transport="http",
                elapsed_ms=elapsed(),
                diagnostics=diagnostics(**({"http_status": observed_status} if observed_status is not None else {})),
                response=api_error_data(headers=observed_headers, truncated=observed_status is not None),
            ) from None
        except httpx.HTTPError as exc:
            raise api_error(
                ErrorCategory.CONNECTIVITY,
                "cloud_http_failed",
                "Cloud authentication HTTP connection failed.",
                operation=path,
                transport="http",
                elapsed_ms=elapsed(),
                diagnostics=diagnostics(
                    kind=type(exc).__name__, **({"http_status": observed_status} if observed_status is not None else {})
                ),
                response=api_error_data(headers=observed_headers, truncated=observed_status is not None),
            ) from None

    async def aclose(self) -> None:
        if self._closed and self._client.is_closed:
            return
        self._closed = True
        self._token = self._key = None
        try:
            if self._refresh is not None and not self._refresh.done():
                self._refresh.cancel()
                # Unlike awaiting a cancelled task, wait has a bound even if it suppresses cancellation.
                done, _ = await asyncio.wait((self._refresh,), timeout=self._policy.cleanup)
                if not done:
                    raise TimeoutError("Cloud token refresh cleanup deadline exceeded.")
        finally:
            # Refresh cleanup cannot consume the HTTP pool's separate shutdown budget.
            async with asyncio.timeout(self._policy.cleanup):
                await self._client.aclose()

"""Draw Things gRPC backend: connection, inventory and lifecycle (generation is added in the next change)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from drawthings_py.generated.dt_grpc import image_service
from grpclib.client import Channel
from grpclib.config import Configuration

from dtflow.backends.drawthings.errors import diagnostics, error, from_exception
from dtflow.backends.drawthings.inventory import build_snapshot, eligible_model_ids
from dtflow.backends.drawthings.tls import TlsPolicy
from dtflow.backends.drawthings.wire import CLIENT_NAME
from dtflow.core.models import (
    BackendError,
    Capabilities,
    ConnectionStatus,
    CredentialProvider,
    ErrorCategory,
    InventorySnapshot,
    ResourceLimits,
    TimeoutPolicy,
    ValidationError,
)

BACKEND_ID = "drawthings-grpc"


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class DrawThingsEndpoint:
    host: str
    port: int
    tls: TlsPolicy = field(default_factory=TlsPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValidationError("Endpoint host is required.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValidationError("Endpoint port must be in 1..65535.")

    @property
    def label(self) -> str:
        """Credential-free endpoint identity."""
        return f"grpcs://{self.host}:{self.port}"


class DrawThingsBackend:
    """One channel to one Draw Things server; strict TLS, no silent fallback of any kind."""

    def __init__(
        self,
        endpoint: DrawThingsEndpoint,
        *,
        timeouts: TimeoutPolicy | None = None,
        limits: ResourceLimits | None = None,
        shared_secret: CredentialProvider | None = None,
    ):
        self.endpoint = endpoint
        self.timeouts = timeouts or TimeoutPolicy()
        self.limits = limits or ResourceLimits()
        self._shared_secret = shared_secret
        self._channel: Channel | None = None
        self._closing = False
        self._closed = False
        self._active = 0
        self._inventory: InventorySnapshot | None = None
        self._stale_reason: str | None = None

    # -- contract ------------------------------------------------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        """Model identifiers come from the last inventory; before any inventory no model is accepted."""
        model_ids = eligible_model_ids(self._inventory) if self._inventory is not None else ()
        return Capabilities(
            BACKEND_ID, model_ids=model_ids, max_concurrency=1, batch_size=1, batch_count=1, limits=self.limits
        )

    async def open(self) -> None:
        """Create the channel; the connection itself is established lazily and bounded by the connect deadline."""
        if self._closing or self._closed:
            raise error(ErrorCategory.CONNECTIVITY, "backend_closed", "Backend is closed.")
        if self._channel is None:
            tls = self.endpoint.tls
            config = Configuration(ssl_target_name_override=tls.server_name) if tls.server_name else None
            self._channel = Channel(self.endpoint.host, self.endpoint.port, ssl=tls.build_context(), config=config)

    async def check_connection(self) -> ConnectionStatus:
        """Non-generative evidence; transport, authentication and inventory stay distinct."""
        checked = now()
        base = {"endpoint": self.endpoint.label}

        def status(transport, authentication, inventory, **diag):
            return ConnectionStatus(
                BACKEND_ID,
                self.endpoint.label,
                checked,
                transport,
                authentication,
                inventory,
                diagnostics(**diag),
            )

        try:
            await self._connect()
        except BackendError as exc:
            return status("failed", "unknown", "unknown", transport_code=exc.failure.code)
        try:
            reply, secret_sent = await self._echo()
        except BackendError as exc:
            if exc.failure.category == ErrorCategory.AUTHENTICATION:
                return status("ok", "failed", "unknown", authentication_code=exc.failure.code)
            return status("failed", "unknown", "unknown", transport_code=exc.failure.code)
        if reply.shared_secret_missing:
            return status(
                "ok",
                "failed",
                "unknown",
                shared_secret="rejected_or_missing" if secret_sent else "required_not_configured",
            )
        snapshot = build_snapshot(BACKEND_ID, reply, observed_at_utc=checked, refreshed=True, secret_sent=secret_sent)
        return status("ok", "ok" if secret_sent else "not_applicable", "ok", entries=len(snapshot.entries), **base)

    async def get_inventory(self, refresh: bool = False) -> InventorySnapshot:
        """Return the cached snapshot or fetch a fresh one.

        A failed refresh raises and leaves the previous snapshot flagged stale (never presented as fresh); a failed
        authentication clears it, because the server no longer accepts our identity.
        """
        if self._inventory is not None and not refresh:
            return self._with_staleness(self._inventory)
        try:
            await self._connect()
            reply, secret_sent = await self._echo()
        except BackendError as exc:
            if exc.failure.category == ErrorCategory.AUTHENTICATION:
                self._inventory = self._stale_reason = None
            elif self._inventory is not None:
                self._stale_reason = exc.failure.code
            raise
        if reply.shared_secret_missing:
            self._inventory = self._stale_reason = None
            raise error(
                ErrorCategory.AUTHENTICATION,
                "shared_secret_rejected_or_missing",
                "Server requires a shared secret that was not accepted.",
                diagnostics=diagnostics(shared_secret="sent" if secret_sent else "not_configured"),
            )
        self._inventory = build_snapshot(
            BACKEND_ID, reply, observed_at_utc=now(), refreshed=True, secret_sent=secret_sent
        )
        self._stale_reason = None
        return self._inventory

    async def aclose(self) -> None:
        """Stop admission, refuse to close under an active generation, then close the channel."""
        self._closing = True
        if self._active:
            raise RuntimeError("Cancel and await owned generations before closing the backend.")
        if self._channel is not None:
            channel, self._channel = self._channel, None
            channel.close()
        self._closed = True

    # -- internals -----------------------------------------------------------------------------------------------

    def _with_staleness(self, snapshot: InventorySnapshot) -> InventorySnapshot:
        refreshed = replace(snapshot, refreshed=False)
        if self._stale_reason is None:
            return refreshed
        return replace(
            refreshed, diagnostics=(*refreshed.diagnostics, ("stale", "true"), ("stale_reason", self._stale_reason))
        )

    def _require_channel(self) -> Channel:
        if self._closing or self._closed or self._channel is None:
            raise error(ErrorCategory.CONNECTIVITY, "backend_closed", "Backend is closed or not open.")
        return self._channel

    async def _secret(self) -> str | None:
        if self._shared_secret is None:
            return None
        credential = await self._shared_secret.get()
        return credential.secret if credential is not None else None

    async def _connect(self) -> None:
        """Establish transport (including TLS) so failures here are provably before any request bytes."""
        channel = self._require_channel()
        try:
            async with asyncio.timeout(self.timeouts.connect):
                await channel.__connect__()
        except Exception as exc:
            raise BackendError(from_exception(exc, phase="connect")) from exc

    async def _echo(self):
        secret = await self._secret()
        request = image_service.EchoRequest(name=CLIENT_NAME)
        if secret:
            request.shared_secret = secret
        stub = image_service.ImageGenerationServiceStub(self._require_channel())
        try:
            reply = await stub.echo(request, timeout=self.timeouts.inventory)
        except Exception as exc:
            raise BackendError(from_exception(exc, phase="inventory")) from exc
        return reply, bool(secret)

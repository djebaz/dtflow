"""Direct DT+ backend: cloud authentication around the shared Draw Things transport path."""

from dataclasses import replace
from pathlib import Path

import httpx

from dtflow.backends.drawthings.backend import DrawThingsBackend, DrawThingsEndpoint, now
from dtflow.backends.drawthings.cloud_assets import (
    AssetUploadPolicy,
    UploadTicket,
    lookup_lora_assets,
    negotiate_lora_asset,
    negotiate_lora_assets,
    upload_signed,
)
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.backends.drawthings.errors import diagnostics
from dtflow.backends.drawthings.tls import TlsPolicy
from dtflow.core.models import (
    MAX_QUEUE_CONCURRENCY,
    AssetContent,
    AssetNegotiation,
    BackendError,
    ConnectionStatus,
    FileIdentity,
    LoRABackendAvailability,
    ValidationError,
)


class DirectCloudBackend(DrawThingsBackend):
    backend_id = "drawthings-cloud"
    unlisted_model_bridge = False

    @staticmethod
    def default_endpoint() -> DrawThingsEndpoint:
        return DrawThingsEndpoint("compute.drawthings.ai", 443, TlsPolicy.system())

    def __init__(
        self,
        auth: CloudAuth,
        *,
        endpoint: DrawThingsEndpoint | None = None,
        max_concurrency: int = 4,
        upload_transport: httpx.AsyncBaseTransport | None = None,
        upload_policy: AssetUploadPolicy | None = None,
        **kwargs,
    ):
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= MAX_QUEUE_CONCURRENCY:
            raise ValidationError(f"Cloud concurrency must be in 1..{MAX_QUEUE_CONCURRENCY}.")
        if endpoint is not None and endpoint.tls.bundled_local:
            raise ValidationError("DT+ uses system or explicitly injected TLS trust, not the local bundled-CA policy.")
        self.max_concurrency = max_concurrency
        super().__init__(endpoint or self.default_endpoint(), **kwargs)
        self._auth = auth
        self._upload_transport, self._upload_policy = upload_transport, upload_policy

    async def lookup_lora_assets(self, identities: tuple[FileIdentity, ...]) -> dict[str, LoRABackendAvailability]:
        """Resolve DT+ LoRA storage availability without uploading file bytes."""
        return await lookup_lora_assets(self._auth, identities)

    @property
    def origin(self) -> str:
        return self._auth.origin

    @property
    def scope(self) -> str:
        return self._auth.scope

    @property
    def credential_usage(self) -> tuple[str, str | None] | None:
        """Opaque identity of the key that authorized this client's generations, for local usage attribution."""
        return self._auth.key_identity

    async def negotiate_asset(self, content: AssetContent) -> AssetNegotiation:
        """Metadata-only BYOM negotiation for one converted LoRA; never transfers bytes."""
        return await negotiate_lora_asset(self._auth, content)

    async def asset_credential_identity(self) -> str:
        """Resolve the selected DT+ key to a stable, non-secret bucket identity."""
        await self._auth.token()
        return self._auth.key_identity[0]

    async def negotiate_assets(
        self, contents: tuple[AssetContent, ...]
    ) -> dict[tuple[str, str, int], AssetNegotiation]:
        """Batch-check distinct unconfirmed LoRAs with one BYOM request."""
        return await negotiate_lora_assets(self._auth, contents)

    async def upload_asset(self, path: Path, content: AssetContent, negotiation: AssetNegotiation) -> None:
        """Stream the file to the negotiated signed destination (explicit asset preparation only)."""
        if not isinstance(negotiation.ticket, UploadTicket):
            raise ValidationError("Upload needs a negotiated upload destination.")
        await upload_signed(
            negotiation.ticket, path, content, transport=self._upload_transport, policy=self._upload_policy
        )

    async def _generation_metadata(self, message):
        return {"authorization": "bearer " + await self._auth.authorize(message)}

    async def _echo(self):
        await self._auth.token()
        return await super()._echo()

    async def check_connection(self) -> ConnectionStatus:
        try:
            await self._auth.token()
        except BackendError as exc:
            return ConnectionStatus(
                self.backend_id,
                self.endpoint.label,
                now(),
                "unknown",
                "failed",
                "unknown",
                diagnostics(
                    authentication_code=exc.failure.code,
                    **{
                        key: value
                        for key, value in exc.failure.diagnostics
                        if key in ("server_message", "http_status", "operation")
                    },
                ),
            )
        status = await super().check_connection()
        return replace(status, authentication="ok" if status.authentication != "failed" else "failed")

    async def aclose(self) -> None:
        await super().aclose()
        await self._auth.aclose()

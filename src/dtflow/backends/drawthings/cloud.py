"""Direct DT+ backend: cloud authentication around the shared Draw Things transport path."""

from dataclasses import replace

from dtflow.backends.drawthings.backend import DrawThingsBackend, DrawThingsEndpoint, now
from dtflow.backends.drawthings.cloud_auth import CloudAuth
from dtflow.backends.drawthings.errors import diagnostics
from dtflow.backends.drawthings.tls import TlsPolicy
from dtflow.core.models import BackendError, ConnectionStatus, ValidationError


class DirectCloudBackend(DrawThingsBackend):
    backend_id = "drawthings-cloud"

    def __init__(
        self, auth: CloudAuth, *, endpoint: DrawThingsEndpoint | None = None, max_concurrency: int = 4, **kwargs
    ):
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 4:
            raise ValidationError("Cloud concurrency must be in 1..4.")
        if endpoint is not None and endpoint.tls.bundled_local:
            raise ValidationError("DT+ uses system or explicitly injected TLS trust, not the local bundled-CA policy.")
        self.max_concurrency = max_concurrency
        super().__init__(endpoint or DrawThingsEndpoint("compute.drawthings.ai", 443, TlsPolicy.system()), **kwargs)
        self._auth = auth

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
                diagnostics(authentication_code=exc.failure.code),
            )
        status = await super().check_connection()
        return replace(status, authentication="ok" if status.authentication != "failed" else "failed")

    async def aclose(self) -> None:
        await super().aclose()
        await self._auth.aclose()

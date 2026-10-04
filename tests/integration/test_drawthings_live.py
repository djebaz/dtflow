"""Optional non-generative live check: strict TLS connection and Echo inventory against a real Draw Things server.

Run explicitly:  DTFLOW_LIVE_TESTS=1 DTFLOW_LIVE_HOST=<ip> DTFLOW_LIVE_PORT=<port> uv run pytest -m live
Optional: DTFLOW_LIVE_CA_FILE (explicit CA bundle), DTFLOW_LIVE_SERVER_NAME (verified TLS name when connecting by IP),
DTFLOW_LIVE_SHARED_SECRET. It never submits a generation and never relaxes verification.
"""

import os
from pathlib import Path

import pytest
from support.network_guard import endpoint

from dtflow.backends.drawthings import DrawThingsBackend, DrawThingsEndpoint, TlsPolicy
from dtflow.core.models import Credential, TimeoutPolicy


class Secret:
    def __init__(self, value):
        self.value = value

    async def get(self):
        return Credential("env", self.value)


@pytest.mark.live
@pytest.mark.asyncio
async def test_strict_connection_and_inventory():
    host, port = os.environ.get("DTFLOW_LIVE_HOST"), os.environ.get("DTFLOW_LIVE_PORT")
    if not host or not port:
        pytest.skip("DTFLOW_LIVE_HOST (an IP literal) and DTFLOW_LIVE_PORT are required.")
    ca_file, name = os.environ.get("DTFLOW_LIVE_CA_FILE"), os.environ.get("DTFLOW_LIVE_SERVER_NAME")
    tls = TlsPolicy.custom_ca(Path(ca_file), name) if ca_file else TlsPolicy.system(name)
    secret = os.environ.get("DTFLOW_LIVE_SHARED_SECRET")
    adapter = DrawThingsBackend(
        DrawThingsEndpoint(host, int(port), tls),
        timeouts=TimeoutPolicy(connect=10.0, inventory=20.0),
        shared_secret=Secret(secret) if secret else None,
    )
    await adapter.open()
    try:
        with endpoint(host, int(port)):
            status = await adapter.check_connection()
            assert status.transport == "ok", f"sanitized diagnostics: {status.diagnostics}"
            assert status.inventory == "ok", f"sanitized diagnostics: {status.diagnostics}"
    finally:
        await adapter.aclose()

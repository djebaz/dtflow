"""Optional non-generative live check: local Draw Things gRPC connection and Echo inventory.

Run explicitly:  DTFLOW_LIVE_TESTS=1 DTFLOW_LIVE_HOST=<ip> DTFLOW_LIVE_PORT=<port> uv run pytest -m live
Optional: DTFLOW_LIVE_CA_FILE (explicit CA bundle), DTFLOW_LIVE_SERVER_NAME (verified TLS name when connecting by IP),
DTFLOW_LIVE_SHARED_SECRET. Default trust uses the bundled Draw Things CA with chain verification and local
hostname/X.509 compatibility settings; an explicit CA/server name selects strict trust. No generation is submitted.
"""

import os
from pathlib import Path

import pytest
from support.live_evidence import save_inventory
from support.live_local import connect_hint, skip_if_local_unreachable, warn_if_model_browsing_off
from support.network_guard import endpoint

from dtflow.backends.drawthings import DrawThingsBackend, DrawThingsEndpoint, TlsPolicy
from dtflow.core.models import Credential, TimeoutPolicy


class Secret:
    def __init__(self, value):
        self.value = value

    async def get(self):
        return Credential("env", self.value)


def configured_local_adapter():
    """Build the configured local gRPC adapter without opening a connection."""
    host, port = os.environ.get("DTFLOW_LIVE_HOST"), os.environ.get("DTFLOW_LIVE_PORT")
    if not host or not port:
        pytest.skip("DTFLOW_LIVE_HOST (an IP literal) and DTFLOW_LIVE_PORT are required.")
    ca_file, name = os.environ.get("DTFLOW_LIVE_CA_FILE"), os.environ.get("DTFLOW_LIVE_SERVER_NAME")
    tls = (
        TlsPolicy.custom_ca(Path(ca_file), name)
        if ca_file
        else (TlsPolicy.system(name) if name else TlsPolicy.drawthings_local())
    )
    secret = os.environ.get("DTFLOW_LIVE_SHARED_SECRET")
    adapter = DrawThingsBackend(
        DrawThingsEndpoint(host, int(port), tls),
        timeouts=TimeoutPolicy(connect=10.0, inventory=20.0),
        shared_secret=Secret(secret) if secret else None,
    )
    return adapter, host, int(port)


@pytest.mark.live
@pytest.mark.asyncio
async def test_local_connection_and_inventory(live_run, request):
    adapter, host, port = configured_local_adapter()
    await adapter.open()
    try:
        with endpoint(host, port):
            status = await adapter.check_connection()
            detail = dict(status.diagnostics)
            if status.transport == "failed":
                skip_if_local_unreachable(
                    request, detail.get("transport_code"), detail.get("transport_kind"), f"{host}:{port}"
                )
            assert status.transport == "ok", f"sanitized diagnostics: {status.diagnostics}" + connect_hint(
                detail.get("transport_kind")
            )
            assert status.inventory == "ok", f"sanitized diagnostics: {status.diagnostics}"
            inventory = await adapter.get_inventory(refresh=True)
            save_inventory(live_run, "local", adapter.endpoint.label, inventory)
            warn_if_model_browsing_off(request, inventory)
    finally:
        await adapter.aclose()

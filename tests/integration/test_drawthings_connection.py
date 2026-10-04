"""Connection, strict-TLS and inventory behavior of the actual Draw Things adapter against the scripted server."""

import pytest
from support.dt_server import TLS_DIR, EchoSpec, black_hole, closed_port, scripted_server
from support.network_guard import endpoint

from dtflow.backends.drawthings import DrawThingsBackend, DrawThingsEndpoint, TlsPolicy
from dtflow.core.models import BackendError, Credential, ErrorCategory, TimeoutPolicy

CA = TLS_DIR / "ca.pem"
FAST = TimeoutPolicy(connect=1.0, inventory=2.0)


class Secret:
    def __init__(self, value):
        self.value = value

    async def get(self):
        return Credential("test", self.value) if self.value else None


def backend(host, port, *, tls=None, secret=None, timeouts=FAST):
    return DrawThingsBackend(
        DrawThingsEndpoint(host, port, tls or TlsPolicy.custom_ca(CA)),
        timeouts=timeouts,
        shared_secret=Secret(secret) if secret else None,
    )


async def opened(*args, **kwargs):
    adapter = backend(*args, **kwargs)
    await adapter.open()
    return adapter


MODELS = (
    {"name": "Model A", "file": "a_q8p.ckpt", "version": "flux1"},
    {"name": "Model B", "file": "b.ckpt", "version": "some_future_architecture"},
)
LORAS = ({"name": "A LoRA", "file": "lora.ckpt", "version": "flux1"},)
CONTROLS = ({"name": "A Control", "file": "control.ckpt", "version": "flux1"},)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "leaf,server_name,transport,code",
    [
        ("server", None, "ok", None),
        ("dnsonly", "dtflow-fixture.test", "ok", None),
        ("dnsonly", None, "failed", "tls_hostname_mismatch"),
        ("wrongname", None, "failed", "tls_hostname_mismatch"),
        ("untrusted", None, "failed", "tls_untrusted_certificate"),
    ],
)
async def test_strict_tls_never_downgrades(leaf, server_name, transport, code):
    async with scripted_server(leaf=leaf) as server:
        adapter = await opened(server.host, server.port, tls=TlsPolicy.custom_ca(CA, server_name))
        try:
            status = await adapter.check_connection()
            assert status.transport == transport
            assert dict(status.diagnostics).get("transport_code") == code
            if code:
                assert (status.authentication, status.inventory) == ("unknown", "unknown")
                assert server.seen.echo_requests == []  # no request is sent after a TLS failure
                with pytest.raises(BackendError) as caught:
                    await adapter.get_inventory(refresh=True)  # and no weaker retry happens
                assert caught.value.failure.code == code
                assert caught.value.failure.category == ErrorCategory.CONNECTIVITY
        finally:
            await adapter.aclose()


def test_tls_policy_cannot_disable_verification():
    import ssl

    weak = ssl.create_default_context()
    weak.check_hostname = False
    weak.verify_mode = ssl.CERT_NONE
    with pytest.raises(Exception, match="verify certificates and hostnames"):
        TlsPolicy(context=weak)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "echo,secret,expected",
    [
        (EchoSpec(files=("a.ckpt",)), None, ("ok", "not_applicable", "ok")),
        (EchoSpec(), None, ("ok", "not_applicable", "ok")),  # an empty inventory is not a failure
        (EchoSpec(secret="s3cret"), None, ("ok", "failed", "unknown")),
        (EchoSpec(secret="s3cret"), "wrong", ("ok", "failed", "unknown")),
        (EchoSpec(secret="s3cret", files=("a.ckpt",)), "s3cret", ("ok", "ok", "ok")),
    ],
)
async def test_connection_evidence_is_observed_not_assumed(echo, secret, expected):
    async with scripted_server(echo=echo) as server:
        adapter = await opened(server.host, server.port, secret=secret)
        try:
            status = await adapter.check_connection()
            assert (status.transport, status.authentication, status.inventory) == expected
            assert "s3cret" not in repr(status) and "wrong" not in repr(status)
            if expected[1] == "failed":
                with pytest.raises(BackendError) as caught:
                    await adapter.get_inventory(refresh=True)  # missing/wrong secret is never an inventory
                assert caught.value.failure.category == ErrorCategory.AUTHENTICATION
        finally:
            await adapter.aclose()


@pytest.mark.asyncio
async def test_unreachable_and_silent_servers_fail_within_the_connect_deadline():
    async with closed_port() as (host, port):
        adapter = await opened(host, port)
        assert dict((await adapter.check_connection()).diagnostics)["transport_code"] == "connect_failed"
        await adapter.aclose()
    async with black_hole() as (host, port):
        adapter = await opened(host, port, timeouts=TimeoutPolicy(connect=0.3, inventory=1.0))
        status = await adapter.check_connection()
        assert (status.transport, dict(status.diagnostics)["transport_code"]) == ("failed", "connect_deadline")
        await adapter.aclose()


@pytest.mark.asyncio
async def test_inventory_mapping_provenance_and_cache():
    echo = EchoSpec(
        files=("a_q8p.ckpt", "b.ckpt", "lora.ckpt", "control.ckpt", "stray.bin"),
        models=MODELS,
        loras=LORAS,
        control_nets=CONTROLS,
        server_identifier=42,
    )
    async with scripted_server(echo=echo) as server:
        adapter = await opened(server.host, server.port)
        try:
            assert adapter.capabilities.model_ids == ()  # nothing is accepted before an inventory exists
            fresh = await adapter.get_inventory(refresh=True)
            assert fresh.refreshed and fresh.server_identity == "42" and fresh.backend_id == "drawthings-grpc"
            kinds = {entry.identifier: (entry.kind, entry.architecture) for entry in fresh.entries}
            assert kinds == {
                "a_q8p.ckpt": ("model", "flux1"),
                "b.ckpt": ("model", "some_future_architecture"),  # unknown architectures are preserved
                "lora.ckpt": ("lora", "flux1"),
                "control.ckpt": ("control", "flux1"),
                "stray.bin": ("unknown", None),  # unclassified files get no fabricated kind or family
            }
            assert {entry.availability for entry in fresh.entries} == {"listed_in_server_inventory"}
            assert adapter.capabilities.model_ids == ("a_q8p.ckpt", "b.ckpt")
            cached = await adapter.get_inventory()
            assert not cached.refreshed and len(server.seen.echo_requests) == 1
            assert cached.entries == fresh.entries
        finally:
            await adapter.aclose()


@pytest.mark.asyncio
async def test_servers_without_model_metadata_list_files_as_unknown_but_eligible():
    async with scripted_server(echo=EchoSpec(files=("only.ckpt",))) as server:
        adapter = await opened(server.host, server.port)
        try:
            snapshot = await adapter.get_inventory(refresh=True)
            assert [(e.kind, e.metadata_source) for e in snapshot.entries] == [("unknown", "echo.files")]
            assert adapter.capabilities.model_ids == ("only.ckpt",)
        finally:
            await adapter.aclose()


@pytest.mark.asyncio
async def test_authentication_failure_on_refresh_clears_the_snapshot():
    echo = EchoSpec(files=("a_q8p.ckpt",), models=MODELS[:1], secret="s3cret")
    async with scripted_server(echo=echo) as server:
        adapter = await opened(server.host, server.port, secret="s3cret")
        try:
            await adapter.get_inventory(refresh=True)
            assert adapter.capabilities.model_ids == ("a_q8p.ckpt",)
            server.service.echo_spec.secret = "rotated"  # the server stops accepting our identity
            with pytest.raises(BackendError) as caught:
                await adapter.get_inventory(refresh=True)
            assert caught.value.failure.category == ErrorCategory.AUTHENTICATION
            assert adapter.capabilities.model_ids == ()  # cleared, not served as if still valid
        finally:
            await adapter.aclose()


@pytest.mark.asyncio
async def test_connectivity_failure_on_refresh_keeps_a_snapshot_marked_stale():
    adapter = None
    async with scripted_server(echo=EchoSpec(files=("a_q8p.ckpt",), models=MODELS[:1])) as server:
        host, port = server.host, server.port
        adapter = await opened(host, port)
        await adapter.get_inventory(refresh=True)
    try:
        with endpoint(host, port):  # the server is gone but the exact endpoint stays registered
            with pytest.raises(BackendError) as caught:
                await adapter.get_inventory(refresh=True)
            assert caught.value.failure.category == ErrorCategory.CONNECTIVITY
            stale = await adapter.get_inventory()
        diagnostics = dict(stale.diagnostics)
        assert diagnostics["stale"] == "true" and diagnostics["stale_reason"] == caught.value.failure.code
        assert not stale.refreshed and adapter.capabilities.model_ids == ("a_q8p.ckpt",)
    finally:
        await adapter.aclose()

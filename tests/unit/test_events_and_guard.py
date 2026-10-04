"""Bounded event admission and exact offline endpoint/live opt-in policy."""

import asyncio
import socket
import subprocess

import pytest
from support.network_guard import live_allowed

from dtflow.core.models import BackendEvent
from dtflow.services.events import EventBuffer


def test_progress_coalesces_without_losing_submission_boundary():
    buffer = EventBuffer(4)
    buffer(BackendEvent("r", 1, "submission_possible", "fixture"))
    for sequence in range(2, 100):
        buffer(BackendEvent("r", sequence, "progress", "fixture"))
    events = buffer.drain()
    assert [(x.kind, x.sequence) for x in events] == [("submission_possible", 1), ("progress", 99)]
    for sequence in range(4):
        buffer(BackendEvent("r", sequence, "terminal", "fixture"))
    with pytest.raises(ValueError, match="Critical event"):
        buffer(BackendEvent("r", 5, "terminal", "fixture"))


def test_guard_blocks_unregistered_loopback_dns_udp_and_children():
    with socket.socket() as client:
        for method in (client.connect, client.connect_ex):
            with pytest.raises(AssertionError, match="Unregistered"):
                method(("127.0.0.1", 7859))
    with pytest.raises(AssertionError, match="Unregistered"):
        socket.getaddrinfo("fixture.invalid", 443)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        with pytest.raises(AssertionError, match="Unregistered"):
            client.sendto(b"fixture", ("127.0.0.1", 7859))
    with pytest.raises(AssertionError, match="Subprocesses"):
        subprocess.run(["unexpected-child"], check=False)


@pytest.mark.asyncio
async def test_async_connections_require_an_exact_fixture_registration(fixture_endpoint):
    async def echo(reader, writer):
        writer.write(await reader.read(1))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(AssertionError, match="Unregistered"):
            await asyncio.open_connection("127.0.0.1", port)
        with fixture_endpoint("127.0.0.1", port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"x")
            await writer.drain()
            assert await reader.read(1) == b"x"
            writer.close()
            await writer.wait_closed()
        with pytest.raises(AssertionError, match="Unregistered"):
            await asyncio.open_connection("127.0.0.1", port)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    "selection,environ,generation,expected",
    [
        ("", {}, False, False),
        ("not live", {"DTFLOW_LIVE_TESTS": "1"}, False, False),
        ("live", {}, False, False),
        ("live", {"DTFLOW_LIVE_TESTS": "1"}, False, True),
        (
            "live",
            {"DTFLOW_LIVE_TESTS": "1", "DTFLOW_LIVE_GENERATION": "1", "DTFLOW_LIVE_AUTHORIZED_WORKLOAD": "named"},
            True,
            False,
        ),
        ("live_generation", {"DTFLOW_LIVE_TESTS": "1", "DTFLOW_LIVE_GENERATION": "1"}, True, False),
        (
            "live_generation",
            {"DTFLOW_LIVE_TESTS": "1", "DTFLOW_LIVE_GENERATION": "1", "DTFLOW_LIVE_AUTHORIZED_WORKLOAD": "named"},
            True,
            True,
        ),
    ],
)
def test_live_gating_requires_selection_opt_ins_and_named_authorization(selection, environ, generation, expected):
    assert live_allowed(selection, environ, generation) is expected

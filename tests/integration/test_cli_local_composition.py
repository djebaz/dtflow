"""One CLI -> composition -> local adapter -> scripted server -> durable output path, including a selected LoRA.

Adapter wire/transport/TLS behavior is owned by the local gRPC suites; this proves only the new application wiring.
"""

import asyncio
import json

import pytest
from support.dt_server import (
    EchoSpec,  # noqa: I001
    Image,
    Signpost,
    pixel_tensor,
    scripted_server,
)
from test_local_grpc_connection import CA, LORAS, MODELS

from dtflow import cli
from dtflow.backends.drawthings import TlsPolicy
from dtflow.backends.drawthings.wire import float32
from dtflow.composition import create_backend

ECHO = EchoSpec(files=("a_q8p.ckpt", "lora.ckpt"), models=MODELS[:1], loras=LORAS)


@pytest.fixture
def fixture_trust(monkeypatch):
    monkeypatch.setattr(
        cli,
        "create_backend",
        lambda selection, timeouts=None: create_backend(selection, timeouts=timeouts, tls=TlsPolicy.custom_ca(CA)),
    )


async def invoke(tmp_path, host, port, *args):
    argv = [
        "-q",
        str(tmp_path / "logs"),
        "generate",
        "-b",
        "local",
        "-e",
        f"{host}:{port}",
        "-d",
        str(tmp_path / "state"),
        *args,
    ]
    return await asyncio.to_thread(cli.main, argv)


@pytest.mark.asyncio
async def test_local_generation_with_lora_through_the_cli(tmp_path, capsys, fixture_trust):
    async with scripted_server((Signpost(), Image(pixel_tensor(128, 128))), ECHO) as server:
        code = await invoke(
            tmp_path,
            server.host,
            server.port,
            "-m",
            "a_q8p.ckpt",
            "-p",
            "a cat",
            "-s",
            "0",
            "-l",
            "lora.ckpt:0.5",
            "-r",
        )
        out = capsys.readouterr().out
        assert code == 0, out
        report = json.loads(out)
        sent = server.service.configuration()
        assert server.seen.generate_requests[0].prompt == "trigger phrase a cat"  # explicit --prefix only
        assert sent.Seed() == 0 and sent.LorasLength() == 1
        assert sent.Loras(0).Weight() == float32(0.5) and sent.Loras(0).File().decode() == "lora.ckpt"
    assert report["backend"] == "drawthings-grpc" and "fake" not in report
    assert report["lora_preflight"][0]["code"] == "ok" and report["lora_preflight"][0]["generation_evidence"] == (
        "application_unconfirmed"
    )
    metadata = json.loads(open(report["metadata"]).read())
    assert metadata["request"]["loras"] == [{"identifier": "lora.ckpt", "weight": 0.5}]
    assert metadata["backend_id"] == "drawthings-grpc"


@pytest.mark.asyncio
async def test_missing_lora_stops_before_any_generation_request(tmp_path, capsys, fixture_trust):
    async with scripted_server((Image(pixel_tensor(128, 128)),), ECHO) as server:
        code = await invoke(tmp_path, server.host, server.port, "-m", "a_q8p.ckpt", "-p", "x", "-l", "absent.ckpt:1")
        captured = capsys.readouterr()
        assert code == 2 and captured.out == ""
        assert json.loads(captured.err.splitlines()[-1])["diagnostics"] == [["absent.ckpt", "not_available"]]
        assert server.seen.generate_requests == []
    assert not (tmp_path / "state" / "outputs").exists()

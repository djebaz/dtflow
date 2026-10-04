"""Offline checks for the Phase 1 diagnostic probe; no generation server."""

import asyncio
import importlib.util
import io
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

PROBE_PATH = Path(__file__).resolve().parents[2] / "tools/drawthings/drawthings_concurrency_test.py"
spec = importlib.util.spec_from_file_location("dtflow_phase1_probe", PROBE_PATH)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)

sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


@pytest.fixture(autouse=True)
def refuse_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Phase 1 unit checks must not connect to a network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def args(tmp_path, jobs=2):
    return SimpleNamespace(
        jobs=jobs, cloud=False, host="fixture", port=1, preset="z_image_turbo", full=False, out=tmp_path
    )


class Service:
    def __init__(self, index, entered, closed, generated, slow=None, fail=False, block=False, images=None):
        self.index, self.entered, self.closed, self.generated = index, entered, closed, generated
        self.slow, self.fail, self.block, self.images = slow, fail, block, images

    async def __aenter__(self):
        if self.slow is not None:
            await self.slow.wait()
        if self.fail:
            raise ConnectionError("fixture refused")
        self.entered.add(self.index)
        return self

    async def __aexit__(self, *exc):
        self.closed.add(self.index)

    async def generate(self, request):
        self.generated.append(set(self.entered))
        if self.block:
            await asyncio.Future()
        return (
            self.images
            if self.images is not None
            else [SimpleNamespace(to_file=lambda path: path.write_bytes(b"fixture"))]
        )


def test_group_waits_for_every_connection(monkeypatch, tmp_path):
    async def scenario():
        entered, closed, generated = set(), set(), []
        slow = asyncio.Event()
        count = iter(range(2))

        def create(*unused):
            index = next(count)
            return Service(index, entered, closed, generated, slow=slow if index == 1 else None)

        monkeypatch.setattr(probe, "create_service", create)
        group = asyncio.create_task(probe.run_concurrent(args(tmp_path), None))
        for _ in range(20):
            await asyncio.sleep(0)
        assert entered == {0}
        assert generated == []
        slow.set()
        results, _ = await group
        assert generated == [{0, 1}, {0, 1}]
        assert closed == {0, 1}
        assert all(result.error is None for result in results)

    asyncio.run(scenario())


def test_connection_failure_prevents_group_submission(monkeypatch, tmp_path):
    async def scenario():
        entered, closed, generated = set(), set(), []
        count = iter(range(2))
        monkeypatch.setattr(
            probe, "create_service", lambda *unused: Service(next(count), entered, closed, generated, fail=True)
        )
        with pytest.raises(ConnectionError, match="fixture refused"):
            await asyncio.wait_for(probe.run_concurrent(args(tmp_path), None), 1)
        assert generated == []

    asyncio.run(scenario())


@pytest.mark.parametrize("before_release", [True, False])
def test_interruption_closes_connected_jobs(monkeypatch, tmp_path, before_release):
    async def scenario():
        entered, closed, generated = set(), set(), []
        never_ready = asyncio.Event()
        count = iter(range(2))

        def create(*unused):
            index = next(count)
            return Service(
                index,
                entered,
                closed,
                generated,
                slow=never_ready if before_release and index == 1 else None,
                block=True,
            )

        monkeypatch.setattr(probe, "create_service", create)
        group = asyncio.create_task(probe.run_concurrent(args(tmp_path), None))
        for _ in range(30):
            await asyncio.sleep(0)
        assert bool(generated) is not before_release
        group.cancel()
        with pytest.raises(asyncio.CancelledError):
            await group
        assert closed == entered

    asyncio.run(scenario())


def test_empty_output_is_failure(monkeypatch, tmp_path):
    async def scenario():
        monkeypatch.setattr(probe, "create_service", lambda *unused: Service(0, set(), set(), [], images=[]))
        result = await probe.run_job(
            name="empty",
            args=args(tmp_path, 1),
            cloud_auth=None,
            prompt="fixture",
            seed=1,
            output_prefix="test",
            barrier=None,
        )
        assert result.output is None
        assert "no images" in result.error
        assert list(tmp_path.iterdir()) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("raw", [b"not json", b"[]"])
def test_malformed_auth_response(monkeypatch, raw):
    monkeypatch.setattr(probe, "urlopen", lambda *unused, **kwargs: io.BytesIO(raw))
    with pytest.raises(probe.CloudAuthError):
        probe._request_json("https://fixture.invalid/token", method="POST", payload={}, operation="fixture")


def test_http_failure_excludes_response_body(monkeypatch):
    def refused(*unused, **kwargs):
        raise HTTPError("https://fixture.invalid", 401, "rejected", {}, io.BytesIO(b"private-token"))

    monkeypatch.setattr(probe, "urlopen", refused)
    with pytest.raises(probe.CloudAuthError, match="HTTP 401") as exc:
        probe._request_json("https://fixture.invalid", method="POST", operation="fixture")
    assert "private-token" not in str(exc.value)


def test_connection_only_never_generates(monkeypatch):
    async def scenario():
        calls = []

        async def connection(*unused):
            calls.append("connection")

        monkeypatch.setattr(probe, "test_connection", connection)
        result = await probe.async_main(SimpleNamespace(cloud=False, skip_conn=False, conn=True))
        assert result == 0
        assert calls == ["connection"]

    asyncio.run(scenario())


def test_cli_validation_and_legacy_aliases():
    parsed = probe.parse_args(["-H", "fixture", "--connection-only"])
    assert parsed.host == "fixture" and parsed.conn
    with pytest.raises(SystemExit):
        probe.parse_args(["--cloud", "--host", "fixture"])
    with pytest.raises(SystemExit):
        probe.parse_args(["--host", "fixture", "--jobs", "9"])

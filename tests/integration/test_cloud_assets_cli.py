"""3B-O9: CLI and queue composition of cloud assets. Local registry semantics are reported honestly, requests carry the
exact immutable prepared reference, and neither `queue add` nor dispatch ever uploads."""

import dataclasses
import json

import pytest
from support.cloud_assets import API_KEY, DtPlus, lora_file, sha256

from dtflow import cli
from dtflow.backends.fake import FakeBackend
from dtflow.core.models import BackendError, LoRASelection, PreparedAsset, ResolvedGenerationRequest
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.queues import read_queues, request_from_record
from dtflow.persistence.store import SQLiteAttempts
from dtflow.services import session
from dtflow.services.assets import AssetService
from dtflow.services.generation import GenerationService
from dtflow.services.queue import QueueDispatcher


@pytest.fixture
def cloud(monkeypatch):
    fixture = DtPlus()
    monkeypatch.setattr(
        session, "create_backend", lambda *args, **kwargs: fixture.backend()
    )  # assets, generation and queue dispatch
    return fixture


@pytest.fixture
def invoke(tmp_path, capsys):
    def call(*args):
        code = cli.main(["-q", str(tmp_path / "logs"), *args, "-d", str(tmp_path / "state")])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return call


def prepare(invoke, file) -> dict:
    code, out, _ = invoke("assets", "prepare", "-f", str(file))
    assert code == 0
    return json.loads(out)


def test_cli_prepare_list_show_reconcile_report_local_registry_semantics(tmp_path, cloud, invoke):
    file = lora_file(tmp_path)
    receipt = prepare(invoke, file)
    assert (receipt["state"], receipt["source"], receipt["identifier"]) == ("confirmed", "transfer", "style.ckpt")
    assert receipt["sha256"] == sha256(file.read_bytes()) and receipt["size"] == file.stat().st_size
    assert receipt["remote_id"].startswith("remote-") and "not remote account inventory" in receipt["note"]
    assert len(cloud.puts) == 1

    calls = len(cloud.api_calls)
    code, out, _ = invoke("assets", "list")
    listed = json.loads(out)
    assert code == 0 and [a["asset_id"] for a in listed["assets"]] == [receipt["asset_id"]]
    assert "not remote account inventory" in listed["note"]
    code, out, _ = invoke("assets", "show", receipt["asset_id"])
    shown = json.loads(out)
    assert code == 0 and shown["state"] == "confirmed" and shown["operations"][0]["action"] == "prepare"
    assert len(cloud.api_calls) == calls  # list and show never touch the network

    code, out, _ = invoke("assets", "reconcile", receipt["asset_id"])
    assert code == 0 and json.loads(out)["reconciliation"] == "confirmed" and len(cloud.puts) == 1

    cloud.stored.clear()  # the server no longer has the file: the receipt is stale, and reconcile still never uploads
    code, out, _ = invoke("assets", "reconcile", receipt["asset_id"])
    stale = json.loads(out)
    assert code == 3 and stale["reconciliation"] == "upload_required" and stale["state"] == "unknown"
    assert len(cloud.puts) == 1

    # Nothing secret is printed, and prepare needs an existing file rather than guessing.
    assert API_KEY not in out
    code, _, err = invoke("assets", "prepare", "-f", str(tmp_path / "missing.ckpt"))
    assert code == 2 and "Invalid request" in err


def test_help_discloses_that_only_prepare_may_upload(capsys):
    text = cli.parser().format_help()
    assert "assets" in text
    choices = cli.parser()._subparsers._group_actions[0].choices["assets"]._subparsers._group_actions[0].choices
    assert "UPLOAD" in choices["prepare"].format_help() and "Never uploads" in choices["reconcile"].format_help()
    assert "-z, --asset" in cli.parser()._subparsers._group_actions[0].choices["generate"].format_help()


def test_queue_add_binds_only_a_confirmed_receipt_and_never_uploads(tmp_path, cloud, invoke):
    receipt = prepare(invoke, lora_file(tmp_path))
    puts = len(cloud.puts)
    base = ("queue", "add", "-b", "cloud", "-p", "x", "-m", "model", "-l", "style.ckpt:0.8")
    code, out, _ = invoke(*base, "-z", receipt["asset_id"])
    assert code == 0
    queue = read_queues(tmp_path / "state", json.loads(out)["queue_id"])[0]
    assert len(cloud.puts) == puts

    with SQLiteReader(tmp_path / "state") as db:
        stored = request_from_record(json.loads(db.execute("SELECT request_json FROM queue_items").fetchone()[0]))
    (prepared,) = stored.prepared_assets
    assert (prepared.receipt_id, prepared.sha256, prepared.size, prepared.remote_id) == (
        receipt["asset_id"],
        receipt["sha256"],
        receipt["size"],
        receipt["remote_id"],
    )
    assert stored.loras == (LoRASelection("style.ckpt", 0.8),)
    assert queue["summary"]["pending"] == 1

    # Unknown receipt, other backend, or no matching --lora: refused before any queue is created.
    asset = receipt["asset_id"]
    refusals = (
        (("-b", "cloud", "-l", "style.ckpt:0.8", "-z", "nonexistent"), "No local asset receipt"),
        (("-b", "fake", "-l", "style.ckpt:0.8", "-z", asset), "cloud backend only"),
        (("-b", "cloud", "-z", asset), "needs a matching --lora"),
    )
    for options, wanted in refusals:
        code, _, err = invoke("queue", "add", "-p", "x", "-m", "model", *options)
        assert code == 2 and wanted in err
    assert len(read_queues(tmp_path / "state")) == 1


class SQLiteReader:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        import sqlite3

        self.db = sqlite3.connect(self.state / "attempts.sqlite3")
        return self.db

    def __exit__(self, *exc):
        self.db.close()


class CloudLikeFake:
    """Fake generation under the cloud backend identity so prepared cloud references are accepted."""

    def __init__(self):
        self.inner = FakeBackend()
        self.capabilities = dataclasses.replace(
            self.inner.capabilities, backend_id="drawthings-cloud", lora_support="selection"
        )
        self.generated = 0

    async def open(self):
        await self.inner.open()

    async def get_inventory(self, refresh=False):
        return await self.inner.get_inventory(refresh)

    async def generate(self, request, *, on_event):
        self.generated += 1
        # The fake accepts no LoRAs; the selection and prepared reference are what the service layer is checking.
        result = await self.inner.generate(
            dataclasses.replace(request, loras=(), prepared_assets=()), on_event=on_event
        )
        return dataclasses.replace(result, backend_id="drawthings-cloud")

    async def aclose(self):
        await self.inner.aclose()


async def prepared_request(tmp_path, cloud: DtPlus) -> tuple[ResolvedGenerationRequest, PreparedAsset]:
    registry = AssetRegistry(tmp_path / "assets-state", writer=True)
    backend = cloud.backend()
    await registry.open()
    try:
        prepared = await AssetService(backend, registry).prepare(lora_file(tmp_path))
    finally:
        await backend.aclose()
        await registry.aclose()
    request = ResolvedGenerationRequest(
        "req-1", "fixture", "fake-fixture", 1, loras=(LoRASelection("style.ckpt", 0.8),), prepared_assets=(prepared,)
    )
    return request, prepared


@pytest.mark.asyncio
async def test_generation_and_queue_carry_the_exact_reference_and_block_stale_ones_before_submission(tmp_path):
    cloud = DtPlus()
    request, prepared = await prepared_request(tmp_path, cloud)
    # The upload fixture contains arbitrary bytes, not a converted LoRA. Supply
    # matching architecture evidence for the fake backend's "fixture" model;
    # this test is about receipt identity and stale-reference refusal.
    prepared = dataclasses.replace(prepared, family="fixture", family_source="file")
    request = dataclasses.replace(request, prepared_assets=(prepared,))
    puts = len(cloud.puts)
    store, backend = SQLiteAttempts(tmp_path / "state"), CloudLikeFake()
    validator_backend = cloud.backend()
    await store.open()
    await backend.open()
    try:
        service = GenerationService(backend, store, tmp_path / "out", assets=AssetService(validator_backend))
        saved = await service.generate(request)
        # The reference is persisted in the attempt and the saved metadata exactly as resolved.
        recorded = json.loads(open(saved.metadata_path).read())["request"]["prepared_assets"]
        assert recorded == [dataclasses.asdict(prepared)] and backend.generated == 1

        # A queue of the same immutable reference survives its durable round trip unchanged.
        later = dataclasses.replace(request, request_id="req-2", seed=2)
        queue_id = await store.create_queue({"name": "cloud", "endpoint": None}, 1, [later])
        cloud.stored.clear()  # stale: the server no longer validates the reference
        final = await QueueDispatcher(store, service, poll=0.01).run(queue_id)
        (item,) = final["items"]
        assert item["state"] == "failed" and item["failure"]["code"] == "prepared_asset_upload_required"
        assert backend.generated == 1 and len(cloud.puts) == puts  # nothing submitted and nothing uploaded

        # Direct generation: same refusal, and no attempt was created for it.
        with pytest.raises(BackendError) as caught:
            await service.generate(dataclasses.replace(request, request_id="req-3", seed=3))
        assert caught.value.failure.code == "prepared_asset_upload_required"
        assert [a["request_id"] for a in await store.inspect()] == ["req-1"]
    finally:
        await backend.aclose()
        await validator_backend.aclose()
        await store.aclose()


@pytest.mark.asyncio
async def test_prepared_references_are_rejected_by_backends_without_cloud_assets(tmp_path):
    cloud = DtPlus()
    request, _ = await prepared_request(tmp_path, cloud)
    store, backend = SQLiteAttempts(tmp_path / "state"), FakeBackend()
    await store.open()
    await backend.open()
    try:
        with pytest.raises(BackendError) as caught:
            await GenerationService(backend, store, tmp_path / "out").generate(request)
        assert caught.value.failure.code == "prepared_asset_unsupported"
        assert await store.inspect() == ()  # explicit fake LoRAs never imply cloud-asset support
    finally:
        await backend.aclose()
        await store.aclose()

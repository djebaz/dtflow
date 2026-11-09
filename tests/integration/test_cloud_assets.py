"""Phase 3B cloud assets: prepare/upload/reuse (3B-O6), identity and concurrency (3B-O7), uncertainty and
sanitization (3B-O8). The DT+ API and signed storage are in-memory fixtures; nothing contacts a real service."""

import asyncio
import logging
import sqlite3

import pytest
from support.cloud_assets import API_KEY, CHUNK, DtPlus, lora_file, settle_loop, sha256

from dtflow.core.models import AssetContent, BackendError, ErrorCategory, RemoteOutcome, ValidationError
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.store import PersistenceError
from dtflow.services.assets import AssetService

SECRETS = (API_KEY, "SIGNED-QUERY-SECRET", "FAKE-SHORT-TOKEN", "storage.invalid")


class Session:
    """One writer process: registry + backend + service, closed like the CLI closes them."""

    def __init__(self, state, cloud: DtPlus):
        self.cloud, self.registry = cloud, AssetRegistry(state, writer=True)
        self.backend = cloud.backend()
        self.service = AssetService(self.backend, self.registry)

    async def __aenter__(self):
        await self.registry.open()
        return self

    async def __aexit__(self, *exc):
        await self.backend.aclose()
        await self.registry.aclose()


def public(exc: BaseException) -> str:
    text = str(exc) + repr(exc)
    if isinstance(exc, BackendError):
        text += repr(exc.failure)
    return text


@pytest.mark.asyncio
async def test_prepare_upload_receipt_restart_and_validated_reuse(tmp_path):
    """3B-O6: POST, streamed signed PUT, durable receipt, restart, then reuse and already-exists with no second PUT."""
    state, cloud = tmp_path / "state", DtPlus()
    file = lora_file(tmp_path)
    data = file.read_bytes()
    async with Session(state, cloud) as one:
        receipt = await one.service.prepare(file, provenance="test")
    assert (receipt.sha256, receipt.size, receipt.identifier) == (sha256(data), len(data), "style.ckpt")
    assert receipt.remote_id == "remote-" + sha256(data)[:12] and receipt.backend_id == "drawthings-cloud"

    (put,) = cloud.puts
    assert put["body"] == data and max(put["sizes"]) <= CHUNK and len(put["sizes"]) == 5
    assert "authorization" not in put["headers"] and put["headers"]["content-length"] == str(len(data))
    assert put["url"] == "https://storage.invalid/put/OBJECT?X-Signature=SIGNED-QUERY-SECRET"
    first = cloud.api_calls[0]
    assert first["entry"] == {"file": "style.ckpt", "sha256": sha256(data), "size": len(data)}
    assert first["authorization"] == "FAKE-SHORT-TOKEN"

    # Restart: a fresh process reads the durable receipt, but reuse still needs this scope's own server validation.
    async with Session(state, cloud) as two:
        record = await two.registry.get(receipt.receipt_id)
        assert record["state"] == "confirmed" and record["source"] == "transfer"
        await two.service.validate(await two.registry.prepared(receipt.receipt_id))
        again = await two.service.prepare(file)
        history = await two.registry.get(receipt.receipt_id)
    assert again.receipt_id == receipt.receipt_id and again.remote_id == receipt.remote_id and len(cloud.puts) == 1
    assert history["source"] == "transfer"  # The original upload remains historical evidence.
    assert history["operations"][-1]["state"] == "confirmed"
    assert history["operations"][-1]["evidence"] == "confirmed_already_exists"


@pytest.mark.asyncio
async def test_already_exists_branch_persists_exact_server_reference_without_put(tmp_path):
    cloud, file = DtPlus(), lora_file(tmp_path)
    cloud.stored[sha256(file.read_bytes())] = "server/ref-001"
    async with Session(tmp_path / "state", cloud) as session:
        receipt = await session.service.prepare(file)
    assert receipt.remote_id == "server/ref-001" and not cloud.puts and len(cloud.api_calls) == 1


@pytest.mark.asyncio
async def test_identical_concurrent_prepares_coalesce_and_a_cancelled_waiter_does_not_cancel_them(tmp_path):
    """3B-O7: one negotiation and one transfer; cancelling one waiter leaves the shared operation to the other."""
    cloud, file = DtPlus(), lora_file(tmp_path)
    gate, reached = asyncio.Event(), asyncio.Event()

    async def hold():
        reached.set()
        await gate.wait()

    cloud.on_chunk = hold
    async with Session(tmp_path / "state", cloud) as session:
        first = asyncio.create_task(session.service.prepare(file))
        second = asyncio.create_task(session.service.prepare(file))
        await asyncio.wait_for(reached.wait(), 5)
        await settle_loop()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        gate.set()
        receipt = await asyncio.wait_for(second, 5)
    # Negotiate before the transfer and once after it to learn the server reference; never per caller.
    assert len(cloud.puts) == 1 and len(cloud.api_calls) == 2 and receipt.remote_id.startswith("remote-")


@pytest.mark.asyncio
async def test_changed_bytes_and_other_scopes_never_share_identity_or_in_flight_work(tmp_path):
    state, file = tmp_path / "state", lora_file(tmp_path)
    one, two = DtPlus("https://api.one.invalid"), DtPlus("https://api.two.invalid")
    release, flying = asyncio.Event(), []

    async def hold_one():
        flying.append(1)
        await release.wait()

    async def hold_two():
        flying.append(2)
        await release.wait()

    one.on_chunk, two.on_chunk = hold_one, hold_two
    registry = AssetRegistry(state, writer=True)
    await registry.open()
    first_backend, second_backend = one.backend(), two.backend()
    try:
        a = asyncio.create_task(AssetService(first_backend, registry).prepare(file))
        b = asyncio.create_task(AssetService(second_backend, registry).prepare(file))
        for _ in range(500):
            if len(flying) == 2:
                break
            await asyncio.sleep(0.01)
        # Same content, different API origin / auth client: two simultaneous transfers, no shared operation.
        assert sorted(flying) == [1, 2]
        release.set()
        receipts = await asyncio.gather(a, b)
        assert receipts[0].receipt_id != receipts[1].receipt_id and {r.origin for r in receipts} == {
            one.origin,
            two.origin,
        }
        assert len(one.puts) == len(two.puts) == 1

        one.on_chunk = None
        changed = lora_file(tmp_path, data=b"\x01" + file.read_bytes()[1:])  # same name, same size, other bytes
        changed.rename(file)
        other = await AssetService(first_backend, registry).prepare(file)
        assert other.sha256 != receipts[0].sha256 and other.receipt_id != receipts[0].receipt_id
        assert other.identifier == receipts[0].identifier == "style.ckpt"
    finally:
        await first_backend.aclose()
        await second_backend.aclose()
        await registry.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup,code,outcome,state_after",
    [
        ("connect", "asset_storage_unreachable", RemoteOutcome.NOT_STARTED, "failed"),
        ("timeout", "asset_transfer_deadline", RemoteOutcome.UNKNOWN, "unknown"),
        ("refused", "asset_storage_refused", RemoteOutcome.FAILED, "failed"),
        ("server_error", "asset_storage_error", RemoteOutcome.UNKNOWN, "unknown"),
    ],
)
async def test_transfer_failures_are_classified_and_never_retried_automatically(
    tmp_path, setup, code, outcome, state_after
):
    """3B-O8: definite versus ambiguous transfer failures; exactly one PUT attempt, secrets absent from the error."""
    import httpx

    cloud, file = DtPlus(), lora_file(tmp_path)
    if setup == "connect":
        cloud.connect_error = httpx.ConnectError("SIGNED-QUERY-SECRET refused")
    elif setup == "timeout":
        cloud.storage_error = httpx.ReadTimeout("SIGNED-QUERY-SECRET timed out")
    elif setup == "refused":
        cloud.storage_status = 403
    else:
        cloud.storage_status = 503
    async with Session(tmp_path / "state", cloud) as session:
        with pytest.raises(BackendError) as caught:
            await session.service.prepare(file)
        assert caught.value.failure.code == code and caught.value.failure.remote_outcome == outcome
        assert not any(secret in public(caught.value) for secret in SECRETS)
        (record,) = await session.registry.list()
    assert record["state"] == state_after and record["evidence"] == code and record["remote_id"] is None
    assert cloud.put_attempts == 1


@pytest.mark.asyncio
async def test_ambiguous_transfer_is_reconciled_by_metadata_only_and_never_re_uploaded(tmp_path):
    import httpx

    cloud, file = DtPlus(), lora_file(tmp_path)
    cloud.storage_error, cloud.store_on_put = httpx.ReadTimeout("late"), False
    async with Session(tmp_path / "state", cloud) as session:
        with pytest.raises(BackendError):
            await session.service.prepare(file)
        (record,) = await session.registry.list()
        assert record["state"] == "unknown"
        # The server shows nothing: a definite absence of the file, still without any transfer.
        _, result = await session.service.reconcile(record["asset_id"])
        assert result.status == "upload_required" and len(cloud.puts) == 1
        # The late write did land after all: a consistent authenticated response confirms reuse, again with no PUT.
        cloud.stored[record["sha256"]] = "remote-late"
        stored, result = await session.service.reconcile(record["asset_id"])
    assert result.status == "confirmed" and stored["state"] == "confirmed" and stored["source"] == "reconciled"
    assert stored["remote_id"] == "remote-late" and len(cloud.puts) == 1


@pytest.mark.asyncio
async def test_successful_transfer_with_failed_receipt_write_is_unknown_locally_and_recoverable(tmp_path, monkeypatch):
    cloud, file = DtPlus(), lora_file(tmp_path)
    async with Session(tmp_path / "state", cloud) as session:

        async def broken(*args, **kwargs):
            raise PersistenceError("disk full")

        monkeypatch.setattr(session.registry, "confirm", broken)
        with pytest.raises(BackendError) as caught:
            await session.service.prepare(file)
        failure = caught.value.failure
        assert failure.code == "asset_receipt_unwritten" and failure.remote_outcome == RemoteOutcome.SUCCEEDED
        (record,) = await session.registry.list()
        assert record["state"] == "unknown" and record["evidence"] == "receipt_write_failed"
        monkeypatch.undo()
        stored, result = await session.service.reconcile(record["asset_id"])
    assert result.status == "confirmed" and stored["state"] == "confirmed" and len(cloud.puts) == 1


@pytest.mark.asyncio
async def test_unresolved_intent_survives_restart_as_unknown_and_unknown_is_never_reusable(tmp_path):
    state, cloud, file = tmp_path / "state", DtPlus(), lora_file(tmp_path)
    async with Session(state, cloud) as session:
        # Simulate a process that died after recording intent and starting the transfer.
        from dtflow.core.models import AssetContent

        data = file.read_bytes()
        asset_id, operation = await session.registry.begin(
            AssetContent("lora", "style.ckpt", sha256(data), len(data)), "drawthings-cloud", cloud.origin
        )
        await session.registry.mark(asset_id, operation, "transferring", "transfer_started")
    async with Session(state, cloud) as restarted:
        record = await restarted.registry.get(asset_id)
        assert record["state"] == "unknown" and record["evidence"] == "interrupted"
        assert [op["state"] for op in record["operations"]] == ["unknown"]
        with pytest.raises(ValidationError):
            await restarted.registry.prepared(asset_id)
    assert not cloud.puts


@pytest.mark.asyncio
async def test_file_changed_during_upload_is_unknown_and_records_no_receipt(tmp_path):
    # Larger than any read-ahead buffer (Windows buffers 8 KiB), so the tail is genuinely unread at the first chunk.
    cloud, file = DtPlus(), lora_file(tmp_path, data=bytes(range(256)) * 512)

    async def mutate():
        with file.open("r+b") as handle:
            handle.seek(-16, 2)  # the tail has not been read yet
            handle.write(b"\xff" * 16)

    cloud.on_chunk = mutate
    async with Session(tmp_path / "state", cloud) as session:
        with pytest.raises(BackendError) as caught:
            await session.service.prepare(file)
        (record,) = await session.registry.list()
    assert caught.value.failure.code == "asset_changed" and record["state"] == "unknown" and record["remote_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,response,code",
    [
        (
            "both",
            lambda e: {
                "data": {
                    "alreadyExists": {e["sha256"]: "r"},
                    "signedUrls": {
                        "urls": {e["file"]: {"sha256": e["sha256"], "url": "https://s.invalid/x?SIGNED-QUERY-SECRET"}}
                    },
                }
            },
            "exists",
        ),
        ("neither", lambda e: {"data": {}}, "cloud_byom_response_invalid"),
        (
            "different_server_value",
            lambda e: {
                "data": {
                    "signedUrls": {
                        "urls": {e["file"]: {"sha256": "cc" * 32, "url": "https://s.invalid/x?SIGNED-QUERY-SECRET"}}
                    }
                }
            },
            "upload_required",
        ),
        (
            "http_url",
            lambda e: {
                "data": {
                    "signedUrls": {
                        "urls": {e["file"]: {"sha256": e["sha256"], "url": "http://s.invalid/x?SIGNED-QUERY-SECRET"}}
                    }
                }
            },
            "cloud_byom_response_invalid",
        ),
        (
            "url_reference",
            lambda e: {"data": {"alreadyExists": {e["sha256"]: "https://s.invalid/x?SIGNED-QUERY-SECRET"}}},
            "cloud_byom_response_invalid",
        ),
        ("not_object", lambda e: {"data": "SIGNED-QUERY-SECRET"}, "cloud_byom_response_invalid"),
    ],
)
async def test_negotiation_consumes_usable_evidence_or_reports_sanitized_protocol_failure(
    tmp_path, name, response, code
):
    cloud, file = DtPlus(), lora_file(tmp_path)
    cloud.response = response
    async with Session(tmp_path / "state", cloud) as session:
        if code in ("exists", "upload_required"):
            content = AssetContent("lora", file.name, sha256(file.read_bytes()), file.stat().st_size)
            result = await session.backend.negotiate_asset(content)
            assert result.status == code
            assert not cloud.puts and not await session.registry.list()
            assert not any(secret in repr(result) for secret in SECRETS)
            return
        with pytest.raises(BackendError) as caught:
            await session.service.prepare(file)
        (record,) = await session.registry.list()
    assert caught.value.failure.code == code and caught.value.failure.category == ErrorCategory.PROTOCOL
    assert not any(secret in public(caught.value) for secret in SECRETS)
    assert record["state"] == "failed" and not cloud.puts


@pytest.mark.asyncio
async def test_authentication_refusal_is_reported_without_credentials_and_check_is_unavailable(tmp_path):
    cloud, file = DtPlus(), lora_file(tmp_path)
    cloud.token_status = 401
    async with Session(tmp_path / "state", cloud) as session:
        with pytest.raises(BackendError) as caught:
            await session.service.prepare(file)
        assert caught.value.failure.category == ErrorCategory.AUTHENTICATION
        assert not any(secret in public(caught.value) for secret in SECRETS)
        from dtflow.core.models import AssetContent

        result = await session.service.check(AssetContent("lora", "a.ckpt", "aa" * 32, 5), None)
    assert result.status == "unavailable" and not cloud.puts


@pytest.mark.asyncio
async def test_credentials_and_signed_urls_are_absent_from_logs_durable_state_and_receipts(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.DEBUG)  # a verbose client must still not leak
    state, cloud, file = tmp_path / "state", DtPlus(), lora_file(tmp_path)
    async with Session(state, cloud) as session:
        receipt = await session.service.prepare(file)
        await session.service.reconcile(receipt.receipt_id)
    assert "PUT" in caplog.text  # the transfer really was logged by the client, so the redaction was exercised
    for secret in (API_KEY, "SIGNED-QUERY-SECRET", "FAKE-SHORT-TOKEN"):
        assert secret not in caplog.text, secret
    blobs = [repr(receipt).encode()]
    for path in state.iterdir():
        if path.name.startswith("assets"):
            blobs.append(path.read_bytes())
    for blob in blobs:
        for secret in (*SECRETS, "OBJECT"):
            assert secret.encode() not in blob, secret


@pytest.mark.asyncio
async def test_registry_refuses_newer_schemas_second_writers_and_read_only_changes(tmp_path):
    state = tmp_path / "state"
    reader = AssetRegistry(state)
    await reader.open()
    assert await reader.list() == [] and not (state / "assets.sqlite3").exists()  # reading creates nothing

    writer = AssetRegistry(state, writer=True)
    await writer.open()
    with pytest.raises(PersistenceError):
        await AssetRegistry(state, writer=True).open()  # one preparing/reconciling process at a time
    await writer.aclose()

    with sqlite3.connect(state / "assets.sqlite3") as db:
        db.execute("PRAGMA user_version=99")
    for registry in (AssetRegistry(state), AssetRegistry(state, writer=True)):
        with pytest.raises(PersistenceError, match="no reset or migration"):
            await registry.open()

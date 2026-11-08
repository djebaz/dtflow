"""Disposable inventory cache, the shared model-eligibility rule and the remembered last session.

Persistence is tested here once; the Qt composition and the service seam only add their own behaviour.
"""

import json

import pytest

from dtflow.composition import BackendSelection
from dtflow.core.models import InventoryEntry, InventorySnapshot, LoRASelection, eligible_model_ids
from dtflow.persistence.inventory_cache import CACHE_VERSION, load_inventory, save_inventory
from dtflow.persistence.last_session import SCHEMA_VERSION, load_last_session, save_last_session
from dtflow.services import session
from dtflow.services.credentials import FakeSavedCredentials
from dtflow.ui.generation_presenter import FormValues, form_from_document, form_to_document

ENDPOINT = "grpcs://127.0.0.1:7859"


def snapshot(kind="model", metadata="present", endpoint=ENDPOINT, entries=2):
    items = tuple(
        InventoryEntry(f"file-{n}.ckpt", kind, f"File {n}", None, "listed", "echo.files") for n in range(entries)
    )
    return InventorySnapshot(
        "drawthings-local",
        "server-1",
        "2026-10-05T10:00:00+00:00",
        True,
        items,
        (("model_metadata", metadata),),
        endpoint,
    )


def test_unclassified_files_are_models_only_when_the_server_sent_no_model_metadata():
    assert eligible_model_ids(snapshot("unknown", "absent")) == ("file-0.ckpt", "file-1.ckpt")
    assert eligible_model_ids(snapshot("unknown", "present")) == ()
    assert eligible_model_ids(snapshot("model", "present")) == ("file-0.ckpt", "file-1.ckpt")
    assert eligible_model_ids(snapshot("lora", "present")) == ()


def test_cache_round_trip_misses_and_never_stores_the_key(tmp_path):
    original = snapshot()
    path = save_inventory(tmp_path, "local", original)
    assert path == tmp_path / "cache" / "inventory" / "local_inventory.json"
    assert load_inventory(tmp_path, "local", endpoint=ENDPOINT) == original
    assert load_inventory(tmp_path, "local", endpoint="grpcs://other:1") is None
    document = json.loads(path.read_text(encoding="utf-8"))
    assert load_inventory(tmp_path, "cloud") is None  # absent file
    path.write_text(json.dumps({**document, "cache_version": CACHE_VERSION + 1}), encoding="utf-8")
    assert load_inventory(tmp_path, "local", endpoint=ENDPOINT) is None
    path.write_text("{not json", encoding="utf-8")
    assert load_inventory(tmp_path, "local", endpoint=ENDPOINT) is None
    assert path.exists()  # a miss never repairs or deletes

    cloud = save_inventory(tmp_path, "cloud", snapshot(endpoint=None), credential=("environment", "KEY-ONE-SENTINEL"))
    assert "KEY-ONE-SENTINEL" not in cloud.read_text(encoding="utf-8")
    assert load_inventory(tmp_path, "cloud", credential=("environment", "KEY-ONE-SENTINEL")) is not None
    assert load_inventory(tmp_path, "cloud", credential=("environment", "KEY-TWO")) is None
    assert load_inventory(tmp_path, "cloud", credential=("saved", "KEY-ONE-SENTINEL")) is None


@pytest.mark.asyncio
async def test_service_reads_only_a_matching_cache_and_honours_no_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("DRAWTHINGS_API_KEY", raising=False)  # an environment key would otherwise be the fallback
    local = BackendSelection("local", "127.0.0.1:7859")
    cached = snapshot()
    assert await session.cached_inventory(local, tmp_path) is None
    await session.remember_inventory(local, tmp_path, cached)
    assert await session.cached_inventory(local, tmp_path) == cached
    assert await session.cached_inventory(local, tmp_path, no_cache=True) is None
    assert await session.cached_inventory(BackendSelection("local", "127.0.0.1:1"), tmp_path) is None
    assert await session.cached_inventory(BackendSelection("fake"), tmp_path) is None

    cloud = BackendSelection("cloud")
    await session.remember_inventory(cloud, tmp_path, snapshot(endpoint=None), FakeSavedCredentials("saved-key"))
    assert await session.cached_inventory(cloud, tmp_path, saved=FakeSavedCredentials("saved-key")) is not None
    assert await session.cached_inventory(cloud, tmp_path, saved=FakeSavedCredentials("another-key")) is None
    assert await session.cached_inventory(cloud, tmp_path) is None  # no credential at all is a miss, not an error
    monkeypatch.setenv("DRAWTHINGS_API_KEY", "env-key")  # a saved key wins over the environment, as for generation
    assert await session.cached_inventory(cloud, tmp_path, saved=FakeSavedCredentials("saved-key")) is not None
    assert await session.cached_inventory(cloud, tmp_path, saved=FakeSavedCredentials("another-key")) is None
    await session.remember_inventory(cloud, tmp_path, snapshot(endpoint=None))  # nothing saved: the environment key
    assert await session.cached_inventory(cloud, tmp_path) is not None
    assert await session.cached_inventory(cloud, tmp_path, saved=FakeSavedCredentials("another-key")) is None


def test_last_session_round_trip_and_unusable_files_remember_nothing(tmp_path):
    assert load_last_session(tmp_path) is None
    path = save_last_session(tmp_path, {"provider": "local", "endpoint": "10.0.0.2:7859"})
    assert load_last_session(tmp_path)["endpoint"] == "10.0.0.2:7859"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1, "provider": "cloud"}), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    assert load_last_session(tmp_path) is None
    path.write_text("[1]", encoding="utf-8")
    assert load_last_session(tmp_path) is None
    assert path.read_text(encoding="utf-8") == "[1]" and before  # reads never rewrite


def test_form_document_round_trip_and_tolerant_defaults():
    values = FormValues(
        "a cat", "blurry", "model.ckpt", "42", 768, 640, 30, 5.5, "dpmpp_2m_karras", (LoRASelection("style", 0.8),)
    )
    assert form_from_document(form_to_document(values)) == values
    damaged = form_from_document(
        {"prompt": 3, "steps": "x", "sampler": "nope", "guidance": 4, "loras": [{"identifier": "", "weight": 1}, 7]}
    )
    assert damaged == FormValues(guidance=4.0)
    assert form_from_document(None) == FormValues()

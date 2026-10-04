"""Resolved LoRA cache combines local hash evidence with DT+ listing/BYOM evidence."""

import json

import pytest
from support.live_evidence import save_inventory

from dtflow.core.models import FileIdentity, InventoryEntry, InventorySnapshot, LoRABackendAvailability
from dtflow.persistence.inventory_cache import CACHE_VERSION
from dtflow.services.lora_inventory import refresh_lora_inventory


class Local:
    def __init__(self):
        self.requested = None
        self.exists = True
        self.stale = False

    async def get_inventory(self, refresh=False):
        return InventorySnapshot(
            "drawthings-grpc",
            "local-server",
            "2026-10-04T00:00:00+00:00",
            refresh,
            (
                InventoryEntry(
                    "native.ckpt",
                    "lora",
                    "Native",
                    "z_image",
                    "listed_in_server_inventory",
                    "echo.override",
                    trigger_prefix="native",
                    default_weight=0.8,
                ),
                InventoryEntry(
                    "custom.ckpt",
                    "lora",
                    "Custom",
                    "z_image",
                    "listed_in_server_inventory",
                    "echo.override",
                    trigger_prefix="custom",
                    default_weight=1.0,
                ),
                InventoryEntry(
                    "unprobed.ckpt",
                    "lora",
                    "Unprobed",
                    "z_image",
                    "listed_in_server_inventory",
                    "echo.override",
                    trigger_prefix="other",
                    default_weight=0.6,
                ),
            ),
            diagnostics=(("stale", "true"),) if self.stale else (),
            endpoint="grpcs://192.168.1.142:7859",
        )

    async def get_file_identities(self, identifiers, *, with_sha256=()):
        self.requested = (identifiers, with_sha256)
        return (FileIdentity("custom.ckpt", self.exists, "cc" * 32 if self.exists else None),)


class Cloud:
    async def get_inventory(self, refresh=False):
        return InventorySnapshot(
            "drawthings-cloud",
            "cloud",
            "2026-10-04T00:00:00+00:00",
            refresh,
            (
                InventoryEntry(
                    "native.ckpt",
                    "unknown",
                    "native.ckpt",
                    None,
                    "listed_in_server_inventory",
                    "echo.files",
                ),
            ),
            endpoint="grpcs://compute.drawthings.ai:443",
        )

    async def lookup_lora_assets(self, identities):
        assert identities == (FileIdentity("custom.ckpt", True, "cc" * 32),)
        return {
            "custom.ckpt": LoRABackendAvailability(
                "byom_reusable",
                "dt_cloud.get_byom_urls",
                "cc" * 32,
                ("cc" * 32) + "_remote",
            )
        }


@pytest.mark.asyncio
async def test_refresh_writes_cross_backend_cache_and_bounds_byom_lookup(tmp_path):
    local = Local()
    snapshot, path = await refresh_lora_inventory(
        local,
        Cloud(),
        tmp_path,
        lookup_identifiers=("custom.ckpt",),
    )

    assert local.requested == (("custom.ckpt",), ("custom.ckpt",))
    by_id = {entry.identifier: entry for entry in snapshot.entries}
    assert by_id["native.ckpt"].dt_cloud.availability == "listed_in_server_inventory"
    assert by_id["custom.ckpt"].dt_cloud.availability == "byom_reusable"
    assert by_id["custom.ckpt"].local_grpc.sha256 == "cc" * 32
    assert by_id["unprobed.ckpt"].dt_cloud.availability == "unknown"
    assert by_id["unprobed.ckpt"].local_grpc.sha256 is None
    assert path == tmp_path / "cache" / "inventory" / "lora_inventory.json"

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cache_version"] == CACHE_VERSION == 2
    assert data["local_source"]["endpoint"] == "grpcs://192.168.1.142:7859"
    assert data["cloud_source"]["server_identity"] == "cloud"
    assert data["local_source"]["observed_at_utc"] == "2026-10-04T00:00:00+00:00"
    assert data["resolved_at_utc"] == snapshot.resolved_at_utc
    assert {entry["identifier"] for entry in data["entries"]} == {
        "native.ckpt",
        "custom.ckpt",
        "unprobed.ckpt",
    }

    # Live evidence uses the same fixed names; connection identities stay in JSON.
    run = tmp_path / "test-run"
    for kind, endpoint in (("local", "grpcs://192.168.1.142:7859"), ("cloud", "grpcs://compute.drawthings.ai:443")):
        raw = await (local if kind == "local" else Cloud()).get_inventory()
        evidence = save_inventory(run, kind, endpoint, raw)
        assert evidence == run / "inventory" / f"{kind}_inventory.json"
        assert json.loads(evidence.read_text(encoding="utf-8"))["endpoint"] == endpoint
        changed = save_inventory(run, kind, "grpcs://another-host:443", raw)
        assert changed == evidence
        assert json.loads(changed.read_text(encoding="utf-8"))["endpoint"] == "grpcs://another-host:443"
    assert sorted(item.name for item in (run / "inventory").iterdir()) == [
        "cloud_inventory.json",
        "local_inventory.json",
    ]

    local.stale = True
    stale, _ = await refresh_lora_inventory(local, Cloud(), tmp_path, refresh=False, lookup_identifiers=())
    assert stale.local_source.observed_at_utc == snapshot.local_source.observed_at_utc
    assert stale.resolved_at_utc != stale.local_source.observed_at_utc
    assert not stale.local_source.refreshed and dict(stale.local_source.diagnostics)["stale"] == "true"

    local.exists = False
    absent, _ = await refresh_lora_inventory(local, Cloud(), tmp_path, lookup_identifiers=("custom.ckpt",))
    custom = next(entry for entry in absent.entries if entry.identifier == "custom.ckpt")
    assert custom.local_grpc.availability == "absent" and custom.local_grpc.evidence_source == "grpc.files_exist"
    assert custom.dt_cloud.availability == "unknown"
    assert sorted(item.name for item in path.parent.iterdir()) == ["lora_inventory.json"]

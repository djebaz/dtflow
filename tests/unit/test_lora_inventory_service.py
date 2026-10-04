"""Resolved LoRA cache combines local hash evidence with DT+ listing/BYOM evidence."""

import json

import pytest

from dtflow.core.models import FileIdentity, InventoryEntry, InventorySnapshot, LoRABackendAvailability
from dtflow.services.lora_inventory import refresh_lora_inventory


class Local:
    def __init__(self):
        self.requested = None

    async def get_inventory(self, refresh=False):
        return InventorySnapshot(
            "drawthings-grpc",
            "local-server",
            "2026-10-04T00:00:00+00:00",
            refresh,
            (
                InventoryEntry(
                    "native.ckpt", "lora", "Native", "z_image", "listed_in_server_inventory",
                    "echo.override", trigger_prefix="native", default_weight=0.8,
                ),
                InventoryEntry(
                    "custom.ckpt", "lora", "Custom", "z_image", "listed_in_server_inventory",
                    "echo.override", trigger_prefix="custom", default_weight=1.0,
                ),
            ),
        )

    async def get_file_identities(self, identifiers, *, with_sha256=()):
        self.requested = (identifiers, with_sha256)
        return (FileIdentity("custom.ckpt", True, "cc" * 32),)


class Cloud:
    async def get_inventory(self, refresh=False):
        return InventorySnapshot(
            "drawthings-cloud",
            "cloud",
            "2026-10-04T00:00:00+00:00",
            refresh,
            (InventoryEntry("native.ckpt", "unknown", "native.ckpt", None, "listed_in_server_inventory", "echo.files"),),
        )

    async def lookup_lora_assets(self, identities):
        assert identities == (FileIdentity("custom.ckpt", True, "cc" * 32),)
        return {
            "custom.ckpt": LoRABackendAvailability(
                "byom_reusable", "dt_cloud.get_byom_urls", "cc" * 32, ("cc" * 32) + "_remote"
            )
        }


@pytest.mark.asyncio
async def test_refresh_writes_cross_backend_cache_and_hashes_only_non_cloud_loras(tmp_path):
    local = Local()
    snapshot, path = await refresh_lora_inventory(local, Cloud(), tmp_path)

    assert local.requested == (("custom.ckpt",), ("custom.ckpt",))
    by_id = {entry.identifier: entry for entry in snapshot.entries}
    assert by_id["native.ckpt"].dt_cloud.availability == "listed_in_server_inventory"
    assert by_id["custom.ckpt"].dt_cloud.availability == "byom_reusable"
    assert by_id["custom.ckpt"].local_grpc.sha256 == "cc" * 32
    assert path == tmp_path / "cache" / "inventory" / "lora_inventory.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cache_version"] == 1
    assert {entry["identifier"] for entry in data["entries"]} == {"native.ckpt", "custom.ckpt"}

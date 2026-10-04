"""Resolve LoRA availability across local Draw Things gRPC and direct DT+."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from dtflow.core.models import (
    FileIdentity,
    InventorySnapshot,
    LoRABackendAvailability,
    LoRAInventoryEntry,
    LoRAInventorySnapshot,
)
from dtflow.persistence.inventory_cache import save_lora_inventory


class LocalLoRABackend(Protocol):
    async def get_inventory(self, refresh: bool = False) -> InventorySnapshot: ...

    async def get_file_identities(
        self, identifiers: tuple[str, ...], *, with_sha256: tuple[str, ...] = ()
    ) -> tuple[FileIdentity, ...]: ...


class CloudLoRABackend(Protocol):
    async def get_inventory(self, refresh: bool = False) -> InventorySnapshot: ...

    async def lookup_lora_assets(
        self, identities: tuple[FileIdentity, ...]
    ) -> dict[str, LoRABackendAvailability]: ...


async def resolve_lora_inventory(
    local: LocalLoRABackend,
    cloud: CloudLoRABackend,
    *,
    refresh: bool = True,
    local_snapshot: InventorySnapshot | None = None,
    cloud_snapshot: InventorySnapshot | None = None,
    lookup_identifiers: tuple[str, ...] | None = None,
) -> LoRAInventorySnapshot:
    """Resolve cross-backend LoRA availability from current inventory evidence.

    lookup_identifiers bounds SHA/BYOM work to selected local LoRAs. None
    keeps the production default of resolving every local LoRA absent from Cloud Echo.
    """
    local_snapshot = local_snapshot or await local.get_inventory(refresh=refresh)
    cloud_snapshot = cloud_snapshot or await cloud.get_inventory(refresh=refresh)
    local_loras = tuple(entry for entry in local_snapshot.entries if entry.kind == "lora")
    cloud_ids = {entry.identifier for entry in cloud_snapshot.entries}

    unresolved = tuple(entry.identifier for entry in local_loras if entry.identifier not in cloud_ids)
    selected = set(lookup_identifiers) if lookup_identifiers is not None else set(unresolved)
    needs_lookup = tuple(identifier for identifier in unresolved if identifier in selected)
    identities = (
        await local.get_file_identities(needs_lookup, with_sha256=needs_lookup)
        if needs_lookup
        else ()
    )
    identity_by_id = {item.identifier: item for item in identities}
    byom = await cloud.lookup_lora_assets(identities) if identities else {}

    entries: list[LoRAInventoryEntry] = []
    for entry in local_loras:
        identity = identity_by_id.get(entry.identifier)
        local_status = LoRABackendAvailability(
            entry.availability,
            entry.metadata_source,
            sha256=identity.sha256 if identity is not None else None,
        )
        cloud_status = (
            LoRABackendAvailability("listed_in_server_inventory", "dt_cloud.echo")
            if entry.identifier in cloud_ids
            else byom.get(entry.identifier, LoRABackendAvailability("unknown", "dt_cloud.unresolved"))
        )
        entries.append(
            LoRAInventoryEntry(
                entry.identifier,
                entry.display_name,
                entry.architecture,
                entry.trigger_prefix,
                entry.default_weight,
                local_status,
                cloud_status,
            )
        )

    return LoRAInventorySnapshot(datetime.now(UTC).isoformat(), tuple(entries))


async def refresh_lora_inventory(
    local: LocalLoRABackend,
    cloud: CloudLoRABackend,
    cache_root: Path,
    *,
    refresh: bool = True,
    lookup_identifiers: tuple[str, ...] | None = None,
) -> tuple[LoRAInventorySnapshot, Path]:
    """Refresh local/cloud evidence and write a derived disposable LoRA cache."""
    snapshot = await resolve_lora_inventory(
        local,
        cloud,
        refresh=refresh,
        lookup_identifiers=lookup_identifiers,
    )
    return snapshot, save_lora_inventory(cache_root, snapshot)

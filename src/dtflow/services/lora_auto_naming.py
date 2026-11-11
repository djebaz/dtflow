"""Apply canonical LoRA naming without deriving architecture from filenames.

File-derived short names and format suffixes are suggestions. Exact architecture
comes only from the canonical content record or an independent file detector.
Physical changes are local, content-verified, and never contact DT+.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry, AssetsBusyError
from dtflow.persistence.store import PersistenceError
from dtflow.services.lora_family import validate_converted_lora
from dtflow.services.lora_naming import (
    LoraPrepareMetadata,
    NamingRequest,
    allocate_batch_names,
    architecture_shortcode,
    occupied_from_snapshot,
    suggest_short_name,
    suggested_lora_format,
)
from dtflow.services.lora_store import INDEX_FILE, LoraStore

log = logging.getLogger(__name__)


def _candidate(record: dict, filename: str, sha256: str, size: int):
    """Build one proposal without changing the saved metadata or file."""
    architecture = record.get("declared_version") or record.get("detected_version")
    if not isinstance(architecture, str) or architecture_shortcode(architecture) is None:
        return None
    suggestion = suggest_short_name(filename)
    saved_name = record.get("short_name")
    short_name = saved_name if saved_name is not None else suggestion.name
    if not short_name:
        return None
    hinted_format, hinted_source = suggested_lora_format(filename)
    format_code = record.get("format_code") or hinted_format
    format_source = record.get("format_source") or hinted_source
    request = NamingRequest(
        short_name=short_name,
        exact_architecture=architecture,
        format_code=format_code,
        manually_edited=saved_name is not None,
        alternatives=suggestion.alternatives,
        sha256=sha256,
        size=size,
    )
    return request, format_source


async def auto_prepare_metadata(
    registry: AssetRegistry, source_name: str, sha256: str, size: int, detected_version: str | None
) -> LoraPrepareMetadata | None:
    """Offer deterministic managed naming for an ordinary explicit Prepare.

    Explicit caller-provided names/metadata bypass this helper. Unknown file
    architecture is never filled from filename, checkpoint or catalogue.
    """
    saved = await registry.lora_metadata(sha256, size) or {}
    # Never borrow an earlier file detection if the present bytes do not
    # classify. An explicit user declaration remains independent evidence.
    candidate = _candidate({**saved, "detected_version": detected_version}, source_name, sha256, size)
    if candidate is None:
        if saved.get("managed_identifier"):
            raise ValidationError(
                "This LoRA already has an accepted managed name, but its exact architecture "
                "could not be verified for Re-Prepare."
            )
        return None
    request, format_source = candidate
    occupied = occupied_from_snapshot(await registry.lora_name_snapshot())
    allocation = allocate_batch_names((request,), occupied)[0]
    if not allocation.ready:
        if saved.get("managed_identifier"):
            raise ValidationError(
                "This LoRA already has an accepted managed name but its architecture or naming "
                "metadata has changed. Review it before Re-Prepare."
            )
        return None
    return LoraPrepareMetadata(
        short_name=allocation.short_name,
        exact_architecture=request.exact_architecture,
        format_code=request.format_code,
        format_source=format_source,
        trigger_words=saved.get("trigger_words") or "",
        declared_version=saved.get("declared_version"),
    )


async def normalize_previous_retained(state: Path) -> int:
    """Automatically name eligible previously retained files, locally and safely.

    Only a first canonical name is assigned; existing names and explicit
    short-name/format choices are preserved. A busy Prepare defers this
    opportunistic work. Missing/unknown evidence is left for the future editor.
    """
    state = Path(state)
    store = LoraStore(state)
    if not (store.directory / INDEX_FILE).is_file():
        return 0
    registry = AssetRegistry(state, writer=True)
    try:
        await registry.open()
    except AssetsBusyError:
        return 0
    try:
        records = await registry.lora_records()
        entries = await asyncio.to_thread(store.entries)
        by_identity = {
            (item.get("sha256"), item.get("size")): item
            for item in entries
            if isinstance(item.get("sha256"), str) and type(item.get("size")) is int
        }
        occupied = occupied_from_snapshot(await registry.lora_name_snapshot())
        pending = []
        for record in records:
            sha, size = record["sha256"], record["size"]
            retained = by_identity.get((sha, size))
            if retained is None:
                continue
            accepted = record.get("managed_identifier")
            if isinstance(accepted, str):
                if retained.get("name") != accepted:
                    try:
                        await asyncio.to_thread(store.rename_accepted, sha, size, accepted)
                    except (ValidationError, OSError) as exc:
                        diagnostic_event(
                            log,
                            "assets.retained_name_sync_failed",
                            level=logging.WARNING,
                            data={"sha": sha[:8], "reason": type(exc).__name__},
                        )
                continue
            candidate = _candidate(record, str(retained.get("name") or ""), sha, size)
            if candidate is not None:
                pending.append((record, candidate))
        allocations = allocate_batch_names(tuple(candidate[0] for _, candidate in pending), occupied)
        assigned = 0
        for (record, (request, format_source)), allocation in zip(pending, allocations, strict=True):
            if not allocation.ready:
                continue
            sha, size = record["sha256"], record["size"]
            try:
                retained = await asyncio.to_thread(store.find, sha, size)
                if retained is None:
                    continue
                await asyncio.to_thread(store.verify, retained)
                await asyncio.to_thread(validate_converted_lora, retained.path)
                metadata = LoraPrepareMetadata(
                    short_name=allocation.short_name,
                    exact_architecture=request.exact_architecture,
                    format_code=request.format_code,
                    format_source=format_source,
                    trigger_words=record.get("trigger_words") or "",
                    declared_version=record.get("declared_version"),
                )
                renamed = await asyncio.to_thread(store.rename_accepted, sha, size, metadata.identifier)
                if renamed is not None and await registry.accept_local_managed_name(sha, size, metadata):
                    assigned += 1
            except (ValidationError, OSError, PersistenceError) as exc:
                diagnostic_event(
                    log,
                    "assets.retained_auto_name_failed",
                    level=logging.WARNING,
                    data={"sha": sha[:8], "reason": type(exc).__name__},
                )
        return assigned
    finally:
        await registry.aclose()

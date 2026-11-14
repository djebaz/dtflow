"""Prepare-time safetensors conversion and durable reuse, independent of Qt and DT+.

This service does not upload files or authorize provider work. Its caller owns the
explicit Prepare action and invokes it on the existing AsyncRunner worker.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.diagnostics import asset_ref_data, event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry
from dtflow.services.lora_convert import CONVERTER_VERSION, ConversionProgress, ConversionRequest, convert_lora
from dtflow.services.lora_convert.errors import ConversionCancelled, ConversionError
from dtflow.services.lora_convert.detect import inspect_source
from dtflow.services.lora_convert.registry import lookup
from dtflow.services.lora_digest import DigestCancelled, hash_file
from dtflow.services.lora_family import detect_lora_family, validate_converted_lora
from dtflow.services.lora_naming import (
    LoraPrepareMetadata,
    ManagedName,
    NamingRequest,
    allocate_batch_names,
    occupied_from_snapshot,
)
from dtflow.services.lora_store import LoraStore, RetainedFile, RetainedNameConflict

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedConversion:
    retained: RetainedFile
    reused: bool
    metadata: LoraPrepareMetadata | None = None


def _prepared(
    retained: RetainedFile,
    reused: bool,
    metadata: LoraPrepareMetadata | None,
    *,
    allocated_name: bool = False,
) -> PreparedConversion:
    try:
        final = metadata
        if metadata is not None and (allocated_name or not metadata.replace_existing):
            final = metadata.for_retained_identifier(retained.name)
    except ValueError as exc:
        raise ConversionError(
            "The retained conversion has a non-canonical name for the selected family or format. "
            "Nothing was uploaded or renamed. Restore the retained-store index from a known-good backup "
            "before preparing this source again."
        ) from exc
    return PreparedConversion(retained, reused, final)


async def _occupied_names(state: Path) -> tuple[ManagedName, ...]:
    registry = AssetRegistry(state)
    await registry.open()
    try:
        return occupied_from_snapshot(await registry.lora_name_snapshot())
    finally:
        await registry.aclose()


def _collision_name(
    store: LoraStore,
    metadata: LoraPrepareMetadata,
    sha256: str,
    size: int,
    occupied: tuple[ManagedName, ...],
) -> str:
    # Read current store names only after the output bytes are known. Retention rechecks under its lock.
    indexed = occupied_from_snapshot([{**item, "managed_identifier": item.get("name")} for item in store.entries()])
    requested = ManagedName(metadata.short_name, metadata.exact_architecture, metadata.format_code)
    request = NamingRequest(
        metadata.short_name,
        metadata.exact_architecture,
        metadata.format_code,
        sha256=sha256,
        size=size,
    )
    allocation = allocate_batch_names((request,), (*occupied, *indexed, requested))[0]
    if not allocation.ready or allocation.filename is None:
        raise ConversionError("No safe managed name is available for the converted LoRA; nothing was overwritten.")
    return allocation.filename


def _convert_sync(
    state: Path,
    source: Path,
    family: str,
    managed_name: str,
    progress: Callable[[ConversionProgress], None] | None,
    cancel: threading.Event | None,
    allow_experimental: bool,
    metadata: LoraPrepareMetadata | None = None,
    occupied: tuple[ManagedName, ...] = (),
) -> PreparedConversion:
    start = time.monotonic()
    spec = lookup(family)
    if metadata is not None and (
        metadata.exact_architecture != spec.exact_architecture or metadata.format_code != "F16"
    ):
        raise ConversionError("Prepare metadata disagrees with the selected converter family or output format.")
    if source.suffix.casefold() != ".safetensors":
        raise ConversionError("Select a .safetensors LoRA source.")
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("Conversion stopped before starting.")
    if progress is not None:
        progress(ConversionProgress("reading", 0, 0))
    try:
        source_sha, source_size = hash_file(source, cancel.is_set if cancel is not None else None)
        stat = source.stat()
    except DigestCancelled as exc:
        raise ConversionCancelled("Conversion stopped before starting.") from exc
    except (OSError, ValueError) as exc:
        raise ConversionError("Could not read the selected LoRA source.") from exc

    hint = {
        **asset_ref_data(filename=source.name, source_sha256=source_sha),
        "file_prefix": source.name[:3],  # compatibility with older converter diagnostics
        "family": family,
    }
    store = LoraStore(state)
    if spec.status != "verified":
        diagnostic_event(
            log,
            "lora_convert.prepare_unverified_family",
            level=logging.WARNING,
            data={**hint, "verification": spec.status},
        )
    reused = store.find_converted(source_sha, family, 1.0, CONVERTER_VERSION)
    if reused is not None:
        # Validate the managed identity before metadata writes or reporting successful reuse. Do not rename
        # accepted bytes or reconvert into the same corrupt index as an implicit recovery strategy.
        prepared = _prepared(reused, True, metadata)
        # Reuse existing CKPT bytes, but allow a later source selection to populate descriptive details that
        # older conversions did not store. Metadata is optional: an index write failure cannot block Prepare.
        try:
            inspected = inspect_source(source)
            store.update_source_metadata(reused.sha256, reused.size, inspected.metadata.retained_record())
        except (OSError, ValidationError):
            diagnostic_event(
                log,
                "lora_convert.source_metadata_not_retained",
                level=logging.WARNING,
                data={**hint, "reason": "metadata_backfill_failed"},
            )
        latest = source.stat()
        if (latest.st_mtime_ns, latest.st_size) != (stat.st_mtime_ns, source_size) or stat.st_size != source_size:
            raise ConversionError("The source changed during conversion lookup. Nothing was prepared.")
        if progress is not None:
            progress(ConversionProgress("reused", 1, 1))
        diagnostic_event(
            log,
            "lora_convert.prepare_reused",
            data={
                **hint,
                **asset_ref_data(sha256=reused.sha256),
                "output_sha_prefix": reused.sha256[:12],
                "size": reused.size,
            },
        )
        return prepared

    diagnostic_event(
        log,
        "lora_convert.prepare_started",
        level=logging.DEBUG,
        data={**hint, "source_bytes": source_size, "converter_version": CONVERTER_VERSION},
    )
    staging_root = store.directory / ".convert"
    staging_root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="job-", dir=staging_root))
    try:
        result = convert_lora(
            ConversionRequest(
                source=source,
                destination=folder / "converted.ckpt",
                family=family,
                allow_experimental=allow_experimental,
            ),
            progress=progress,
            cancel=cancel,
        )
        if cancel is not None and cancel.is_set():
            raise ConversionCancelled("Conversion stopped before retaining its output.")
        try:
            validate_converted_lora(result.destination)
        except ValidationError as exc:
            raise ConversionError("The converted checkpoint is not a valid Draw Things LoRA.") from exc
        detected = detect_lora_family(result.destination)
        if detected.family != spec.exact_architecture:
            raise ConversionError(
                "The converted output could not be verified as the selected family. A partial LoRA can be "
                "recognised from its source yet still miss what the converted check requires. Nothing was retained."
            )
        if (
            result.source_sha256 != source_sha
            or stat.st_size != source_size
            or stat.st_mtime_ns != source.stat().st_mtime_ns
        ):
            raise ConversionError("The source changed during conversion. Nothing was retained.")
        record = {
            "converter_version": CONVERTER_VERSION,
            "draw_things_revision": result.provenance.draw_things_revision,
            "family": family,
            "scale": 1.0,
            "verification": spec.status,
            "source": {
                "sha256": source_sha,
                "size": source_size,
                "name": source.name,
                "path": str(source.absolute()),
                "mtime_ns": stat.st_mtime_ns,
                "edge": store._edge(source, source_size),
            },
            "converted_at_utc": datetime.now(UTC).isoformat(),
        }
        # Descriptive header metadata (#414): bounded, display-only, never evidence for the family or the output.
        # It is kept once per retained file (not per conversion record).
        inspected = inspect_source(source)

        def retain(name):
            return store.retain_converted(
                result.destination,
                result.destination_sha256,
                result.destination_size,
                managed_name=name,
                conversion=record,
                source_metadata=inspected.metadata.retained_record(),
            )

        final_name = managed_name
        if metadata is not None:
            # Conversion may take minutes. Read accepted reservations now, not before starting the worker.
            # This is advisory; retention and registry.begin still enforce their authoritative checks.
            occupied = asyncio.run(_occupied_names(state))
        if metadata is not None and any(
            name.short_name.casefold() == metadata.short_name.casefold()
            and name.exact_architecture == metadata.exact_architecture
            and name.format_code == metadata.format_code
            and (name.sha256, name.size) != (result.destination_sha256, result.destination_size)
            for name in occupied
        ):
            final_name = _collision_name(store, metadata, result.destination_sha256, result.destination_size, occupied)
        try:
            retained = retain(final_name)
        except RetainedNameConflict:
            if metadata is None:
                raise  # Legacy callers without authoring metadata retain their strict no-overwrite contract.
            occupied = asyncio.run(_occupied_names(state))
            final_name = _collision_name(store, metadata, result.destination_sha256, result.destination_size, occupied)
            retained = retain(final_name)  # One bounded retry; a second collision fails without overwriting.
        diagnostic_event(
            log,
            "lora_convert.prepare_completed",
            data={
                **hint,
                **asset_ref_data(sha256=retained.sha256),
                "output_sha_prefix": retained.sha256[:12],
                "source_bytes": source_size,
                "output_bytes": retained.size,
                "tensors": result.tensors_written,
                "seconds": round(time.monotonic() - start, 2),
                "converter_version": CONVERTER_VERSION,
                "verification": spec.status,
            },
        )
        return _prepared(retained, False, metadata, allocated_name=final_name != managed_name)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def convert_for_prepare(
    state: Path,
    source: Path,
    family: str,
    managed_name: str,
    *,
    progress: Callable[[ConversionProgress], None] | None = None,
    cancel: threading.Event | None = None,
    allow_experimental: bool = False,
    metadata: LoraPrepareMetadata | None = None,
) -> PreparedConversion:
    """Reuse or convert a source LoRA on a worker; the returned path is retained and verified."""
    if metadata is not None:
        if metadata.identifier != managed_name:
            raise ConversionError("Prepare metadata disagrees with the requested conversion name.")
    return await asyncio.to_thread(
        _convert_sync,
        state,
        source,
        family,
        managed_name,
        progress,
        cancel,
        allow_experimental,
        metadata,
    )

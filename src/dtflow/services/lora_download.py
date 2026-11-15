"""Download a LoRA from a provider, convert it for Draw Things, and retain the result (#463, epic #327).

Qt-free. One explicit call does: refuse an unsupported family (before any bytes), download and verify, convert with
the existing Prepare converter path, keep provenance, and permanently delete the downloaded ``.safetensors`` only
after the converted ``.ckpt`` is retained and verified. It never uploads to DT+ and never starts generation; Prepare
stays an explicit user action on the converted file.

A file the converter refuses (LoKr, LoHa, a mislabeled upload and the like) is kept as "downloaded, not convertible"
with the reason, so it can be inspected, retried or removed.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry
from dtflow.persistence.store import PersistenceError
from dtflow.services import civitai
from dtflow.services.lora_convert import ConversionProgress
from dtflow.services.lora_convert.detect import inspect_source
from dtflow.services.lora_convert.errors import ConversionCancelled, ConversionError
from dtflow.services.lora_convert.registry import FamilySpec
from dtflow.services.lora_convert_prepare import PreparedConversion, convert_for_prepare
from dtflow.services.lora_naming import (
    LoraPrepareMetadata,
    NamingRequest,
    allocate_batch_names,
    occupied_from_snapshot,
    suggest_short_name,
)
from dtflow.services.lora_store import LoraStore

log = logging.getLogger(__name__)

State = Literal["converted", "not_convertible"]
FORMAT = "F16"  # the converter's only output for a .safetensors source; the user can change the name in Prepare


@dataclass(frozen=True, slots=True)
class AcquiredLora:
    """Outcome of one download and conversion. ``converted``: the ``.ckpt`` is retained; otherwise the source is kept."""

    state: State
    version: civitai.CivitaiVersion
    family: FamilySpec
    downloaded: civitai.DownloadedFile
    reason: str | None = None  # why the converter refused, for "downloaded, not convertible"
    prepared: PreparedConversion | None = None
    source_deleted: bool = False
    detector_note: str | None = None  # set when the header detector disagrees with the Civitai label
    trigger_words: str = ""
    warnings: tuple[str, ...] = ()  # non-fatal follow-ups, such as trigger words that could not be saved


def default_trigger_words(version: civitai.CivitaiVersion) -> str:
    return ", ".join(version.trained_words)


def _detector_note(path: Path, family: FamilySpec) -> str | None:
    """A cross-check only; the converter's own tensor matching stays the safety net."""
    try:
        detection = inspect_source(path).detection
    except (ConversionError, OSError, ValueError):
        return None
    if detection.outcome == "detected" and detection.family != family.key:
        from dtflow.services.lora_convert.registry import lookup

        return (
            f"The file's structure looks like {lookup(detection.family).label}, but Civitai lists "
            f"{family.label}. {detection.reason}".strip()
        )
    if detection.outcome == "ambiguous":
        return f"The file's structure does not settle the family ({detection.reason})".strip()
    return None


async def _metadata(state: Path, version: civitai.CivitaiVersion, family: FamilySpec, display_name: str):
    """Default managed name from the existing filename helper and batch naming, F16."""
    suggestion = suggest_short_name(display_name)
    short = suggestion.name or suggest_short_name(version.model_name).name or f"Civitai{version.version_id}"
    registry = AssetRegistry(state)
    await registry.open()
    try:
        occupied = occupied_from_snapshot(await registry.lora_name_snapshot())
    finally:
        await registry.aclose()
    allocation = allocate_batch_names(
        (NamingRequest(short, family.exact_architecture, FORMAT, alternatives=suggestion.alternatives),), occupied
    )[0]
    if not allocation.ready or not allocation.short_name:
        raise ConversionError("No safe managed name is available for this LoRA; nothing was converted.")
    return LoraPrepareMetadata(
        short_name=allocation.short_name,
        exact_architecture=family.exact_architecture,
        format_code=FORMAT,
        format_source="converter_default",
        trigger_words=default_trigger_words(version),
        declared_version=family.exact_architecture,
    )


def _acquisition(
    version: civitai.CivitaiVersion, family: FamilySpec, downloaded: civitai.DownloadedFile, *, deleted: bool
) -> dict:
    return {
        "provider": version.provider,
        "model_id": version.model_id,
        "version_id": version.version_id,
        "file_id": version.file.id,
        "model_name": version.model_name,
        "version_name": version.version_name,
        "creator": version.creator,
        "base_model_label": version.base_model,
        "family": family.key,
        "permissions": version.permissions,
        "tags": list(version.tags),
        "trained_words": list(version.trained_words),
        "trained_words_state": version.trained_words_state,
        "availability": version.availability,
        "original_filename": downloaded.display_name,
        "source_sha256": downloaded.sha256,
        "source_size": downloaded.size,
        "hash_verified": downloaded.hash_verified,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "source_deleted": deleted,
    }


def _delete_source(state: Path, downloaded: civitai.DownloadedFile) -> bool:
    """Permanently delete the downloaded source (no trash, no copy). Only a file this module's download made."""
    directory = civitai.sources_dir(state).resolve()
    path = downloaded.path
    try:
        if path.is_symlink() or path.resolve().parent != directory or path.name != f"{downloaded.sha256}.safetensors":
            return False
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


async def _seed_triggers(state: Path, prepared: PreparedConversion, family: FamilySpec, words: str) -> bool:
    registry = AssetRegistry(state, writer=True, recover=False)
    await registry.open()
    try:
        retained = prepared.retained
        return await registry.seed_lora_triggers(retained.sha256, retained.size, family.exact_architecture, words)
    finally:
        await registry.aclose()


async def convert_downloaded(
    state: Path,
    version: civitai.CivitaiVersion,
    downloaded: civitai.DownloadedFile,
    *,
    progress: Callable[[ConversionProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> AcquiredLora:
    """Convert an already downloaded source with the family its Civitai label maps to."""
    family = civitai.supported_family(version)
    note = await asyncio.to_thread(_detector_note, downloaded.path, family)
    words = default_trigger_words(version)

    def kept(reason: str) -> AcquiredLora:
        diagnostic_event(
            log,
            "lora_download.not_convertible",
            level=logging.WARNING,
            data={"version_id": version.version_id, "family": family.key},
        )
        return AcquiredLora("not_convertible", version, family, downloaded, reason, None, False, note, words)

    try:
        metadata = await _metadata(state, version, family, downloaded.display_name)
        prepared = await convert_for_prepare(
            state,
            downloaded.path,
            family.key,
            metadata.identifier,
            progress=progress,
            cancel=cancel,
            allow_experimental=family.experimental,  # owner decision: SDXL passes the consent automatically
            metadata=metadata,
        )
    except ConversionCancelled:
        raise
    except ConversionError as exc:
        return kept(exc.user_message)
    except (ValidationError, OSError) as exc:
        return kept(str(exc) or "The converted LoRA could not be retained.")

    retained = prepared.retained
    warnings: list[str] = []
    store = LoraStore(state)
    try:
        await asyncio.to_thread(store.verify, retained)
        saved = await asyncio.to_thread(
            store.record_acquisition,
            retained.sha256,
            retained.size,
            _acquisition(version, family, downloaded, deleted=False),
        )
    except (ValidationError, OSError) as exc:
        saved = False
        warnings.append(f"The download provenance could not be saved: {exc}")
    deleted = False
    if saved:
        # Only now, with the .ckpt retained, verified and its provenance written, the source may go.
        deleted = await asyncio.to_thread(_delete_source, state, downloaded)
        if deleted:
            try:
                await asyncio.to_thread(
                    store.record_acquisition,
                    retained.sha256,
                    retained.size,
                    _acquisition(version, family, downloaded, deleted=True),
                )
            except (ValidationError, OSError):
                warnings.append("The source file was deleted but its record still says it is kept.")
        else:
            warnings.append("The downloaded .safetensors could not be deleted; it stays in the download folder.")
    else:
        warnings.append("The downloaded .safetensors was kept because its provenance could not be saved.")
    if words:
        try:
            await _seed_triggers(state, prepared, family, words)
        except (PersistenceError, ValidationError, OSError) as exc:
            warnings.append(f"The trigger words could not be saved for Prepare ({type(exc).__name__}).")
    diagnostic_event(
        log,
        "lora_download.converted",
        data={"version_id": version.version_id, "family": family.key, "source_deleted": deleted},
    )
    return AcquiredLora("converted", version, family, downloaded, None, prepared, deleted, note, words, tuple(warnings))


async def download_and_convert(
    state: Path,
    version: civitai.CivitaiVersion,
    token: str | None = None,
    *,
    manager: civitai.DownloadManager | None = None,
    on_download: Callable[[civitai.DownloadProgress], None] | None = None,
    on_convert: Callable[[ConversionProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> AcquiredLora:
    """The one Download action: refuse an unsupported family, download, verify, convert, retain."""
    civitai.supported_family(version)  # refused here and again inside download(), before any bytes
    if manager is not None:
        handle = manager.submit(version, token, on_download)
        downloaded = await asyncio.wrap_future(handle.future)
    else:
        downloaded = await asyncio.to_thread(
            civitai.download, version, state, token, on_progress=on_download, cancel=cancel
        )
    return await convert_downloaded(state, version, downloaded, progress=on_convert, cancel=cancel)

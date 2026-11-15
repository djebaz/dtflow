"""Registry of downloaded LoRA source files (#464, epic #327).

Qt-free. Provenance rows live in ``assets.sqlite3`` (see ``persistence/lora_sources``); the bytes live in
``<state>/lora_sources/<sha256>.safetensors``. Every write here uses a short scoped writer (``recover=False``, so other
operations are left alone) that is opened only when needed, never while a download streams, and is retried once if
another process holds the writer lock. Functions are async: call them from a worker thread with ``asyncio.run``.

Deleting a source file is always permanent (``unlink``; never the trash or recycle bin).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import time
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.assets import AssetRegistry, AssetsBusyError
from dtflow.persistence.lora_sources import FILE_DELETED, SourceRecord
from dtflow.services.civitai import CivitaiVersion, DownloadedFile, sources_dir

log = logging.getLogger(__name__)

ORPHAN_GRACE_SECONDS = 3600.0  # a file this young may be a download whose row is about to be written
RETRY_DELAY_SECONDS = 1.0
_HASH_NAME = re.compile(r"([0-9a-f]{64})\.safetensors\Z")


class SourceRegistryBusy(ValidationError):
    """The asset writer lock stayed busy; the verified file is kept in ``lora_sources/`` and nothing is lost."""


async def _scoped(state: Path, action):
    """Run ``action(registry)`` under a short scoped writer; one retry when the writer lock is busy."""
    for attempt in (0, 1):
        registry = AssetRegistry(state, writer=True, recover=False)
        try:
            await registry.open()
            return await action(registry)
        except AssetsBusyError as exc:
            if attempt:
                raise SourceRegistryBusy(
                    "Another DTFlow process is using the asset database. The downloaded file is kept; "
                    "registering it will be retried."
                ) from exc
            await asyncio.sleep(RETRY_DELAY_SECONDS)
        finally:
            await registry.aclose()


def civitai_record(
    version: CivitaiVersion,
    downloaded: DownloadedFile,
    downloaded_at_utc: str,
    *,
    family: str | None = None,
    family_evidence: str = "",
) -> SourceRecord:
    """Normalised provenance for a Civitai download. ``family`` is DTFlow's own family for the original label
    (looked up by the caller in the families file); the label itself is kept apart and unchanged."""
    file = version.file
    return SourceRecord(
        sha256=downloaded.sha256,
        size=downloaded.size,
        provider=version.provider,
        downloaded_at_utc=downloaded_at_utc,
        model_id=version.model_id,
        version_id=version.version_id,
        file_id=file.id,
        model_name=version.model_name,
        version_name=version.version_name,
        creator=version.creator,
        original_filename=downloaded.display_name,
        license=dict(version.permissions),
        tags=version.tags,
        trigger_words=version.trained_words,
        trigger_words_state=version.trained_words_state,
        base_model_label=version.base_model,
        classified_family=family,
        classification_source="provider" if family else "unknown",
        classification_evidence=family_evidence or (f"{version.provider} base model label" if family else ""),
        hash_verified=downloaded.hash_verified,
    )


async def register_source(state: Path, record: SourceRecord) -> None:
    """Persist provenance for a verified download (refreshes an existing row without losing a user correction)."""
    await _scoped(Path(state), lambda registry: registry.record_source(record))
    diagnostic_event(log, "lora_source.registered", data={"provider": record.provider})


async def list_sources(state: Path) -> tuple[dict, ...]:
    """Read-only; empty when the database or the table does not exist yet."""
    registry = AssetRegistry(Path(state))
    await registry.open()
    return await registry.sources()


async def get_source(state: Path, sha256: str) -> dict | None:
    registry = AssetRegistry(Path(state))
    await registry.open()
    return await registry.source(sha256)


async def mark_not_convertible(state: Path, sha256: str, reason: str, *, refused: bool = False) -> None:
    """Keep the source file and record why conversion failed (``refused=True``: the converter declined the file)."""
    await _scoped(
        Path(state),
        lambda registry: registry.update_source(sha256, state="refused" if refused else "failed", detail=reason),
    )


async def correct_family(state: Path, sha256: str, family: str | None) -> bool:
    """Record a user correction of the family (``None`` clears it); the provider label and classification stay."""
    return await _scoped(Path(state), lambda registry: registry.declare_source_family(sha256, family))


def _delete_file(path: Path) -> bool:
    """Permanently delete a regular file (never a link); True when a file was removed."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        raise ValidationError("A stored source name is not a regular file; nothing was deleted.")
    path.unlink()
    return True


async def finish_conversion(state: Path, sha256: str, converted_sha256: str, retained: Path) -> bool:
    """Record a successful conversion and then permanently delete the ``.safetensors``.

    Call only after the converted ``.ckpt`` is retained and verified: ``retained`` must be that file and is checked to
    exist before anything is deleted. The row stays as provenance, marked "source file deleted". Returns True when a
    source file was deleted."""
    try:
        info = Path(retained).lstat()
    except OSError as exc:
        raise ValidationError("The converted file is not in place; the source was kept.") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValidationError("The converted file is not in place; the source was kept.")
    path = sources_dir(Path(state)) / f"{sha256}.safetensors"

    async def action(registry: AssetRegistry) -> bool:
        if not await registry.update_source(
            sha256, state="converted", detail="", converted=(converted_sha256, info.st_size)
        ):
            raise ValidationError("No source row exists for this file; it was kept.")
        deleted = _delete_file(path)
        await registry.update_source(sha256, file_state=FILE_DELETED)
        return deleted

    return await _scoped(Path(state), action)


async def remove_source(state: Path, sha256: str) -> bool:
    """Remove a kept source (failed or refused conversion): permanently delete its file and its row."""
    path = sources_dir(Path(state)) / f"{sha256}.safetensors"

    async def action(registry: AssetRegistry) -> bool:
        row = await registry.source(sha256)
        if row is None:
            return False
        _delete_file(path)
        return await registry.delete_source(sha256)

    return await _scoped(Path(state), action)


async def sweep_orphans(state: Path, *, grace_seconds: float = ORPHAN_GRACE_SECONDS) -> int:
    """Startup sweep: permanently delete ``<sha256>.safetensors`` files in ``lora_sources/`` that no source row
    references and that are older than the grace period. Never touches a referenced file (including a kept failed or
    refused one), ``.staging``, links or other names. Best effort; returns the number deleted."""
    folder = sources_dir(Path(state))
    removed = 0
    try:
        if not folder.is_dir() or folder.is_symlink():
            return 0
        referenced = {row["sha256"] for row in await list_sources(state)}
        with os.scandir(folder) as entries:
            for entry in entries:
                match = _HASH_NAME.match(entry.name)
                if not match or match.group(1) in referenced:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and time.time() - info.st_mtime > grace_seconds:
                        Path(entry.path).unlink()
                        removed += 1
                except OSError:
                    continue
    except (OSError, ValidationError):
        pass
    diagnostic_event(log, "lora_source.orphan_sweep", data={"removed": removed})
    return removed

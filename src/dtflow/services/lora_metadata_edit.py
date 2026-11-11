"""Local, single-record LoRA metadata reads and edits for Connections (#373).

Editing changes only the canonical local metadata row: it never uploads, renames a prepared or retained file, changes
an accepted managed identifier, or alters a receipt. The asset writer lock is held only for the edit; a busy writer
(an active Prepare) refuses the edit instead of waiting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dtflow.core.models import ValidationError
from dtflow.persistence.assets import AssetRegistry, AssetsBusyError

_ARCHITECTURE = re.compile(r"[a-z0-9_.-]+")


async def read_lora_rows(state: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """Canonical metadata keyed by content identity; no provider call and no write."""
    registry = AssetRegistry(Path(state))
    await registry.open()
    try:
        records = await registry.lora_records()
    finally:
        await registry.aclose()
    return {(record["sha256"], record["size"]): record for record in records}


async def save_lora_metadata(
    state: Path,
    sha256: str,
    size: int,
    *,
    declared_version: str | None,
    short_name: str | None,
    format_code: str | None,
    trigger_words: str,
) -> dict[str, Any]:
    """Save one record's editable fields. ``None`` clears the optional architecture declaration or short name."""
    if declared_version is not None and not _ARCHITECTURE.fullmatch(declared_version):
        raise ValidationError("Choose an exact Draw Things architecture version.")
    registry = AssetRegistry(Path(state), writer=True)
    try:
        await registry.open()
    except AssetsBusyError as exc:
        raise ValidationError("A LoRA is being prepared right now. Try again when it finishes.") from exc
    try:
        current = await registry.lora_metadata(sha256, size)
        if current is None:
            raise ValidationError("This LoRA has no local metadata record yet; Prepare it first.")
        changes: dict[str, Any] = {
            "declared_version": declared_version,
            "short_name": short_name,
            "trigger_words": trigger_words.strip(),
        }
        if format_code is None:
            changes.update(format_code=None, format_source=None)
        elif format_code != current.get("format_code"):
            changes.update(format_code=format_code, format_source="user")
        return await registry.update_lora_metadata(sha256, size, **changes)
    finally:
        await registry.aclose()

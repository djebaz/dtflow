"""Read-only family detection from converted Draw Things LoRA tensor headers.

Only SQLite tensor names and shape metadata are inspected, never weights. A
filename, current checkpoint, or generic hidden-size match is not proof of
family. Unknown and ambiguous structures remain explicitly unknown.

Z-Image and Krea 2 signatures are grounded in owner-provided converted CKPT
reports (#369). Additional architectures require their own converted examples
before their family can be used for a blocking check or a DT+ override.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from dataclasses import dataclass
from collections.abc import Iterable

from dtflow.core.models import ValidationError
from dtflow.services.lora_store import LoraStore
from pathlib import Path

MAX_TENSORS = 20_000
_SHAPE = re.compile(r"^\s*[\[(]\s*\d+(?:\s*,\s*\d+)*\s*[\])]\s*$")
_DIT_LORA_NAME = re.compile(r"^__dit__\[t-([^\]]+)\]__(?:up|down)__$")


@dataclass(frozen=True, slots=True)
class LoraFamily:
    """Family supported by file evidence, or None when not established."""

    family: str | None = None
    source: str = "unknown"


def _dimensions(value: object) -> tuple[int, ...]:
    """Decode shape metadata only, never the tensor's weight payload.

    Draw Things s4nnc stores dimensions as little-endian 32-bit integers in
    the `dim` column; other converted SQLite files may use JSON text.
    """
    if isinstance(value, bytes):
        if not value or len(value) > 64 or len(value) % 4:
            return ()
        values = struct.unpack(f"<{len(value) // 4}i", value)
        return tuple(d for d in values if d > 0)
    if isinstance(value, str) and _SHAPE.fullmatch(value):
        try:
            values = json.loads(value.replace("(", "[").replace(")", "]"))
            if isinstance(values, list) and all(type(d) is int and d > 0 for d in values):
                return tuple(values)
        except ValueError:
            pass
    return ()


def detect_lora_family(path: Path) -> LoraFamily:
    """Inspect converted SQLite metadata only; never guess from its file name.

    A family is returned only when independent architectural landmarks agree.
    Unknown or unsupported formats remain valid LoRAs for existing workflows.
    """
    path = Path(path)
    if not path.is_file():
        return LoraFamily()
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=1.0) as db:
            db.execute("PRAGMA query_only=ON")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tensors'"
            ).fetchone():
                return LoraFamily()
            columns = {row[1] for row in db.execute("PRAGMA table_info(tensors)")}
            name_column = next((c for c in ("name", "key", "tensor_name") if c in columns), None)
            shape_column = next((c for c in ("dim", "shape", "dims", "dimensions") if c in columns), None)
            if name_column is None:
                return LoraFamily()
            # Names come from a fixed whitelist of schema columns, not from the DB.
            select = name_column + (", " + shape_column if shape_column else "")
            rows = db.execute(f"SELECT {select} FROM tensors LIMIT ?", (MAX_TENSORS + 1,)).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return LoraFamily()
    if not rows or len(rows) > MAX_TENSORS:
        return LoraFamily()

    names = [
        str(row[0]).lower()
        for row in rows
        if isinstance(row[0], str)
    ]
    # A full checkpoint can expose the same tensor dimensions but is not an
    # adapter. Inspect only converted Draw Things LoRA up/down tensor names.
    modules = [
        match.group(1)
        for name in names
        if (match := _DIT_LORA_NAME.fullmatch(name)) is not None
    ]
    if not modules:
        return LoraFamily()
    dimensions = {
        dimension
        for row in rows
        if len(row) > 1 and isinstance(row[0], str)
        and _DIT_LORA_NAME.fullmatch(row[0].lower())
        for dimension in _dimensions(row[1])
    }

    # The converted NNC names encode modules as "t-<module>-<layer>-...".
    # Requiring "layers.29"/"blocks.27" misses *all* reported files; missing
    # ada_ln also does not invalidate attention/FFN-only Z-Image adapters.
    # Match combinations of independent module and width evidence, never
    # only a model width or a filename.
    z_image = (
        3840 in dimensions
        and 10240 in dimensions
        and any(re.fullmatch(r"ffn-\d+-ffn_(?:gate|down|up)_proj-\d+-\d+", m) for m in modules)
        and any(re.fullmatch(r"(?:k|q|v|o)-\d+-\d+", m) for m in modules)
    )
    krea_2 = (
        6144 in dimensions
        and 1536 in dimensions
        and 16384 in dimensions
        and any(re.fullmatch(r"to_(?:gate|k|q|v|out)-\d+-\d+", m) for m in modules)
        and any(m.startswith(("text_fusion_", "textfusion_", "txtfusion_")) for m in modules)
    )
    if z_image != krea_2:
        return LoraFamily("z_image" if z_image else "krea_2", "file")
    return LoraFamily()


def retained_families(state: Path, identities: Iterable[tuple[str, int]]) -> dict[tuple[str, int], LoraFamily]:
    """Inspect legacy retained LoRAs locally, once per content identity; never upload.

    Used only to enrich read-only lists and preflight. Dispatch separately
    verifies the retained checksum before trusting these bytes for a request.
    Missing or unreadable files stay Unknown without hiding other receipts.
    """
    store = LoraStore(state)
    result: dict[tuple[str, int], LoraFamily] = {}
    for sha256, size in dict.fromkeys(identities):
        try:
            retained = store.find(sha256, size)
            detected = LoraFamily()
            if retained is not None and retained.path.stat().st_size == size:
                detected = detect_lora_family(retained.path)
                if detected.family is not None:
                    store.verify(retained)
            result[(sha256, size)] = detected
        except (OSError, ValidationError):
            result[(sha256, size)] = LoraFamily()
    return result

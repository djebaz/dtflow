"""Versioned, display-only diagnostic field projection.

Pure functions shared by the Logs row/detail presentation. Raw record bytes,
numeric types and semantic event/severity meanings are never modified.
"""

from __future__ import annotations

import json

PROJECTION_VERSION = 1
_ORDER = (
    "wall_elapsed_ms",
    "elapsed_ms",
    "wall_total_ms",
    "build_wall_ms",
    "sql_wall_ms",
    "sql_cpu_ms",
    "cpu_total_ms",
    "mean_ms",
    "p95_ms",
    "max_ms",
    "max_slice_ms",
    "samples",
    "elements",
    "returned",
    "mounted",
    "added",
    "removed",
    "outputs",
    "mode",
    "scale",
    "source",
    "direction",
    "offset",
    "query_busy",
    "preparation_busy",
    "retryable",
)


def characteristic_order(name: str) -> tuple:
    """Sort known semantic keys first, with stable deterministic extensions."""
    if name in _ORDER:
        return 0, _ORDER.index(name), ""
    if name.endswith("_cpu_ms"):
        return 1, 0, name.casefold()
    if name.endswith("_ms"):
        return 2, 0, name.casefold()
    return 3, 0, name.casefold(), name


def characteristics(record: dict, *, limit: int | None = None, preview_bytes: int = 96):
    data = record.get("data", {})
    if type(data) is not dict:
        return (), 0
    keys = sorted(data, key=characteristic_order)
    visible = keys if limit is None else keys[:limit]
    result = []
    for key in visible:
        value = data[key]
        if type(value) in (dict, list):
            display = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif value is None:
            display = "null"
        elif type(value) is bool:
            display = "true" if value else "false"
        else:
            display = str(value)
        if preview_bytes >= 0:
            encoded = display.encode("utf-8")
            if len(encoded) > preview_bytes:
                display = encoded[:preview_bytes].decode("utf-8", "ignore") + "…"
        result.append((key, display))
    return tuple(result), max(0, len(keys) - len(visible))


def stable_color_index(value: str, count: int = 8) -> int:
    """FNV-1a over UTF-8, independent of Python hash randomization and row order."""
    digest = 2166136261
    for byte in value.casefold().encode("utf-8"):
        digest = (digest ^ byte) * 16777619 & 0xFFFFFFFF
    return digest % count

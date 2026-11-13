"""Versioned, display-only diagnostic field projection.

Pure functions shared by the Logs row/detail presentation. Raw record bytes,
numeric types and semantic event/severity meanings are never modified.
"""

from __future__ import annotations

import json
import math

PROJECTION_VERSION = 3
# Display-only groups, using emitted names (including retained compatibility aliases).
# A field's presence is not an outcome classifier: e.g. a checksum comparison is not a failure.
_PRIORITY_GROUPS = (
    ("reason", "error_code", "code", "error_type", "server_message", "interpretation_message"),
    (
        "asset_sha_prefix",
        "asset_id",
        "source_sha_prefix",
        "output_sha_prefix",
        "operation_id",
        "request_id",
    ),
    (
        "outcome",
        "remote_outcome",
        "status",
        "state",
        "receipt",
        "verification",
        "evidence",
        "retryable",
        "http_status",
        "submission_stage",
    ),
    (
        "operation",
        "phase",
        "stage",
        "family",
        "backend",
        "backend_id",
        "filename_prefix",
        "file_prefix",
        "mode",
        "source",
        "direction",
        "scale",
        "offset",
    ),
    (
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
        "seconds",
        "seconds_per_mib",
        "inspection",
        "source_bytes",
        "output_bytes",
        "size",
        "size_mib",
        "samples",
        "elements",
        "returned",
        "mounted",
        "added",
        "removed",
        "outputs",
        "tensors",
        "item",
        "total",
        "count",
    ),
    ("credential_id", "key_alias", "key_hint", "key_source", "query_busy", "preparation_busy"),
)


def characteristic_order(name: str) -> tuple:
    """Fixed grouped order for full field lists and nested hover data, never input JSON order."""
    for group, fields in enumerate(_PRIORITY_GROUPS):
        if name in fields:
            return group, fields.index(name), "", ""
    # Unknown timing keys remain measurements, not promoted above identity/result fields.
    if name.endswith("_cpu_ms"):
        return 4, len(_PRIORITY_GROUPS[4]), name.casefold(), name
    if name.endswith("_ms"):
        return 4, len(_PRIORITY_GROUPS[4]) + 1, name.casefold(), name
    return len(_PRIORITY_GROUPS), 0, name.casefold(), name


def characteristics(record: dict, *, limit: int | None = None, preview_bytes: int = 96):
    data = record.get("data", {})
    if type(data) is not dict:
        return (), 0
    keys = sorted(data, key=characteristic_order)
    if limit is not None:
        # Reserve coverage before filling scarce preview slots: one cause, one primary
        # reference and up to two result fields (e.g. failed + remote not_started).
        # This selects membership only; the visible subset retains the same fixed order
        # as full/nested fields. No value/event inference or synthetic characteristics.
        leading = []
        for group, count in ((0, 1), (1, 1), (2, 2)):
            leading.extend([key for key in _PRIORITY_GROUPS[group] if key in data][:count])
        keys = leading + [key for key in keys if key not in leading]
    visible = keys if limit is None else sorted(keys[:limit], key=characteristic_order)
    result = []
    for key in visible:
        value = data[key]
        if key.endswith("_ms") and type(value) is float and math.isfinite(value):
            # Human-facing timing chips use whole milliseconds; records stay exact.
            display = str(round(value))
        elif type(value) in (dict, list):
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

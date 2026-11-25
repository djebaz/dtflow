"""Shared byte limit for script sources."""

from pathlib import Path

MAX_SOURCE_BYTES = 1_048_576


def read_source_bytes(path: Path) -> bytes:
    with path.open("rb") as source:
        data = source.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Script source is too large (limit 1 MiB).")
    return data

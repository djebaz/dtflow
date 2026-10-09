"""Bounded, rebuildable index of exported copies; no source paths or application-state writes."""

import json
import re
from pathlib import Path, PurePosixPath

from dtflow.services.artifacts import atomic_write

FILENAME = ".dtflow-export-index.json"
CACHE_VERSION = 1
MAX_ENTRIES = 10_000
MAX_BYTES = 8 * 1024 * 1024


def load_export_index(destination: Path) -> dict:
    try:
        with (destination / FILENAME).open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {}
    if len(data) > MAX_BYTES:
        raise ValueError("Export index exceeds its size limit.")
    document = json.loads(data)
    if (
        not isinstance(document, dict)
        or type(document.get("cache_version")) is not int
        or document["cache_version"] != CACHE_VERSION
    ):
        raise ValueError("Unsupported export index cache version.")
    files = document.get("files")
    if not isinstance(files, dict) or len(files) > MAX_ENTRIES:
        raise ValueError("Invalid export index entries.")
    for name, record in files.items():
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or ":" in name
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) not in (1, 2)
            or (len(path.parts) == 2 and path.parts[0] not in ("noise", "blank"))
            or path.suffix.lower() not in (".jpg", ".jpeg")
            or not isinstance(record, dict)
        ):
            raise ValueError("Invalid export index path.")
        for field in ("size", "mtime_ns"):
            if type(record.get(field)) is not int or record[field] < 0:
                raise ValueError("Invalid export index fingerprint.")
        sidecar = record.get("sidecar")
        if sidecar is not None and (
            not isinstance(sidecar, dict)
            or any(type(sidecar.get(field)) is not int or sidecar[field] < 0 for field in ("size", "mtime_ns"))
        ):
            raise ValueError("Invalid export index sidecar fingerprint.")
        identity, checksum = record.get("identity"), record.get("source_png_sha256")
        if identity is not None and (not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}:\d+", identity)):
            raise ValueError("Invalid export index identity.")
        if checksum is not None and (not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum)):
            raise ValueError("Invalid export index checksum.")
        if (identity is None) != (checksum is None):
            raise ValueError("Incomplete export index identity.")
    return files


def save_export_index(destination: Path, files: dict) -> None:
    if len(files) > MAX_ENTRIES:
        raise ValueError("Export index exceeds its entry limit.")
    encoded = (
        json.dumps(
            {"cache_version": CACHE_VERSION, "files": files}, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("Export index exceeds its size limit.")
    atomic_write(destination / FILENAME, encoded)

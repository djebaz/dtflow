"""Headless saved-output services shared by CLI and Qt; no backend or presentation imports."""

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dtflow.core.gallery import GalleryDetail, GalleryItem, GalleryPage, GalleryQuery
from dtflow.core.models import ValidationError
from dtflow.persistence import gallery as storage

MAX_METADATA_BYTES = 1024 * 1024
SECRET = re.compile(r"api.?key|token|password|secret|authorization|cookie|credential|signed.?url|base64", re.I)


def safe_metadata(value):
    """Retain inspection data while excluding credential-like fields and URL credentials/query strings."""
    if isinstance(value, dict):
        return {str(k): "[redacted]" if SECRET.search(str(k)) else safe_metadata(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        # Resolution evidence is persisted as key/value pairs, so protect those keys too.
        if len(value) == 2 and isinstance(value[0], str) and SECRET.search(value[0]):
            return [value[0], "[redacted]"]
        return [safe_metadata(x) for x in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        try:
            parts = urlsplit(value)
            return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
        except ValueError:
            return "[redacted URL]"
    return value


def item_from_record(record: dict) -> GalleryItem:
    try:
        request = json.loads(record["request_json"])
        loras = tuple((str(x["identifier"]), float(x["weight"])) for x in request.get("loras", []))
    except (ValueError, KeyError, TypeError):
        request, loras = {}, ()
    issue, size = "", None
    try:
        path = Path(record["path"])
        if not path.is_file():
            raise OSError
        size = path.stat().st_size
    except OSError:
        issue = "Image is missing or unreadable."
    return GalleryItem(
        record["identity"],
        record["attempt_id"],
        record["request_id"],
        record["path"],
        record["metadata_path"],
        record["saved_at_utc"],
        record["backend_id"],
        record["model"] or "",
        record["prompt"] or "",
        record["negative"] or "",
        record["width"],
        record["height"],
        loras,
        json.dumps(safe_metadata(request), allow_nan=False),
        size,
        issue,
    )


async def query_outputs(state: Path, query: GalleryQuery | None = None) -> GalleryPage:
    """Discover only committed final references; originals and execution state are never modified."""
    query = query or GalleryQuery()

    def read():
        records, total = storage.query_records(state, query)
        return GalleryPage(tuple(item_from_record(x) for x in records), total, query.offset, query.limit)

    return await asyncio.to_thread(read)


async def locate_output(state: Path, query: GalleryQuery, identity: str) -> int | None:
    return await asyncio.to_thread(storage.locate_record, state, query, identity)


async def filter_choices(state: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return await asyncio.to_thread(storage.facets, state)


def load_detail(item: GalleryItem) -> GalleryDetail:
    """Read a bounded sidecar, checking its recorded identity; fall back to explicit durable evidence."""
    try:
        with Path(item.metadata_path).open("rb") as stream:
            encoded = stream.read(MAX_METADATA_BYTES + 1)
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError
        metadata = json.loads(encoded)
        if (
            not isinstance(metadata, dict)
            or metadata.get("schema_version") != 1
            or metadata.get("attempt_id") != item.attempt_id
        ):
            raise ValueError
        artifacts = metadata.get("artifacts", [])
        artifact = next((x for x in artifacts if x.get("path") == item.path), None)
        if not artifact or artifact.get("designation") != "final":
            raise ValueError
        safe = json.dumps(safe_metadata(metadata), ensure_ascii=False, indent=2, allow_nan=False)
        return GalleryDetail(item, safe, item.issue)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        evidence = {
            "attempt_id": item.attempt_id,
            "request_id": item.request_id,
            "backend_id": item.backend_id,
            "request": json.loads(item.request_json),
            "saved_at_utc": item.saved_at_utc,
            "metadata_status": "missing, unreadable, oversized or inconsistent",
        }
        return GalleryDetail(
            item,
            json.dumps(evidence, ensure_ascii=False, indent=2),
            "Metadata is unavailable or inconsistent; showing durable request evidence.",
        )


async def inspect_output(state: Path, identity: str) -> GalleryDetail:
    def read():
        record = storage.get_record(state, identity)
        if record is None:
            raise ValidationError("Saved output does not exist.")
        return load_detail(item_from_record(record))

    return await asyncio.to_thread(read)

"""Headless saved-output services shared by CLI and Qt; no backend or presentation imports."""

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dtflow.core.gallery import GalleryDetail, GalleryItem, GalleryPage, GalleryQuery
from dtflow.core.models import ValidationError
from dtflow.persistence import gallery as storage
from dtflow.persistence.original_io import original_images_lock
from dtflow.services.artifact_paths import ArtifactReadPaths, foreign_windows_path
from dtflow.services.gallery_read_alias import resolve_output_read

MISSING_ISSUE = "Image file is missing."
UNREADABLE_ISSUE = "Image is unreadable or its location is unavailable."


def _confirmed_missing(path: Path) -> bool:
    """Presentation only: the file is gone while its attempt folder or the outputs folder is reachable.

    An unmapped drive, unlistable parent or permission failure stays "unavailable". This is never removal
    authority; Delete re-proves absence with its own managed-state checks.
    """
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    else:
        return False
    for folder in (path.parent, path.parent.parent):
        try:
            if folder.is_dir():
                with os.scandir(folder):
                    return True
        except OSError:
            return False
    return False


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


def item_from_record(record: dict, paths: ArtifactReadPaths | None = None, *, state: Path | None = None) -> GalleryItem:
    try:
        request = json.loads(record["request_json"])
        loras = tuple((str(x["identifier"]), float(x["weight"])) for x in request.get("loras", []))
    except (ValueError, KeyError, TypeError):
        request, loras = {}, ()
    issue, size = "", None
    paths = paths or ArtifactReadPaths.discover()
    access_path, access_metadata_path = "", ""
    try:
        if state is None:
            access_path = paths.resolve(record["path"])
        else:
            access_path, access_metadata_path, _ = resolve_output_read(state, record, paths=paths)
        path = Path(access_path)
        if not path.is_file():
            raise OSError
        size = path.stat().st_size
    except OSError:
        try:
            candidate = access_path or paths.resolve(record["path"])
        except OSError:
            candidate = ""
        issue = MISSING_ISSUE if candidate and _confirmed_missing(Path(candidate)) else UNREADABLE_ISSUE
    if not access_metadata_path:
        try:
            access_metadata_path = paths.resolve(record["metadata_path"])
        except OSError:
            pass  # load_detail retains durable evidence when the sidecar is unavailable.
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
        access_path,
        access_metadata_path,
    )


async def query_outputs(state: Path, query: GalleryQuery | None = None) -> GalleryPage:
    """Discover only committed final references; originals and execution state are never modified."""
    query = query or GalleryQuery()

    def read():
        records, total = storage.query_records(state, query)
        paths = ArtifactReadPaths.discover()
        return GalleryPage(
            tuple(item_from_record(x, paths, state=state) for x in records), total, query.offset, query.limit
        )

    return await asyncio.to_thread(read)


async def locate_output(state: Path, query: GalleryQuery, identity: str) -> int | None:
    return await asyncio.to_thread(storage.locate_record, state, query, identity)


def copy_output_paths(state: Path, identities: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
    """Worker-only clipboard paths; aliases never replace recorded references."""

    def read():
        with original_images_lock(state, timeout=30):
            return storage.selection_paths(
                state,
                identities,
                read_record=lambda record: resolve_output_read(state, record, verify=True)[0],
            )

    return read()


async def filter_choices(state: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return await asyncio.to_thread(storage.facets, state)


def load_detail(item: GalleryItem, *, state: Path | None = None) -> GalleryDetail:
    """Read a bounded sidecar, checking its recorded identity; fall back to explicit durable evidence."""
    try:
        access = (
            resolve_item_read(item, state)[1]
            if state is not None
            and item.access_path
            and item.access_path != item.path
            and item.path.startswith("/mnt/")
            else ArtifactReadPaths.discover().resolve(item.metadata_path)
            if foreign_windows_path(item.metadata_path)
            else item.read_metadata_path
        )
        with Path(access).open("rb") as stream:
            encoded = stream.read(MAX_METADATA_BYTES + 1)
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError
        metadata = json.loads(encoded)
        if (
            not isinstance(metadata, dict)
            or type(metadata.get("schema_version")) is not int
            or metadata.get("schema_version") not in (1, 2)
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
        return load_detail(item_from_record(record, state=state), state=state)

    return await asyncio.to_thread(read)


def resolve_item_read(item: GalleryItem, state: Path) -> tuple[str, str]:
    """Recheck a selected gallery row under the caller's original-file lease."""
    attempt, separator, number = item.identity.rpartition(":")
    if not separator or attempt != item.attempt_id or not number.isascii() or not number.isdecimal():
        raise OSError("Recorded output identity is inconsistent.")
    ordinal = int(number)
    if str(ordinal) != number:
        raise OSError("Recorded output identity is inconsistent.")
    image, metadata, _ = resolve_output_read(
        state,
        {
            "identity": item.identity,
            "attempt_id": item.attempt_id,
            "ordinal": ordinal,
            "path": item.path,
            "metadata_path": item.metadata_path,
        },
        verify=True,
    )
    return image, metadata

"""Shared checks for saved managed originals, never mutation authority.

Deletion additionally needs committed ownership, a fingerprint, shared locks
and durable intent. This module performs no filesystem mutation.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

_MAX_METADATA_BYTES = 1024 * 1024
# Display-only memo of digests already proven in this process, keyed by file
# fingerprint. Removal and export never consult it; they always re-hash.
_DISPLAY_DIGESTS: dict[tuple, bool] = {}
_MAX_DISPLAY_DIGESTS = 4096


def ensure_unredirected_path(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(component)):
            raise OSError("Managed output path is redirected.")
        attributes = getattr(component.lstat(), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise OSError("Managed output contains a Windows reparse point.")


def matches_managed_state(state: Path, recorded: str, tail: tuple[str, ...]) -> bool:
    """Check complete recorded layout; /mnt/drive alone is never sufficient."""
    if os.name != "nt" or not isinstance(recorded, str) or not recorded.startswith("/mnt/"):
        return False
    if len(recorded) > 32768 or "\0" in recorded or "\\" in recorded:
        return False
    parts = recorded.split("/")
    if any(not part or part in (".", "..") or ":" in part for part in parts[1:]):
        return False
    native = state.absolute()
    if not re.fullmatch(r"[A-Za-z]:\\", native.anchor):
        return False
    expected = ("", "mnt", native.drive[0].lower(), *native.parts[1:], *tail)
    return len(parts) == len(expected) and all(
        left.casefold() == right.casefold() for left, right in zip(parts, expected, strict=True)
    )


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def declared_raw_artifact(sidecar: Path, row: dict) -> tuple[str, str] | None:
    """Return the recorded path and SHA-256 the attempt sidecar declares for its single raw provider output.

    None means the attempt declares no raw output at all. A declaration that is malformed, names another file
    or belongs to another attempt raises OSError, so a file is never owned by its name alone.
    """
    suffix = "final-1.png"
    try:
        recorded_final = row["path"]
        if not isinstance(recorded_final, str) or not recorded_final.endswith(suffix):
            raise ValueError
        recorded_raw = recorded_final[: -len(suffix)] + "raw-1.png"
        with sidecar.open("rb") as stream:
            data = stream.read(_MAX_METADATA_BYTES + 1)
        if len(data) > _MAX_METADATA_BYTES:
            raise ValueError
        document = json.loads(data)
        if (
            not isinstance(document, dict)
            or type(document.get("schema_version")) is not int
            or document["schema_version"] not in (1, 2)
            or document.get("attempt_id") != row["attempt_id"]
        ):
            raise ValueError
        operation = document.get("image_operation")
        records = operation.get("raw_artifacts") if isinstance(operation, dict) else None
        if records is None or records == []:
            return None
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise ValueError
        declared, digest = records[0].get("path"), records[0].get("sha256")
        if (
            declared != recorded_raw
            or records[0].get("role") != "raw_provider_evidence"
            or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise OSError("Raw provider output is not provably this attempt's.") from exc
    return recorded_raw, digest


def verify_managed_sidecar(
    image: Path,
    sidecar: Path,
    row: dict,
    *,
    check_digest: bool = True,
    check_size: bool = True,
    display: bool = False,
) -> None:
    """Prove recorded sidecar ownership; check output SHA when it is available."""
    try:
        with sidecar.open("rb") as stream:
            data = stream.read(_MAX_METADATA_BYTES + 1)
        if len(data) > _MAX_METADATA_BYTES:
            raise ValueError
        document = json.loads(data)
        if (
            not isinstance(document, dict)
            or type(document.get("schema_version")) is not int
            or document["schema_version"] not in (1, 2)
            or document.get("attempt_id") != row["attempt_id"]
        ):
            raise ValueError
        artifacts = document.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError
        matches = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict)
            and artifact.get("path") == row["path"]
            and artifact.get("designation") == "final"
        ]
        if len(matches) != 1:
            raise ValueError
        size = matches[0].get("size_bytes")
        if check_size and size is not None and (type(size) is not int or size != image.stat().st_size):
            raise ValueError
        operation = document.get("image_operation")
        digest = operation.get("final_sha256") if isinstance(operation, dict) else None
        if digest is not None and check_digest:
            if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise ValueError
            info = image.stat()
            key = (str(image), info.st_size, info.st_mtime_ns, info.st_ino, digest)
            if not (display and _DISPLAY_DIGESTS.get(key)):
                if file_sha256(image) != digest:
                    raise ValueError
                if len(_DISPLAY_DIGESTS) >= _MAX_DISPLAY_DIGESTS:
                    _DISPLAY_DIGESTS.clear()
                _DISPLAY_DIGESTS[key] = True
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise OSError("Managed output metadata or image content does not match its record.") from exc

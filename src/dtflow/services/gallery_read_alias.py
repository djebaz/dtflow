"""Read-only aliases for WSL1-managed outputs recorded under a shared Windows state.

Never persist access aliases or use this function to authorize image removal.
Native paths and existing verified Windows-to-WSL mapping retain precedence.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from dtflow.services.artifact_paths import ArtifactReadPaths

_MAX_METADATA_BYTES = 1024 * 1024
_ATTEMPT = re.compile(r"[0-9a-f]{32}\Z")


def _plain(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(component)):
            raise OSError("Managed output path is redirected.")
        attributes = getattr(component.lstat(), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise OSError("Managed output contains a Windows reparse point.")


def _matches_state(state: Path, recorded: str, tail: tuple[str, ...]) -> bool:
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


def _managed_candidate(state: Path, row: dict) -> tuple[Path, Path]:
    attempt, ordinal, identity = row["attempt_id"], row["ordinal"], row["identity"]
    if (
        not isinstance(attempt, str)
        or not _ATTEMPT.fullmatch(attempt)
        or type(ordinal) is not int
        or ordinal < 0
        or identity != f"{attempt}:{ordinal}"
    ):
        raise OSError("Recorded output identity is inconsistent.")
    tail = ("outputs", attempt)
    if not _matches_state(state, row["path"], (*tail, f"final-{ordinal + 1}.png")) or not _matches_state(
        state, row["metadata_path"], (*tail, "metadata.json")
    ):
        raise OSError("Recorded output is not under this shared Windows state.")
    root = state.absolute()
    image = root.joinpath(*tail, f"final-{ordinal + 1}.png")
    sidecar = root.joinpath(*tail, "metadata.json")
    try:
        for component in (root, root / "outputs", image.parent, image, sidecar):
            _plain(component)
        if root.resolve(strict=True) != root:
            raise OSError
        if image.resolve(strict=True) != image or sidecar.resolve(strict=True) != sidecar:
            raise OSError
        if not image.is_relative_to(root / "outputs"):
            raise OSError
        if any(not stat.S_ISREG(p.stat().st_mode) for p in (image, sidecar)):
            raise OSError
    except (OSError, ValueError) as exc:
        raise OSError("Managed output or sidecar is missing or redirected.") from exc
    return image, sidecar


def _verify_sidecar(image: Path, sidecar: Path, row: dict) -> None:
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
        if size is not None and (type(size) is not int or size != image.stat().st_size):
            raise ValueError
        operation = document.get("image_operation")
        digest = operation.get("final_sha256") if isinstance(operation, dict) else None
        if digest is not None:
            if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise ValueError
            sha = hashlib.sha256()
            with image.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(block)
            if sha.hexdigest() != digest:
                raise ValueError
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise OSError("Managed output metadata or image content does not match its record.") from exc


def resolve_output_read(
    state: Path, row: dict, *, verify: bool = False, paths: ArtifactReadPaths | None = None
) -> tuple[str, str, bool]:
    """Return image, sidecar and whether a state-derived read-only alias was used."""
    paths = paths or ArtifactReadPaths.discover()
    try:
        native = paths.resolve(row["path"])
        if Path(native).is_file():
            try:
                metadata = paths.resolve(row["metadata_path"])
            except OSError:
                metadata = row["metadata_path"]
            return native, metadata, False
    except OSError:
        pass
    if os.name != "nt" or not isinstance(row.get("path"), str) or not row["path"].startswith("/"):
        raise OSError("Recorded output image is missing or its path is unsupported.")
    image, sidecar = _managed_candidate(Path(state), row)
    if verify:
        _verify_sidecar(image, sidecar, row)
        _managed_candidate(Path(state), row)
    return str(image), str(sidecar), True

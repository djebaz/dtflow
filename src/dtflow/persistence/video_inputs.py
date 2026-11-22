"""Content-addressed durable video sources; never cache or dispatch consent."""

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from dtflow.core.models import ValidationError
from dtflow.persistence.image_inputs import _check, _read, _write_new

MAX_SOURCE_BYTES = 32 * 1024 * 1024


def source_bytes(path: Path, digest: str) -> bytes:
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValidationError("The video start image needs its exact SHA-256.")
    data = _read(Path(path), MAX_SOURCE_BYTES)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValidationError("The selected or retained video start image does not match its recorded SHA-256.")
    return data


def retain_source(state: Path, data: bytes, digest: str) -> str:
    """Atomically retain verified bytes; explicit capture may repair a damaged copy of the same identity."""
    if (
        not isinstance(data, bytes)
        or not 0 < len(data) <= MAX_SOURCE_BYTES
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or hashlib.sha256(data).hexdigest() != digest
    ):
        raise ValidationError("Video source bytes do not match their bounded content identity.")
    root = Path(os.path.abspath(state))
    directories = (*reversed(root.parents), root, root / "inputs", root / "inputs" / "video")
    for directory in directories:
        _check(directory, directory=True)
    target = root / "inputs" / "video" / f"{digest}.source"
    target.parent.mkdir(parents=True, exist_ok=True)
    _check(target, directory=False)  # A redirected target is never a repair candidate.
    try:
        existing = source_bytes(target, digest)
    except ValidationError:
        existing = None  # Missing/truncated/bad bytes may be replaced only with the verified bytes above.
    if existing == data:
        return str(target)
    if existing is not None:
        raise ValidationError("Retained video source identity mismatch.")
    pending = target.with_name(f".{digest}-{uuid4().hex}.pending")
    try:
        _write_new(pending, data)
        for directory in directories:
            _check(directory, directory=True)
        _check(target, directory=False)
        os.replace(pending, target)
        if os.name != "nt":
            descriptor = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as exc:
        raise ValidationError(
            "The retained video start-image copy could not be committed; the original is unchanged."
        ) from exc
    finally:
        pending.unlink(missing_ok=True)
    return str(target)

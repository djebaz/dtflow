"""Content-addressed durable video sources; never cache or dispatch consent."""

import hashlib
import os
import re
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.persistence.image_inputs import _check, _read, _write_new

MAX_SOURCE_BYTES = 32 * 1024 * 1024


def source_bytes(path: Path, digest: str) -> bytes:
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValidationError("The video start image needs its exact SHA-256.")
    data = _read(Path(path), MAX_SOURCE_BYTES)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValidationError("The video start image has changed; choose it again.")
    return data


def retain_source(state: Path, data: bytes, digest: str) -> str:
    """Write once and verify existing identical content; never overwrite a corrupt or redirected entry."""
    root = Path(os.path.abspath(state))
    for directory in (*reversed(root.parents), root, root / "inputs", root / "inputs" / "video"):
        _check(directory, directory=True)
    target = root / "inputs" / "video" / f"{digest}.source"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_new(target, data)
    except FileExistsError:
        if source_bytes(target, digest) != data:
            raise ValidationError("Retained video source identity mismatch.") from None
    return str(target)

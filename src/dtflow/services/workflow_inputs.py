"""The DTFlow inputs folder of workflow file nodes (#316), like ComfyUI's ``input/``.

Choosing a file for a Load image or Load image (as mask) node copies it to ``<state>/workflows/inputs/`` under a
collision-free name; the workflow stores only that name, never a path. The name is ``<readable stem>-<sha256 prefix>``
plus the extension, so the same content always maps to the same name (a repeated pick reuses the file) and different
content never overwrites an existing file. A workflow opened on another computer shows the name; the file must be
chosen again there.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from dtflow.core.image_inputs import IMAGE_BYTE_LIMIT
from dtflow.core.models import ValidationError

INPUT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
HASH_LENGTH = 16  # hex characters of the content hash kept in the name (64 bits)
STEM_LENGTH = 48


def inputs_dir(state: Path) -> Path:
    return Path(state) / "workflows" / "inputs"


def input_path(state: Path, name: str) -> Path:
    """The stored file of a node's file name; a name that is not a plain file name never leaves the folder."""
    if not name or name != Path(name).name or name.startswith(".") or "\\" in name or "\0" in name:
        raise ValidationError(f"{name!r} is not a plain file name from the DTFlow inputs folder.")
    return inputs_dir(state) / name


def _stem(source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._-")
    return stem[:STEM_LENGTH] or "image"


def import_input_file(state: Path, source: str | Path) -> str:
    """Copy ``source`` into the inputs folder and return the stored file name (worker thread).

    The file is read once: that buffer is decode-checked (PNG, JPEG or WebP, a single image), hashed and written, so the
    name always matches the stored bytes even if the source changes meanwhile. A stored file of the same name is reused
    only when its bytes hash the same; on a prefix collision with different bytes the full hash names the file. The
    copy is written next to its final place and renamed, so a half-written file is never visible."""
    from dtflow.services.image_preparation import check_image_bytes

    source = Path(source)
    suffix = source.suffix.lower()
    if suffix not in INPUT_EXTENSIONS:
        raise ValidationError("Choose a PNG, JPEG or WebP image file.")
    try:
        with source.open("rb") as stream:
            data = stream.read(IMAGE_BYTE_LIMIT + 1)
    except OSError as exc:
        raise ValidationError(f"The file could not be read ({exc.strerror or 'I/O error'}).") from exc
    check_image_bytes(data)  # empty, over 64 MiB, undecodable or animated files are refused here
    digest = hashlib.sha256(data).hexdigest()
    folder = inputs_dir(state)
    try:
        for length in (HASH_LENGTH, len(digest)):
            name = f"{_stem(source)}-{digest[:length]}{suffix}"
            target = folder / name
            if target.is_file():
                if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                    return name  # the same content is already stored
                continue  # a different file holds this name: use the longer one
            folder.mkdir(parents=True, exist_ok=True)
            temporary = folder / f".{uuid4().hex}.tmp"
            try:
                temporary.write_bytes(data)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return name
    except OSError as exc:
        raise ValidationError(
            f"The file could not be copied into the DTFlow inputs folder ({exc.strerror or 'I/O error'})."
        ) from exc
    raise ValidationError("The file could not be stored under a free name.")

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
import shutil
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
    """Copy ``source`` into the inputs folder and return the stored file name (worker thread: reads and hashes the file).

    The file must be a PNG, JPEG or WebP that decodes as a single image. The copy is written next to its final place
    and renamed, so a half-written file is never visible."""
    from dtflow.services.image_preparation import check_image_file

    source = Path(source)
    if source.suffix.lower() not in INPUT_EXTENSIONS:
        raise ValidationError("Choose a PNG, JPEG or WebP image file.")
    check_image_file(source)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        name = f"{_stem(source)}-{digest.hexdigest()[:HASH_LENGTH]}{source.suffix.lower()}"
        folder = inputs_dir(state)
        target = folder / name
        if (
            target.is_file()
            and target.stat().st_size == source.stat().st_size
            and target.stat().st_size <= IMAGE_BYTE_LIMIT
        ):
            return name  # the same content is already stored
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / f".{uuid4().hex}.tmp"
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise ValidationError(
            f"The file could not be copied into the DTFlow inputs folder ({exc.strerror or 'I/O error'})."
        ) from exc
    return name

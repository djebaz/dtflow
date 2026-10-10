"""Exclusive, bounded durable source/mask snapshots; not disposable cache.

Call on a worker. A manifest is the commit marker, written last. Partial directories
are retained as failure evidence and are never reused, repaired or replayed here.
These functions do not register outputs, create attempts or authorize generation.
"""

import hashlib
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

from dtflow.core.image_inputs import (
    IMAGE_BYTE_LIMIT,
    INPUT_NAMES,
    ImageContent,
    ImageInputSnapshot,
    PreparedImageInputs,
)
from dtflow.core.models import ValidationError

MANIFEST_BYTE_LIMIT = 16 * 1024


def _check(path: Path, *, directory: bool) -> None:
    """Reject redirected paths, junctions and unexpected existing object types."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValidationError("Durable input paths and all state-directory ancestors must not be symlinks or junctions; choose a non-redirected state location.")
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValidationError("Durable input directory is not a directory.")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise ValidationError("Durable input content is not a regular file.")


def _directory(state: Path, snapshot: ImageInputSnapshot) -> Path:
    # Do not resolve away symlinks/reparse points before inspecting ancestors.
    root = Path(os.path.abspath(state))
    for ancestor in (*reversed(root.parents), root):
        _check(ancestor, directory=True)
    inputs = root / "inputs"
    target = inputs / snapshot.attempt_id
    _check(inputs, directory=True)
    _check(target, directory=True)
    return target


def _write_new(path: Path, encoded: bytes) -> None:
    _check(path.parent, directory=True)
    _check(path, directory=False)
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _read(path: Path, limit: int) -> bytes:
    _check(path.parent, directory=True)
    _check(path, directory=False)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValidationError("Durable input content is not a regular file.")
            encoded = stream.read(limit + 1)
        _check(path, directory=False)
    except OSError as exc:
        raise ValidationError("Durable input content could not be read.") from exc
    if not encoded or len(encoded) > limit:
        raise ValidationError("Durable input content is empty or exceeds its byte limit.")
    return encoded


def save_input_snapshot(
    state: Path, snapshot: ImageInputSnapshot, prepared: PreparedImageInputs
) -> ImageInputSnapshot:
    """Commit already-validated pixels once; refuse mismatched identities/overwrites.

    The orchestration owner must hold its durable-state ownership boundary and call
    the headless pixel validator before this lower-level persistence function.
    No filename or external source path is taken from the manifest.
    """
    expected = ImageInputSnapshot.from_prepared(
        snapshot.attempt_id, prepared, preserve_outside=snapshot.preserve_outside
    )
    if expected != snapshot:
        raise ValidationError("Durable snapshot does not match prepared input evidence.")
    # Check all identities before creating any directory or content file.
    for name in INPUT_NAMES:
        content = getattr(prepared, name)
        if content is not None and hashlib.sha256(content.encoded).hexdigest() != content.sha256:
            raise ValidationError("Prepared input content digest mismatch.")
    manifest = json.dumps(asdict(snapshot), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(manifest) > MANIFEST_BYTE_LIMIT:
        raise ValidationError("Durable input manifest exceeds its byte limit.")
    directory = _directory(state, snapshot)
    directory.parent.mkdir(parents=True, exist_ok=True)
    _directory(state, snapshot)
    directory.mkdir()  # Exclusive: incomplete and committed sets alike cannot be reused.
    for name in INPUT_NAMES:
        content = getattr(prepared, name)
        if content is not None:
            _write_new(directory / f"{name}.png", content.encoded)
    pending = directory / "manifest.pending"
    _write_new(pending, manifest)
    _directory(state, snapshot)
    pending.rename(directory / "manifest.json")
    # Files and marker are fsynced. Directory fsync is additionally available on
    # POSIX; Windows crash-durability still needs target-machine acceptance.
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return snapshot


def load_input_snapshot(state: Path, snapshot: ImageInputSnapshot) -> PreparedImageInputs:
    """Verify committed manifest and every content hash/size; never trust mtime.

    Caller supplies the expected immutable snapshot from durable request evidence,
    not merely an attempt ID. Pixel-mode/mask checks belong to the headless service.
    """
    directory = _directory(state, snapshot)
    try:
        record = json.loads(_read(directory / "manifest.json", MANIFEST_BYTE_LIMIT))
        committed = ImageInputSnapshot.from_record(record)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ValidationError("Durable input manifest is malformed.") from exc
    if committed != snapshot:
        raise ValidationError("Durable input manifest does not match expected request evidence.")
    contents = {}
    for name in INPUT_NAMES:
        reference = getattr(snapshot, name)
        if reference is None:
            contents[name] = None
            continue
        encoded = _read(directory / f"{name}.png", min(reference.byte_count, IMAGE_BYTE_LIMIT))
        if len(encoded) != reference.byte_count or hashlib.sha256(encoded).hexdigest() != reference.sha256:
            raise ValidationError("Durable input content identity mismatch.")
        contents[name] = ImageContent(
            encoded, reference.sha256, reference.width, reference.height, reference.channels
        )
    return PreparedImageInputs(
        **contents, fit=snapshot.fit, grow=snapshot.grow, feather=snapshot.feather
    )

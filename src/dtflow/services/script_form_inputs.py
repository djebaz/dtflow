"""Explicit picker-to-owned-input boundary. All filesystem/pixel work runs on workers.

External picker paths stop here: only capabilities bound to the run/form/field are
persisted as answers. The host receives embedded images or a confined copied folder.
"""

from __future__ import annotations

import base64
import os
import stat
import uuid
from dataclasses import asdict
from pathlib import Path

from dtflow.core.image_inputs import IMAGE_BYTE_LIMIT, ImageInputSnapshot
from dtflow.core.models import ValidationError
from dtflow.scripting.forms import (
    MAX_ANSWER_BYTES,
    MAX_IMAGES,
    bounded_json,
    contributes,
    image_bytes,
    image_sources,
    input_token,
    validate_answers,
    validate_components,
)
from dtflow.services.image_inputs import restore_image_inputs, snapshot_image_inputs
from dtflow.services.image_preparation import prepare_image_bytes, prepare_script_image_bytes, prepare_script_image_file
from dtflow.services.script_runs import prompt_input, read_open_prompt, recalled_answers, register_prompt_input

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def read_script_form(state: Path, run_id: str, ordinal: int) -> dict | None:
    """Read suggestions and decode bounded raster thumbnails off the GUI thread."""
    prompt = read_open_prompt(state, run_id)
    if prompt is None or prompt.ordinal != ordinal:
        return None
    validate_components(prompt.components)
    thumbnails = {}

    def visit(fields):
        for spec in fields:
            if spec["type"] == "section":
                visit(spec.get("views") or [])
            elif spec["type"] == "image":
                for source in image_sources(spec):
                    if source not in thumbnails:
                        prepared = prepare_image_bytes(image_bytes(source), 256, 256)
                        thumbnails[source] = prepared.image.encoded

    visit(prompt.components)
    return {
        "prompt": prompt,
        "answers": recalled_answers(state, run_id, prompt.components),
        "thumbnails": thumbnails,
    }


def _field(components: list, path: tuple) -> dict:
    if not path or len(path) > 13 or any(type(x) is not int or x < 0 for x in path):
        raise ValidationError("Invalid script form field identity.")
    fields = components
    for depth, index in enumerate(path):
        fields = [s for s in fields if contributes(s)]
        if index >= len(fields):
            raise ValidationError("This script form field no longer exists.")
        spec = fields[index]
        if depth != len(path) - 1:
            if spec["type"] != "section":
                raise ValidationError("Invalid nested script form field.")
            fields = spec.get("views") or []
    return spec


def _plain(path: Path, directory=False) -> None:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise ValidationError("Selected input must not be redirected or an unexpected file type.")


def _owned_directory(state: Path, run_id: str, name: str, *, create=False) -> Path:
    if any(len(x) != 32 or any(c not in "0123456789abcdef" for c in x) for x in (run_id, name)):
        raise ValidationError("Invalid script input directory identity.")
    root = Path(os.path.abspath(state))
    target = root / "script_files" / run_id / "imports" / name
    for part in (*reversed(target.parents), target):
        if part.exists() or part.is_symlink():
            _plain(part, directory=True)
        elif create:
            part.mkdir()
        else:
            raise ValidationError("Owned script input folder is missing.")
    return target


def snapshot_picked_inputs(state: Path, run_id: str, ordinal: int, field: tuple, paths: list[str]) -> list[str]:
    """Snapshot explicit native selections; never recursively import a user directory."""
    prompt = read_open_prompt(state, run_id)
    if prompt is None or prompt.ordinal != ordinal:
        raise ValidationError("This script form has closed.")
    spec = _field(prompt.components, field)
    kind = spec["type"]
    if kind not in ("imageField", "directory") or not isinstance(paths, list):
        raise ValidationError("This field does not accept native picker input.")
    maximum = 1 if kind == "directory" or not spec.get("multiSelect") else MAX_IMAGES
    if not 1 <= len(paths) <= maximum or any(not isinstance(p, str) for p in paths):
        raise ValidationError("Too many selected files for this field.")
    sources = [Path(p) for p in paths]
    if kind == "directory":
        _plain(sources[0], directory=True)
        # Bounded, nonrecursive snapshot. Never hand the external folder to JS.
        selected = []
        with os.scandir(sources[0]) as entries:
            for count, entry in enumerate(entries, 1):
                if count > 256:
                    raise ValidationError("Folder has over 256 entries; choose a smaller input folder.")
                if Path(entry.name).suffix.lower() in IMAGE_SUFFIXES:
                    selected.append(Path(entry.path))
                    if len(selected) > MAX_IMAGES:
                        raise ValidationError(f"Folder has over {MAX_IMAGES} images; choose a smaller input folder.")
        sources = sorted(selected)
    records = []
    crops = []
    total = 0
    for source in sources:
        current = read_open_prompt(state, run_id)
        if current is None or current.ordinal != ordinal:
            raise ValidationError("This script form closed while selected inputs were preparing.")
        _plain(source)
        if source.stat().st_size > IMAGE_BYTE_LIMIT:
            raise ValidationError("Selected image exceeds the input byte limit.")
        prepared, crop = prepare_script_image_file(source, multiple=spec.get("multiple", 1))
        total += len(prepared.source.encoded)
        if total > 32 * 1024 * 1024:
            raise ValidationError("Selected input set exceeds 32 MiB of normalized image data.")
        snapshot = snapshot_image_inputs(state, uuid.uuid4().hex, prepared, preserve_outside=False)
        records.append((snapshot, prepared.source.encoded))
        crops.append(crop)
    if kind == "directory":
        name = uuid.uuid4().hex
        target = _owned_directory(state, run_id, name, create=True)
        for index, (_, encoded) in enumerate(records, 1):
            with (target / f"image-{index:03}.png").open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        record = {"directory": name, "snapshots": [asdict(s) for s, _ in records], "crops": crops}
        return [register_prompt_input(state, run_id, ordinal, field, kind, record)]
    # Refuse an answer that would exceed the existing bridge before enabling Continue.
    bounded_json(["data:image/png;base64," + base64.b64encode(b).decode("ascii") for _, b in records], MAX_ANSWER_BYTES)
    return [
        register_prompt_input(state, run_id, ordinal, field, kind, {"snapshot": asdict(s), "crop": crop})
        for (s, _), crop in zip(records, crops, strict=True)
    ]


def crop_inline_form_images(components: list, answers: list) -> list:
    """Apply explicit grid policy to embedded answers, including headless answer files."""
    result = []
    for spec, value in zip((s for s in components if contributes(s)), answers, strict=True):
        if spec["type"] == "section":
            value = crop_inline_form_images(spec.get("views") or [], value)
        elif spec["type"] == "imageField" and spec.get("multiple", 1) > 1:
            cropped = []
            for source in value:
                if input_token(source):
                    cropped.append(source)
                else:
                    prepared, _ = prepare_script_image_bytes(image_bytes(source), multiple=spec["multiple"])
                    cropped.append("data:image/png;base64," + base64.b64encode(prepared.source.encoded).decode("ascii"))
            value = cropped
        result.append(value)
    bounded_json(result, MAX_ANSWER_BYTES)
    return result


def _check_image_multiple(prepared, spec):
    multiple = spec.get("multiple", 1)
    if prepared.source.width % multiple or prepared.source.height % multiple:
        raise ValidationError("Owned script image no longer matches this field's pixel multiple; select it again.")


def resolve_form_inputs(state: Path, run_id: str, ordinal: int, components: list, answers: list) -> list:
    """Revalidate owned capability provenance and content before any host reply."""

    def origin(path, kind, token):
        prompt_input(state, run_id, ordinal, path, kind, token)

    validate_answers(components, answers, origin=origin)
    answers = crop_inline_form_images(components, answers)

    def visit(fields, values, path=()):
        result = []
        for index, (spec, value) in enumerate(zip((s for s in fields if contributes(s)), values, strict=True)):
            here = (*path, index)
            kind = spec["type"]
            if kind == "section":
                value = visit(spec.get("views") or [], value, here)
            elif kind == "imageField":
                resolved = []
                for token in value:
                    if not input_token(token):
                        resolved.append(token)
                        continue
                    record = prompt_input(state, run_id, ordinal, here, kind, token)
                    prepared = restore_image_inputs(state, ImageInputSnapshot.from_record(record["snapshot"]))
                    _check_image_multiple(prepared, spec)
                    resolved.append(
                        "data:image/png;base64," + base64.b64encode(prepared.source.encoded).decode("ascii")
                    )
                value = resolved
            elif kind == "directory" and value:
                record = prompt_input(state, run_id, ordinal, here, kind, value)
                target = _owned_directory(state, run_id, record["directory"])
                for index, snapshot in enumerate(record["snapshots"], 1):
                    prepared = restore_image_inputs(state, ImageInputSnapshot.from_record(snapshot))
                    _check_image_multiple(prepared, spec)
                    copied = target / f"image-{index:03}.png"
                    _plain(copied)
                    with copied.open("rb") as stream:
                        if stream.read(IMAGE_BYTE_LIMIT + 1) != prepared.source.encoded:
                            raise ValidationError("Owned input-folder content no longer matches its snapshot.")
                value = str(target)
            result.append(value)
        return result

    result = visit(components, answers)
    bounded_json(result, MAX_ANSWER_BYTES)
    return result

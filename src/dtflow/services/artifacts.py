"""Durable artifact writes and bounded final-output validation."""

import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from dtflow.core.models import Designation, GenerationResult, ResolvedGenerationRequest, ResourceLimits
from dtflow.core.png import validate_png


def validate_result(result: GenerationResult, request: ResolvedGenerationRequest, limits: ResourceLimits) -> None:
    if result.request_id != request.request_id:
        raise ValueError("Result request identity does not match.")
    if result.completion.terminal_status != "OK" or not result.completion.required_outputs_valid:
        raise ValueError("Successful completion evidence is missing.")
    if not 1 <= len(result.artifacts) <= limits.final_count:
        raise ValueError("Final outputs are empty or exceed the count limit.")
    if len(result.artifacts) != request.batch_size * request.batch_count:
        raise ValueError("Required final outputs are incomplete.")
    if sum(len(x.encoded) for x in result.artifacts) > limits.final_bytes:
        raise ValueError("Aggregate final output exceeds the limit.")
    for artifact in result.artifacts:
        if artifact.designation != Designation.FINAL or artifact.media_type != "image/png":
            raise ValueError("Preview/partial or unsupported media cannot establish success.")
        if (artifact.width_px, artifact.height_px) != (request.width_px, request.height_px):
            raise ValueError("Final output dimensions do not match the resolved request.")
        if len(artifact.encoded) > limits.artifact_bytes:
            raise ValueError("Encoded artifact exceeds the limit.")
        validate_png(artifact.encoded, artifact.width_px, artifact.height_px, decoded_limit=limits.tensor_bytes)


def sync_directory(directory: Path) -> None:
    """Flush directory entries where supported; Windows needs target validation."""
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def save_artifacts(
    output: Path,
    attempt_id: str,
    request: ResolvedGenerationRequest,
    result: GenerationResult,
    build_identity: dict[str, str],
    limits: ResourceLimits,
) -> tuple[tuple[str, ...], str]:
    if request.operation != "txt2img":
        raise ValueError("Image-operation output saving requires dedicated raw/final evidence handling.")
    validate_result(result, request, limits)
    output.mkdir(parents=True, exist_ok=True)
    directory = output / attempt_id
    directory.mkdir(exist_ok=False)
    sync_directory(output)
    paths = []
    for index, artifact in enumerate(result.artifacts, 1):
        path = directory / f"final-{index}.png"
        atomic_write(path, artifact.encoded)
        paths.append(str(path.resolve()))
    metadata = generation_metadata(attempt_id, request, result, build_identity, [
        artifact_record(path, artifact) for path, artifact in zip(paths, result.artifacts, strict=True)
    ])
    metadata_path = directory / "metadata.json"
    atomic_write(metadata_path, (json.dumps(metadata, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return tuple(paths), str(metadata_path.resolve())


def artifact_record(path, artifact) -> dict:
    """One canonical sidecar representation for registered final or retained raw evidence."""
    return {
        "path": str(path),
        "artifact_id": artifact.artifact_id,
        "designation": artifact.designation,
        "media_type": artifact.media_type,
        "width_px": artifact.width_px,
        "height_px": artifact.height_px,
        "metadata": dict(artifact.metadata),
    }


def generation_metadata(attempt_id, request, result, build_identity, artifacts: list[dict]) -> dict:
    """Shared generation provenance; output dimensions/roles belong to explicit artifact records."""
    metadata = {
        "schema_version": 2,
        "attempt_id": attempt_id,
        "request": asdict(request),
        "backend_id": result.backend_id,
        "server_identity": result.server_identity,
        "effective_configuration": dict(result.effective_configuration),
        "completion": asdict(result.completion),
        "warnings": result.warnings,
        "build": build_identity,
        "artifacts": artifacts,
    }
    if result.backend_elapsed_seconds is not None:
        metadata["timing"] = {"backend_elapsed_seconds": result.backend_elapsed_seconds}
    return metadata

"""Worker-only image-operation saving: raw evidence plus one registered final image.

Pillow stays in image_preparation. Export/Trash/cache never discover files by
scanning these directories; only final paths enter the execution artifact ledger.
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from dtflow.core.image_inputs import ImageInputSnapshot, PreparedImageInputs
from dtflow.core.models import GenerationResult, ResolvedGenerationRequest, ResourceLimits
from dtflow.core.png import validate_png
from dtflow.services.artifacts import (
    artifact_record, atomic_write, generation_metadata, sync_directory, validate_result,
)
from dtflow.services.image_preparation import ALGORITHM, composite_image


def save_image_artifacts(
    output: Path,
    attempt_id: str,
    request: ResolvedGenerationRequest,
    result: GenerationResult,
    build_identity: dict[str, str],
    limits: ResourceLimits,
    prepared: PreparedImageInputs,
) -> tuple[tuple[str, ...], str, bytes]:
    """Keep untouched raw PNG; optionally composite, then atomically save metadata.

    Caller owns the original-file lease and only registers returned final/metadata
    paths after all writes succeed. Failures retain available raw/partial evidence
    but do not publish a gallery output or change remote success into a retry.
    """
    if request.operation not in ("img2img", "inpaint") or request.image_inputs is None:
        raise ValueError("Image-operation saving requires managed input evidence.")
    expected = ImageInputSnapshot.from_prepared(
        attempt_id, prepared, preserve_outside=request.image_inputs.preserve_outside
    )
    if expected != request.image_inputs:
        raise ValueError("Image-operation output inputs do not match the owning attempt.")
    validate_result(result, request, limits)
    if len(result.artifacts) != 1:
        raise ValueError("Image-operation saving requires exactly one raw result.")
    output.mkdir(parents=True, exist_ok=True)
    directory = output / attempt_id
    directory.mkdir(exist_ok=False)
    sync_directory(output)
    raw = result.artifacts[0]
    raw_path = directory / "raw-1.png"
    atomic_write(raw_path, raw.encoded)
    # The application-owned final (including an unchanged raw copy) is distinct
    # from provider evidence. Retain the provider identity only on the raw record.
    final = replace(raw, artifact_id=f"{raw.artifact_id}.dtflow-final")
    if request.image_inputs.preserve_outside:
        composite = composite_image(prepared, raw.encoded)
        if len(composite.encoded) > limits.artifact_bytes or len(raw.encoded) + len(composite.encoded) > limits.final_bytes:
            raise ValueError("Raw/composite output exceeds the encoded byte limit.")
        validate_png(composite.encoded, composite.width, composite.height, decoded_limit=limits.tensor_bytes)
        final = replace(
            final, encoded=composite.encoded, width_px=composite.width, height_px=composite.height,
            metadata=(*raw.metadata, ("dtflow_output_role", "source_sized_composite")),
        )
    final_path = directory / "final-1.png"
    atomic_write(final_path, final.encoded)
    paths = (str(final_path.resolve()),)
    metadata = generation_metadata(
        attempt_id, request, result, build_identity, [artifact_record(paths[0], final)]
    )
    raw_record = artifact_record(str(raw_path.resolve()), raw)
    raw_record.update(role="raw_provider_evidence", sha256=hashlib.sha256(raw.encoded).hexdigest())
    metadata["image_operation"] = {
        "preparation_schema_version": prepared.schema_version,
        "preserve_outside_mask": request.image_inputs.preserve_outside,
        "final_role": "source_sized_composite" if request.image_inputs.preserve_outside else "raw_provider_copy",
        "source_sha256": prepared.source.sha256,
        "source_normalization": {
            "algorithm": ALGORITHM,
            "orientation": "exif_transposed",
            "pixel_mode": "RGB",
            "bits_per_channel": 8,
            "alpha_background": "white",
            "color_management": "no_icc_transform_or_profile_retention",
            "inherited_exif_icc_metadata": "removed",
            "outside_mask_reference": "prepared_source_rgb" if request.image_inputs.preserve_outside else None,
            "original_file_pixel_preservation_guaranteed": False,
        },
        "raw_artifact_id": raw.artifact_id,
        "raw_artifacts": [raw_record],
        "final_sha256": hashlib.sha256(final.encoded).hexdigest(),
    }
    metadata_path = directory / "metadata.json"
    atomic_write(metadata_path, (json.dumps(metadata, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return paths, str(metadata_path.resolve()), final.encoded

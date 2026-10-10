"""Versioned immutable request JSON decoding, without provider/persistence types."""

from dataclasses import asdict
from typing import Any

from dtflow.core.image_inputs import ImageInputSnapshot
from dtflow.core.models import LoRASelection, PreparedAsset, ResolvedGenerationRequest, Sampler, ValidationError


def request_from_record(record: dict[str, Any]) -> ResolvedGenerationRequest:
    """Read v2, or historical v1/missing-version txt2img only; never drop fields."""
    try:
        if not isinstance(record, dict):
            raise ValidationError("Stored request must be an object.")
        values = dict(record)
        version = values.get("schema_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise ValidationError("Unsupported stored-request schema.")
        if version == 1:
            if (
                values.get("operation", "txt2img") != "txt2img"
                or values.get("image_inputs") is not None
                or values.get("strength", 1) != 1
            ):
                raise ValidationError("Historical requests may only represent text-to-image.")
            values.update(schema_version=2, operation="txt2img", strength=1.0, image_inputs=None)
        elif not {"operation", "strength", "image_inputs"} <= values.keys():
            raise ValidationError("Request v2 requires explicit operation, strength and input evidence.")
        if values.get("image_inputs") is not None:
            values["image_inputs"] = ImageInputSnapshot.from_record(values["image_inputs"])
        values["sampler_id"] = Sampler(values["sampler_id"])
        values["loras"] = tuple(LoRASelection(**item) for item in values["loras"])
        values["resolution_evidence"] = tuple(tuple(pair) for pair in values["resolution_evidence"])
        values["prepared_assets"] = tuple(PreparedAsset(**item) for item in values.get("prepared_assets", ()))
        return ResolvedGenerationRequest(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Stored request is malformed or has an unsupported schema.") from exc


def upgraded_request_record(record: dict[str, Any]) -> dict[str, Any]:
    """Canonical v2 JSON fields; caller owns the atomic migration transaction."""
    return asdict(request_from_record(record))

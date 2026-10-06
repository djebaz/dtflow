"""Draw Things wire conversion: the only place provider request messages are built.

The resolved seed is serialized through the public ``GenConfig.to_fbs(seed)`` path and never through
``RequestBuilder``/``build_grpc_message``, which replace a seed of zero with a random one in drawthings-py 0.4.0.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from importlib.metadata import version

from drawthings_py.configs.gen_config import GenConfig
from drawthings_py.generated.dt_grpc import image_service
from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration

from dtflow.backends.drawthings.errors import error
from dtflow.core.models import ErrorCategory, Metadata, ResolvedGenerationRequest, Sampler

# Provider sampler names; drawthings-py silently substitutes a default for unknown names, so the result is verified.
SAMPLER_NAME = {Sampler.EULER_A: "EulerA", Sampler.DPMPP_2M_KARRAS: "DPMPP2MKarras"}
SAMPLER_WIRE_VALUE = {"EulerA": 1, "DPMPP2MKarras": 0}
# Text-to-image runs at full strength in every drawthings-py preset; the library default is 0.0.
TXT2IMG_STRENGTH = 1.0
CLIENT_NAME = "dtflow"
# Every selected LoRA applies to the whole base model; refiner/modifier-only modes are not part of 3A.
LORA_MODE = "All"


@dataclass(frozen=True, slots=True)
class WireRequest:
    message: image_service.ImageGenerationRequest
    effective_configuration: Metadata


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _reject(request: ResolvedGenerationRequest, code: str, message: str):
    return error(ErrorCategory.VALIDATION, code, message, request.request_id)


def validate_supported(request: ResolvedGenerationRequest) -> None:
    """Reject anything outside the verified txt2img scope before any serialization."""
    if request.operation != "txt2img":
        raise _reject(request, "unsupported_operation", "Only text-to-image is supported.")
    if (request.batch_size, request.batch_count) != (1, 1):
        raise _reject(request, "unsupported_batch", "Only a single output per request is supported.")
    if request.sampler_id not in SAMPLER_NAME:
        raise _reject(request, "unsupported_sampler", "Sampler is not supported by this adapter.")


def custom_lora_override(request: ResolvedGenerationRequest, version: str | None) -> bytes:
    """Metadata for user-prepared cloud LoRAs, in the Echo ``override.loras`` JSON shape (``name``, ``file``, ``version``).

    The server cannot resolve a custom file from its name alone and appears to ignore it without an error, so each prepared
    LoRA is described explicitly. ``version`` is the selected base model's own Draw Things version string; with no known
    version nothing is sent rather than a guess.
    """
    prepared = {item.identifier for item in request.prepared_assets}
    if not prepared or not version:
        return b""
    entries = [
        {"name": item.identifier, "file": item.identifier, "version": version}
        for item in request.loras
        if item.identifier in prepared
    ]
    return json.dumps(entries).encode() if entries else b""


def build_generation_message(
    request: ResolvedGenerationRequest, shared_secret: str | None, base_version: str | None = None
) -> WireRequest:
    validate_supported(request)
    sampler = SAMPLER_NAME[request.sampler_id]
    config = GenConfig(
        model=request.model_id,
        width=request.width_px,
        height=request.height_px,
        steps=request.steps,
        guidance=request.guidance,
        sampler=sampler,
        strength=TXT2IMG_STRENGTH,
        batch_size=1,
        batch_count=1,
    )
    for selection in request.loras:
        config.add_lora(selection.identifier, selection.weight, LORA_MODE)
    configuration = config.to_fbs(request.seed)
    effective = verify_wire_configuration(request, configuration)
    message = image_service.ImageGenerationRequest(
        scale_factor=1,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        configuration=configuration,
        user=CLIENT_NAME,
    )
    override = custom_lora_override(request, base_version)
    if override:
        message.override = image_service.MetadataOverride(loras=override)
    if shared_secret:
        message.shared_secret = shared_secret
    return WireRequest(message, effective)


def verify_wire_configuration(request: ResolvedGenerationRequest, configuration: bytes) -> Metadata:
    """Decode what will be sent and assert it equals the resolved request; return the effective configuration."""
    decoded = dict(GenConfig.from_fbs(configuration).items())
    raw = GenerationConfiguration.GetRootAs(configuration, 0)
    expected = {
        "seed": request.seed,
        "width": request.width_px,
        "height": request.height_px,
        "steps": request.steps,
        "model": request.model_id,
        "sampler": SAMPLER_NAME[request.sampler_id],
        "batch_size": 1,
        "batch_count": 1,
    }
    mismatches = [key for key, value in expected.items() if decoded.get(key) != value]
    sent = [(item.get("file"), item.get("weight"), item.get("mode")) for item in decoded.get("loras") or []]
    wanted = [(item.identifier, float32(item.weight), LORA_MODE) for item in request.loras]
    if [(f, w, m) for f, w, m in sent] != wanted:
        mismatches.append("loras")
    if float32(request.guidance) != decoded.get("guidance"):
        mismatches.append("guidance")
    if raw.Sampler() != SAMPLER_WIRE_VALUE[SAMPLER_NAME[request.sampler_id]]:
        mismatches.append("sampler_wire_value")
    if mismatches:
        raise error(
            ErrorCategory.PROTOCOL,
            "wire_configuration_mismatch",
            "Serialized configuration does not match the resolved request.",
            request.request_id,
        )
    pairs = {key: str(value) for key, value in decoded.items() if value is not None}
    pairs["sampler_wire_value"] = str(raw.Sampler())
    pairs["loras"] = ",".join(f"{item.identifier}@{item.weight}" for item in request.loras)
    pairs["config_source"] = "drawthings-py " + version("drawthings-py")
    return tuple(sorted(pairs.items()))

"""Draw Things wire conversion: the only place provider request messages are built.

The resolved seed is serialized through the public ``GenConfig.to_fbs(seed)`` path and never through
``RequestBuilder``/``build_grpc_message``, which replace a seed of zero with a random one in drawthings-py 0.4.0.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from importlib.metadata import version

import flatbuffers
from drawthings_py.configs.gen_config import GenConfig
from drawthings_py.generated.dt_grpc import image_service
from drawthings_py.generated.dt_grpc.config_generated import (
    GenerationConfiguration,
    GenerationConfigurationT,
    SamplerType,
)

from dtflow.backends.drawthings.errors import error
from dtflow.backends.drawthings.image_inputs import encode_image_inputs
from dtflow.backends.drawthings.video_wire import start_image_tensor, with_num_frames
from dtflow.core.image_inputs import ImageInputSnapshot, PreparedImageInputs
from dtflow.core.image_operations import ImageOperationPermit
from dtflow.core.model_profiles import exact_model_description_digest, exact_model_spec, profile_for
from dtflow.core.models import ErrorCategory, Metadata, ResolvedGenerationRequest, ResourceLimits, Sampler
from dtflow.core.video import video_profile

# Provider sampler names; drawthings-py silently substitutes a default for unknown names, so the result is verified.
SAMPLER_NAME = {
    Sampler.EULER_A: "EulerA",
    Sampler.DPMPP_2M_KARRAS: "DPMPP2MKarras",
    Sampler.DDIM: "DDIM",
    Sampler.PLMS: "PLMS",
    Sampler.DPMPP_SDE_KARRAS: "DPMPPSDEKarras",
    Sampler.UNIPC: "UniPC",
    Sampler.LCM: "LCM",
    Sampler.EULER_A_SUBSTEP: "EulerASubstep",
    Sampler.DPMPP_SDE_SUBSTEP: "DPMPPSDESubstep",
    Sampler.TCD: "TCD",
    Sampler.EULER_A_TRAILING: "EulerATrailing",
    Sampler.DPMPP_SDE_TRAILING: "DPMPPSDETrailing",
    Sampler.DPMPP_2M_AYS: "DPMPP2MAYS",
    Sampler.EULER_A_AYS: "EulerAAYS",
    Sampler.DPMPP_SDE_AYS: "DPMPPSDEAYS",
    Sampler.DPMPP_2M_TRAILING: "DPMPP2MTrailing",
    Sampler.DDIM_TRAILING: "DDIMTrailing",
    Sampler.UNIPC_TRAILING: "UniPCTrailing",
    Sampler.UNIPC_AYS: "UniPCAYS",
    Sampler.TCD_TRAILING: "TCDTrailing",
}
# The generated flatbuffer enum is the authority for the numeric value of each provider name.
SAMPLER_WIRE_VALUE = {name: getattr(SamplerType, name) for name in SAMPLER_NAME.values()}
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


def _same_float32(decoded: object, wanted: float) -> bool:
    """drawthings-py rounds some decoded float32 values (0.6 reads back as 0.6, not 0.6000000238...), so compare loosely."""
    return isinstance(decoded, int | float) and abs(decoded - float32(wanted)) <= 1e-6 * max(1.0, abs(wanted))


def wire_lora_name(request: ResolvedGenerationRequest, identifier: str) -> str:
    """DT+ downloads an uploaded LoRA only when the request names it by its SHA-256 (Draw Things' ServerLoRALoader)."""
    for prepared in request.prepared_assets:
        if prepared.identifier == identifier:
            return prepared.sha256
    return identifier


def _reject(request: ResolvedGenerationRequest, code: str, message: str):
    return error(ErrorCategory.VALIDATION, code, message, request.request_id)


def validate_supported(request: ResolvedGenerationRequest, *, image_permitted: bool = False) -> None:
    """Image expressibility requires the separate exact owner gate, never a family guess."""
    if request.operation != "txt2img" and not image_permitted:
        raise _reject(request, "unsupported_operation", "Image operations require an exact owner permit.")
    if (request.batch_size, request.batch_count) != (1, 1):
        raise _reject(request, "unsupported_batch", "Only a single output per request is supported.")
    if request.sampler_id not in SAMPLER_NAME:
        raise _reject(request, "unsupported_sampler", "Sampler is not supported by this adapter.")


def custom_lora_override(request: ResolvedGenerationRequest) -> bytes:
    """Describe prepared cloud LoRAs with their own exact Draw Things versions.

    Echo silently drops an override entry without a ``version``. Reject unknown
    prepared files rather than inferring the selected checkpoint's architecture.
    """
    prepared = {item.identifier: item for item in request.prepared_assets}
    entries = []
    for item in request.loras:
        asset = prepared.get(item.identifier)
        if asset is None:
            continue
        if not asset.family:
            raise _reject(
                request,
                "prepared_lora_architecture_unknown",
                f"Prepared LoRA {item.identifier} has no detected architecture; it cannot be sent to DT+.",
            )
        entries.append(
            {
                "name": item.identifier,
                "file": asset.sha256,
                "prefix": "",
                "version": asset.family,
            }
        )
    return json.dumps(entries).encode() if entries else b""


def with_guidance_embed(configuration: bytes, value: float) -> bytes:
    """drawthings-py 0.4.0 skips ``guidance_embed`` while ``speed_up_with_guidance_embed`` is on and writes the 3.5
    default, which is exactly when Flux uses it; set the preset value on the generated table instead."""
    table = GenerationConfigurationT.InitFromObj(GenerationConfiguration.GetRootAs(configuration, 0))
    table.guidanceEmbed = value
    builder = flatbuffers.Builder(0)
    builder.Finish(table.Pack(builder))
    return bytes(builder.Output())


def build_generation_message(
    request: ResolvedGenerationRequest,
    shared_secret: str | None,
    base_version: str | None = None,
    model_spec: dict | None = None,
    *,
    prepared: PreparedImageInputs | None = None,
    image_permit: ImageOperationPermit | None = None,
    backend_id: str | None = None,
    endpoint: str | None = None,
    allow_image_probe: bool = False,
    limits: ResourceLimits | None = None,
) -> WireRequest:
    limits = limits or ResourceLimits()
    image_operation = request.operation != "txt2img"
    transport = None
    if request.pose_control is not None:
        if backend_id != "drawthings-cloud" or prepared is None or image_permit is not None:
            raise _reject(request, "pose_not_configured", "Pose requires cloud and retained prepared inputs.")
        if (
            ImageInputSnapshot.from_prepared(request.image_inputs.attempt_id, prepared, preserve_outside=False)
            != request.image_inputs
        ):
            raise _reject(request, "pose_input_mismatch", "Pose input differs from the recorded snapshot.")
        transport = encode_image_inputs(prepared, limits)
    elif image_operation:
        if not isinstance(image_permit, ImageOperationPermit) or prepared is None or request.image_inputs is None:
            raise _reject(request, "image_inputs_missing", "Image operations require owned inputs and an owner permit.")
        image_permit.validate_scope(
            backend_id, endpoint, request.model_id, request.operation, allow_probe=allow_image_probe
        )
        expected = ImageInputSnapshot.from_prepared(
            request.image_inputs.attempt_id, prepared, preserve_outside=request.image_inputs.preserve_outside
        )
        if expected != request.image_inputs:
            raise _reject(request, "image_input_mismatch", "Prepared inputs differ from the resolved request.")
        if backend_id == "drawthings-cloud" and (
            model_spec != exact_model_spec(request.model_id)
            or image_permit.model_description_sha256 != exact_model_description_digest(request.model_id)
        ):
            raise _reject(
                request,
                "exact_model_description_missing",
                "The published model description differs from the permit.",
            )
        transport = encode_image_inputs(prepared, limits)
    elif prepared is not None or image_permit is not None:
        raise _reject(request, "unexpected_image_inputs", "Text-to-image cannot include image transport or permits.")
    validate_supported(request, image_permitted=image_operation)
    sampler = SAMPLER_NAME[request.sampler_id]
    # Flow models (Flux, ...) need version-specific fields that a bare configuration leaves at library defaults.
    profile = profile_for(request.model_id, base_version)
    extras = dict(profile.extras) if profile else {}
    if request.num_frames is not None:
        # A video model never borrows another family's values: its own entry or nothing.
        own = video_profile(request.model_id)
        extras = dict(own.extras) if own else {}
    if image_operation:
        extras.update(mask_blur=0.0, mask_blur_outset=0, preserve_original_after_inpaint=False)
    config = GenConfig(
        model=request.model_id,
        width=request.width_px,
        height=request.height_px,
        steps=request.steps,
        guidance=request.guidance,
        sampler=sampler,
        strength=request.strength if image_operation else TXT2IMG_STRENGTH,
        batch_size=1,
        batch_count=1,
        **extras,
    )
    for selection in request.loras:
        config.add_lora(wire_lora_name(request, selection.identifier), selection.weight, LORA_MODE)
    if request.pose_control is not None:
        config.add_control(
            file=request.pose_control.checkpoint,
            weight=request.pose_control.strength,
            guidance_start=request.pose_control.guidance_start,
            guidance_end=request.pose_control.guidance_end,
            no_prompt=False,
            global_average_pooling=False,
            down_sampling_rate=1.0,
            control_mode=request.pose_control.control_mode,
            input_override="Pose",
            target_blocks=[],
        )
    configuration = config.to_fbs(request.seed)
    if request.num_frames is not None:
        configuration = with_num_frames(configuration, request.num_frames)
    if "guidance_embed" in extras:
        configuration = with_guidance_embed(configuration, extras["guidance_embed"])
    effective = verify_wire_configuration(request, configuration, extras)
    message = image_service.ImageGenerationRequest(
        scale_factor=1,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        configuration=configuration,
        user=CLIENT_NAME,
    )
    video_tensor = start_image_tensor(request) if request.start_image else None
    if video_tensor is not None:
        message.image = hashlib.sha256(video_tensor).digest()
        message.contents = [video_tensor]
    override = custom_lora_override(request)
    control_nets = b""
    if request.pose_control is not None:
        from dtflow.core.pose import control_spec

        control_nets = json.dumps([control_spec(request.pose_control.checkpoint)]).encode()
    if override or model_spec or control_nets:
        message.override = image_service.MetadataOverride(
            loras=override, models=json.dumps([model_spec]).encode() if model_spec else b"", control_nets=control_nets
        )
    if shared_secret:
        message.shared_secret = shared_secret
    if transport is not None:
        if request.pose_control is not None:
            message.hints = [
                image_service.HintProto(
                    hint_type="pose", tensors=[image_service.TensorAndWeight(tensor=transport.image, weight=1.0)]
                )
            ]
        else:
            message.image = transport.image
        if transport.mask is not None:
            message.mask = transport.mask
        message.contents = list(transport.contents)
        # Count the complete protobuf too, not just its tensors: config/prompt/
        # overrides consume the same transport budget. No payload is logged.
        if len(bytes(message)) > limits.transport_bytes:
            raise _reject(
                request, "image_request_byte_limit", "Encoded image request exceeds the transport byte limit."
            )
        if request.pose_control is not None:
            effective = (
                *effective,
                ("pose_tensor_sha256", transport.image.hex()),
                ("pose_cloud_acceptance", "unverified_owner_experiment"),
            )
        else:
            effective = (
                *effective,
                ("image_tensor_sha256", transport.image.hex()),
                ("mask_tensor_sha256", transport.mask.hex() if transport.mask is not None else ""),
                ("image_operation_evidence", image_permit.evidence_id),
                ("image_operation_status", image_permit.status),
                ("model_description_sha256", image_permit.model_description_sha256 or ""),
            )
    return WireRequest(message, effective)


def verify_wire_configuration(
    request: ResolvedGenerationRequest, configuration: bytes, extras: dict[str, float | bool] | None = None
) -> Metadata:
    """Decode what will be sent and assert it equals the resolved request; return the effective configuration."""
    decoded = dict(GenConfig.from_fbs(configuration).items())
    raw = GenerationConfiguration.GetRootAs(configuration, 0)
    if request.pose_control is not None:
        from drawthings_py.generated.dt_grpc.config_generated import ControlInputType, ControlMode

        control = raw.Controls(0) if raw.ControlsLength() == 1 else None
        if (
            control is None
            or control.File().decode() != request.pose_control.checkpoint
            or not _same_float32(control.Weight(), request.pose_control.strength)
            or control.InputOverride() != ControlInputType.Pose
            or control.ControlMode() != getattr(ControlMode, request.pose_control.control_mode)
            or not _same_float32(control.GuidanceStart(), request.pose_control.guidance_start)
            or not _same_float32(control.GuidanceEnd(), request.pose_control.guidance_end)
            or control.GlobalAveragePooling()
            or control.DownSamplingRate() != 1
        ):
            raise _reject(request, "pose_wire_mismatch", "Serialized pose control differs from the resolved request.")
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
    wanted = [(wire_lora_name(request, item.identifier), item.weight, LORA_MODE) for item in request.loras]
    if len(sent) != len(wanted) or any(
        (f, m) != (name, mode) or not _same_float32(w, weight)
        for (f, w, m), (name, weight, mode) in zip(sent, wanted, strict=True)
    ):
        mismatches.append("loras")
    if request.num_frames is not None and decoded.get("num_frames") != request.num_frames:
        mismatches.append("num_frames")
    if not _same_float32(decoded.get("guidance"), request.guidance):
        mismatches.append("guidance")
    if not _same_float32(raw.Strength(), request.strength):
        mismatches.append("strength")
    if request.operation != "txt2img" and (
        raw.MaskBlur() != 0 or raw.MaskBlurOutset() != 0 or raw.PreserveOriginalAfterInpaint()
    ):
        mismatches.append("provider_mask_adjustments")
    for key, wanted_value in (extras or {}).items():
        sent_value = decoded.get(key)
        if isinstance(wanted_value, bool):
            same = sent_value is wanted_value
        else:
            same = _same_float32(sent_value, wanted_value)
        if not same:
            mismatches.append(key)
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
    pairs["loras"] = ",".join(f"{wire_lora_name(request, item.identifier)}@{item.weight}" for item in request.loras)
    pairs["config_source"] = "drawthings-py " + version("drawthings-py")
    return tuple(sorted(pairs.items()))

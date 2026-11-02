"""Python port of upstream ``ScriptModels.swift`` (JSGenerationConfiguration, JSLoRA, JSControl, geometry).

Field names, types and the conversions visible to scripts follow the pinned upstream source. Decoding is strict like
Swift's ``Codable``: required keys must be present and correctly typed, optional keys may be absent or null.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# kind -> (minimum, maximum) for integers; floats are finite float32-range numbers.
_INT_RANGES = {
    "i8": (-(2**7), 2**7 - 1),
    "u8": (0, 2**8 - 1),
    "i32": (-(2**31), 2**31 - 1),
    "u32": (0, 2**32 - 1),
    "i64": (-(2**63), 2**63 - 1),
}
_F32_MAX = 3.4028234663852886e38

# (name, kind, optional). Order follows upstream ``JSGenerationConfiguration``.
CONFIGURATION_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("id", "i64", False),
    ("width", "u32", False),
    ("height", "u32", False),
    ("seed", "i64", False),
    ("steps", "u32", False),
    ("guidanceScale", "f32", False),
    ("strength", "f32", False),
    ("model", "str", True),
    ("sampler", "i8", False),
    ("hiresFix", "bool", False),
    ("hiresFixWidth", "u32", False),
    ("hiresFixHeight", "u32", False),
    ("hiresFixStrength", "f32", False),
    ("tiledDecoding", "bool", False),
    ("decodingTileWidth", "u32", False),
    ("decodingTileHeight", "u32", False),
    ("decodingTileOverlap", "u32", False),
    ("tiledDiffusion", "bool", False),
    ("diffusionTileWidth", "u32", False),
    ("diffusionTileHeight", "u32", False),
    ("diffusionTileOverlap", "u32", False),
    ("upscaler", "str", True),
    ("upscalerScaleFactor", "u8", False),
    ("imageGuidanceScale", "f32", False),
    ("seedMode", "i8", False),
    ("clipSkip", "u32", False),
    ("controls", "controls", False),
    ("loras", "loras", False),
    ("maskBlur", "f32", False),
    ("maskBlurOutset", "i32", False),
    ("sharpness", "f32", False),
    ("faceRestoration", "str", True),
    ("clipWeight", "f32", False),
    ("negativePromptForImagePrior", "bool", False),
    ("imagePriorSteps", "u32", False),
    ("refinerModel", "str", True),
    ("originalImageHeight", "u32", False),
    ("originalImageWidth", "u32", False),
    ("cropTop", "i32", False),
    ("cropLeft", "i32", False),
    ("targetImageHeight", "u32", False),
    ("targetImageWidth", "u32", False),
    ("aestheticScore", "f32", False),
    ("negativeAestheticScore", "f32", False),
    ("zeroNegativePrompt", "bool", False),
    ("refinerStart", "f32", False),
    ("negativeOriginalImageHeight", "u32", False),
    ("negativeOriginalImageWidth", "u32", False),
    ("batchCount", "u32", False),
    ("batchSize", "u32", False),
    ("numFrames", "u32", False),
    ("fps", "u32", False),
    ("motionScale", "u32", False),
    ("guidingFrameNoise", "f32", False),
    ("startFrameGuidance", "f32", False),
    ("shift", "f32", False),
    ("shiftForAudio", "f32", True),
    ("usesSolAttention", "bool", True),
    ("solAttentionStart", "i32", True),
    ("solAttentionTau", "f32", True),
    ("stage2Steps", "u32", False),
    ("stage2Guidance", "f32", False),
    ("stage2Shift", "f32", False),
    ("stochasticSamplingGamma", "f32", False),
    ("preserveOriginalAfterInpaint", "bool", False),
    ("t5TextEncoder", "bool", False),
    ("separateClipL", "bool", False),
    ("clipLText", "str", True),
    ("separateOpenClipG", "bool", False),
    ("openClipGText", "str", True),
    ("speedUpWithGuidanceEmbed", "bool", False),
    ("guidanceEmbed", "f32", False),
    ("resolutionDependentShift", "bool", False),
    ("teaCache", "bool", False),
    ("teaCacheStart", "i32", False),
    ("teaCacheEnd", "i32", False),
    ("teaCacheThreshold", "f32", False),
    ("teaCacheMaxSkipSteps", "i32", False),
    ("separateT5", "bool", False),
    ("t5Text", "str", True),
    ("causalInference", "i32", False),
    ("causalInferencePad", "i32", False),
    ("cfgZeroStar", "bool", False),
    ("cfgZeroInitSteps", "i32", False),
    ("compressionArtifacts", "str", True),
    ("compressionArtifactsQuality", "f32", True),
    ("colorCalibration", "str", True),
    ("expandPromptToJson", "bool", False),
)
CONFIGURATION_KEYS = frozenset(name for name, _, _ in CONFIGURATION_FIELDS)

LORA_FIELDS = (("file", "str", True), ("weight", "f32", False), ("mode", "str", True))
CONTROL_FIELDS = (
    ("file", "str", True),
    ("weight", "f32", False),
    ("guidanceStart", "f32", False),
    ("guidanceEnd", "f32", False),
    ("noPrompt", "bool", False),
    ("globalAveragePooling", "bool", False),
    ("downSamplingRate", "f32", False),
    ("controlImportance", "str", False),
    ("inputOverride", "str", False),
    ("targetBlocks", "strlist", False),
)

LORA_MODES = ("all", "base", "refiner")  # unknown values decode as "all"
CONTROL_IMPORTANCE = ("balanced", "prompt", "control")  # unknown values decode as "balanced"
COMPRESSION_ARTIFACTS = ("disabled", "h264", "h265", "jpeg")  # unknown values decode as "disabled"
COLOR_CALIBRATION = ("none", "lab")  # anything but "lab" decodes as disabled
DEFAULT_COMPRESSION_QUALITY = 43.1
DIMENSION_FIELDS = (
    "width",
    "height",
    "hiresFixWidth",
    "hiresFixHeight",
    "decodingTileWidth",
    "decodingTileHeight",
    "decodingTileOverlap",
)


class ScriptModelError(ValueError):
    """A script-supplied value does not decode like upstream's ``swiftObject`` (message names the field)."""


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScriptModelError(f"{name} must be a number.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ScriptModelError(f"{name} must be finite.")
    return value


def _decode(kind: str, value: Any, name: str) -> Any:
    if kind == "bool":
        if not isinstance(value, bool):
            raise ScriptModelError(f"{name} must be a boolean.")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise ScriptModelError(f"{name} must be a string.")
        return value
    if kind == "strlist":
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise ScriptModelError(f"{name} must be a list of strings.")
        return list(value)
    if kind == "f32":
        number = float(_number(value, name))
        if abs(number) > _F32_MAX:
            raise ScriptModelError(f"{name} is outside the float32 range.")
        return number
    if kind in _INT_RANGES:
        number = _number(value, name)
        if number != int(number):
            raise ScriptModelError(f"{name} must be an integer.")
        low, high = _INT_RANGES[kind]
        if not low <= int(number) <= high:
            raise ScriptModelError(f"{name} is outside {low}..{high}.")
        return int(number)
    raise AssertionError(kind)


def _decode_object(spec: tuple[tuple[str, str, bool], ...], value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScriptModelError(f"{label} must be an object.")
    decoded: dict[str, Any] = {}
    for name, kind, optional in spec:
        if name not in value or value[name] is None:
            if not optional:
                raise ScriptModelError(f"{label}.{name} is required.")
            decoded[name] = None
            continue
        decoded[name] = _decode(kind, value[name], f"{label}.{name}")
    return decoded


@dataclass(frozen=True, slots=True)
class DecodedConfiguration:
    """Strictly decoded script configuration plus the keys upstream would log as unknown."""

    values: dict[str, Any]
    unknown_keys: tuple[str, ...]


def decode_configuration(value: Any) -> DecodedConfiguration:
    """Decode a JS configuration object like ``swiftObject(..., JSGenerationConfiguration)``."""
    if not isinstance(value, dict):
        raise ScriptModelError("Missing configuration parameter")
    decoded: dict[str, Any] = {}
    for name, kind, optional in CONFIGURATION_FIELDS:
        if name not in value or value[name] is None:
            if not optional:
                raise ScriptModelError(f"configuration.{name} is required.")
            decoded[name] = None
        elif kind == "controls":
            if not isinstance(value[name], (list, tuple)):
                raise ScriptModelError("configuration.controls must be a list.")
            decoded[name] = [
                _decode_object(CONTROL_FIELDS, item, f"configuration.controls[{index}]")
                for index, item in enumerate(value[name])
            ]
        elif kind == "loras":
            if not isinstance(value[name], (list, tuple)):
                raise ScriptModelError("configuration.loras must be a list.")
            decoded[name] = [
                _decode_object(LORA_FIELDS, item, f"configuration.loras[{index}]")
                for index, item in enumerate(value[name])
            ]
        else:
            decoded[name] = _decode(kind, value[name], f"configuration.{name}")
    unknown = tuple(sorted(key for key in value if key not in CONFIGURATION_KEYS))
    return DecodedConfiguration(decoded, unknown)


def round_dimensions(values: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Upstream ``fixDimensionIfNecessary``: round dimensions down to a multiple of 64 and return the warnings."""
    fixed = dict(values)
    warnings = []
    for name in DIMENSION_FIELDS:
        dimension = fixed[name]
        if dimension % 64 != 0:
            rounded = dimension - dimension % 64
            warnings.append(
                "All dimensions (width, height, hi-res fix width, hi-res fix height must be divisible by 64. "
                f"Rounding {dimension} down to {rounded}..."
            )
            fixed[name] = rounded
    return fixed, tuple(warnings)


def normalize_configuration(values: dict[str, Any]) -> dict[str, Any]:
    """Upstream ``createGenerationConfiguration`` normalizations that are independent of Draw Things internals.

    A negative seed means "random" upstream; here it stays ``-1`` so the DTFlow resolver can record an explicit seed.
    """
    result = dict(values)
    for name in ("refinerModel", "upscaler", "faceRestoration", "clipLText", "openClipGText", "t5Text"):
        if result[name] == "":
            result[name] = None
    result["batchCount"] = max(result["batchCount"], 1)
    result["batchSize"] = min(max(result["batchSize"], 1), 4)
    quality = result["compressionArtifactsQuality"]
    result["compressionArtifactsQuality"] = min(
        max(DEFAULT_COMPRESSION_QUALITY if quality is None else quality, 0), 100
    )
    compression = (result["compressionArtifacts"] or "").lower()
    result["compressionArtifacts"] = compression if compression in COMPRESSION_ARTIFACTS[1:] else "disabled"
    result["colorCalibration"] = "lab" if (result["colorCalibration"] or "").lower() == "lab" else "none"
    result["loras"] = [
        {**item, "mode": item["mode"] if item["mode"] in LORA_MODES else "all"} for item in result["loras"]
    ]
    result["controls"] = [
        {
            **item,
            "controlImportance": item["controlImportance"].lower()
            if item["controlImportance"].lower() in CONTROL_IMPORTANCE
            else "balanced",
        }
        for item in result["controls"]
    ]
    result["causalInferenceEnabled"] = result["causalInference"] > 0
    return result

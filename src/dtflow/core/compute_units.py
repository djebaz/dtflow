"""Purely local Draw Things Compute Units estimate for supported text-to-image models.

The arithmetic follows the instruction-count path of drawthingsai/draw-things-community:
ModelZoo/Sources/ComputeUnits.swift (blob b4d64fd012d3d603c04a623fa18943c9fdd15307)
and SwiftDiffusion/Sources/Models/InstructionCount/*.swift, all at source commit
82bd56c81a313060dceb34092bcf9d671c39a739. It is not a server
quote, credit balance, hardware benchmark, compatibility check, or request permit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from dtflow.core.model_profiles import exact_model_spec, profile_for

CALIBRATION = 5.14816e-12
SUPPORTED = frozenset(
    {
        "flux1",
        "flux2",
        "flux2_4b",
        "flux2_9b",
        "z_image",
        "cosmos2.5_2b",
        "qwen_image",
        "qwen_image_2.1",
        "krea_2",
        "ideogram_4",
    }
)
TEXT_ONLY_MODIFIERS = frozenset({"none", "kontext", "kontext_kv"})


@dataclass(frozen=True, slots=True)
class ComputeUnitEstimate:
    units: int | None
    reason: str


def _dense(rows: int, inputs: int, outputs: int) -> int:
    return rows * inputs * outputs


def _attention(batch: int, heads: int, dim: int, query: int, keys: int) -> int:
    return batch * heads * (2 * dim + 5) * query * keys


def _patches(count: int, channels: int, inputs: int) -> int:
    return count * channels * 4 * inputs


def _flux1(image: int, tokens: int, embed: bool) -> tuple[int, int]:
    c, double, single = 3072, 19, 38
    seq = image + tokens
    attn = _attention(1, c // 128, 128, seq, seq)
    main = _patches(image, c, 16) + double * (12 * _dense(seq, c, c) + attn)
    for index in range(single):
        projection = image if index == single - 1 else seq
        main += (
            3 * _dense(seq, c, c)
            + _dense(projection, c, c)
            + _dense(projection, c, 4 * c)
            + _dense(projection, 4 * c, c)
            + attn
        )
    main += _dense(image, c, 64)
    fixed = _dense(1, 256, c) + _dense(1, c, c)
    if embed:
        fixed += _dense(1, 256, c) + _dense(1, c, c)
    fixed += _dense(1, 768, c) + _dense(1, c, c)
    fixed += _dense(tokens, 4096, c)
    fixed += (12 * double + 3 * single + 2) * _dense(1, c, c)
    return main, fixed


def _flux2(image: int, tokens: int, version: str, embed: bool) -> tuple[int, int]:
    c, (double, single) = {
        "flux2": (6144, (8, 48)),
        "flux2_9b": (4096, (8, 24)),
        "flux2_4b": (3072, (5, 20)),
    }[version]
    seq = image + tokens
    attn = _attention(1, c // 128, 128, seq, seq)
    main = _patches(image, c, 32)
    for _ in range(double):
        for rows in (tokens, image):
            main += 4 * _dense(rows, c, c) + 2 * _dense(rows, c, 3 * c) + _dense(rows, 3 * c, c)
        main += attn
    for index in range(single):
        projected = image if index == single - 1 else seq
        main += (
            3 * _dense(seq, c, c)
            + _dense(projected, c, c)
            + 2 * _dense(projected, c, 3 * c)
            + _dense(projected, 3 * c, c)
            + attn
        )
    main += _dense(image, c, 128)
    fixed = _dense(tokens, 4096, c) + _dense(1, 256, c) + _dense(1, c, c)
    if embed:
        fixed += _dense(1, 256, c) + _dense(1, c, c)
    fixed += 17 * _dense(1, c, c)
    return main, fixed


def _z_block(keys: int, queries: int, c: int, segments: tuple[int, ...] = ()) -> int:
    count = (
        2 * _dense(keys, c, c) + 2 * _dense(queries, c, c) + 2 * _dense(queries, c, 10240) + _dense(queries, 10240, c)
    )
    if len(segments) > 1 and keys == queries:
        count += sum(_attention(1, c // 128, 128, part, part) for part in segments)
    else:
        count += _attention(1, c // 128, 128, queries, keys)
    return count


def _z_image(image: int, tokens: int, c: int, layers: int) -> tuple[int, int]:
    rounded = (image + 31) // 32 * 32
    seq = rounded + tokens
    main = (
        _dense(image, 64, c)
        + 2 * _z_block(rounded, rounded, c)
        + (layers - 1) * _z_block(seq, seq, c)
        + _z_block(seq, image, c)
        + _dense(image, c, 64)
    )
    if c != 3840:  # Cosmos 2.5 shares this main path but has no fixed path.
        return main, 0
    rt = (tokens + 31) // 32 * 32
    fixed = (
        _dense(tokens * 2, 2560, c)
        + _dense(1, 256, 1024)
        + _dense(1, 1024, 256)
        + 2 * _z_block(rt * 2, rt * 2, c, (rt, rt))
        + (2 * 4 + layers * 4 + 1) * _dense(1, 256, c)
    )
    return main, fixed


def _qwen21(count: int, tokens: int, steps: int, cfg: int) -> tuple[int, int]:
    c = 4096
    rows = cfg * count
    main = (
        _dense(rows, 64, c)
        + 32 * (4 * _dense(rows, c, c) + 3 * _dense(rows, c, 12288) + _attention(cfg, 32, 128, count, count + tokens))
        + _dense(rows, c, 64)
    )
    text = cfg * tokens
    fixed = (
        2 * _dense(text, c, c)
        + _dense(cfg * (steps + 1), 256, c)
        + 6 * _dense(cfg * (steps + 1), c, c)
        + 31 * (4 * _dense(text, c, c) + 3 * _dense(text, c, 12288) + _attention(cfg, 32, 128, tokens, tokens))
        + 2 * _dense(text, c, c)
    )
    return main, fixed


def _qwen(image: int, tokens: int) -> tuple[int, int]:
    c, layers = 3072, 60
    seq = image + tokens
    attn = _attention(1, c // 128, 128, seq, seq)
    main = _patches(image, c, 16)
    for index in range(layers):
        main += 3 * _dense(tokens, c, c) + 3 * _dense(image, c, c) + attn
        if index != layers - 1:
            main += _dense(tokens, c, c) + _dense(tokens, c, 4 * c) + _dense(tokens, 4 * c, c)
        main += _dense(image, c, c) + _dense(image, c, 4 * c) + _dense(image, 4 * c, c)
    main += _dense(image, c, 64)
    fixed = _dense(tokens, 3584, c) + _dense(1, 256, c) + ((layers - 1) * 12 + 8 + 3) * _dense(1, c, c)
    return main, fixed


def _krea_block(
    query: int, keys: int, hidden: int, heads: int, kv_heads: int, intermediate: int, segments: tuple[int, ...] = ()
) -> int:
    kv_width = hidden // heads * kv_heads
    count = (
        3 * _dense(query, hidden, hidden)
        + 2 * _dense(keys, hidden, kv_width)
        + 2 * _dense(query, hidden, intermediate)
        + _dense(query, intermediate, hidden)
    )
    if len(segments) > 1 and query == keys:
        count += sum(_attention(1, heads, hidden // heads, item, item) for item in segments)
    else:
        count += _attention(1, heads, hidden // heads, query, keys)
    return count


def _krea(image: int, tokens: int, cfg: int) -> tuple[int, int]:
    c, layers = 6144, 28
    seq = image + tokens
    main = (
        _dense(image, 64, c)
        + (layers - 1) * _krea_block(seq, seq, c, 48, 12, 16384)
        + _krea_block(image, seq, c, 48, 12, 16384)
        + _dense(image, c, 64)
    )
    text_lengths = (tokens, tokens) if cfg > 1 else (0, tokens)
    length = sum(text_lengths)
    # Krea text fusion is run once, with one layerwise 12-token batch per text token.
    fusion = (
        2 * length * _krea_block(12, 12, 2560, 20, 20, 6912)
        + _dense(length * 2560, 12, 1)
        + 2 * _krea_block(length, length, 2560, 20, 20, 6912, text_lengths if text_lengths[0] else ())
    )
    # Fixed count is per CFG image batch. Text fusion is not multiplied by CFG.
    fixed_per_batch = _dense(length, 2560, c) + _dense(length, c, c) + _dense(1, 256, c) + 7 * _dense(1, c, c)
    return main * cfg, fusion + fixed_per_batch * cfg


def _ideogram(image: int, tokens: int) -> tuple[int, int]:
    c, layers = 4608, 34
    seq = image + tokens

    def block(query: int) -> int:
        return (
            2 * _dense(query, c, c)
            + 2 * _dense(seq, c, c)
            + _attention(1, c // 256, 256, query, seq)
            + 2 * _dense(query, c, 12288)
            + _dense(query, 12288, c)
        )

    main = _dense(image, 128, c) + (layers - 1) * block(seq) + block(image) + _dense(image, c, 128)
    fixed = (
        _dense(tokens, 4096 * 13, c) + 2 * _dense(1, c, c) + _dense(1, c, 512) + (layers * 4 + 1) * _dense(1, 512, c)
    )
    return main, fixed


def estimate_compute_units(
    *,
    model_id: str,
    width_px: int,
    height_px: int,
    steps: int,
    guidance: float,
    has_loras: bool = False,
    operation: str = "txt2img",
    base_model: str | None = None,
) -> ComputeUnitEstimate:
    """Estimate one T2I output, using a classified base family when exact metadata is absent."""
    if operation != "txt2img":
        return ComputeUnitEstimate(None, "Image edits are not supported by this estimate.")
    if not model_id:
        return ComputeUnitEstimate(None, "Select a checkpoint first.")
    spec = exact_model_spec(model_id)
    base_estimate = spec is None or has_loras
    if spec is None:
        # Explicit catalogue classification only; never infer architecture from the filename
        # or substitute another checkpoint's technical description. Broad FLUX families
        # use their base architecture, not a claim about the selected variant.
        version = {"flux_1": "flux1", "flux_2": "flux2"}.get(base_model, base_model)
        if version not in SUPPORTED:
            return ComputeUnitEstimate(None, "No supported base architecture is known for this checkpoint.")
        profile = profile_for(model_id, version)
        extras = dict(profile.extras) if profile else {}
        spec = {"version": version, "guidance_embed": "guidance_embed" in extras}
    version = spec.get("version")
    if version not in SUPPORTED:
        return ComputeUnitEstimate(None, "Model architecture not supported by this estimate.")
    if spec.get("modifier", "none") not in TEXT_ONLY_MODIFIERS:
        base_estimate = True  # Count the base architecture without the unsupported modifier.
    if any(type(x) is not int or x <= 0 for x in (width_px, height_px, steps)):
        return ComputeUnitEstimate(None, "Invalid dimensions or steps.")
    if width_px % 64 or height_px % 64 or type(guidance) not in (float, int) or not math.isfinite(guidance):
        return ComputeUnitEstimate(None, "Invalid dimensions or guidance.")
    profile = profile_for(model_id, version)
    if spec.get("guidance_embed") and profile is None:
        return ComputeUnitEstimate(None, "Effective guidance-embed setting is unknown.")
    extras = dict(profile.extras) if profile else {}
    guidance_embed = spec.get("guidance_embed") is True and extras.get("speed_up_with_guidance_embed") is True
    cfg = 1 if spec.get("is_consistency_model") is True or guidance_embed else (2 if abs(guidance - 1) > 1e-3 else 1)
    # Draw Things' GenerationConfiguration.startWidth is the pixel dimension / 64.
    # Qwen-Image 2.1 then scales it by 4; all other supported families by 8.
    scale = 4 if version == "qwen_image_2.1" else 8
    height, width = height_px // 64 * scale, width_px // 64 * scale
    image = height // 2 * (width // 2)
    padded = spec.get("padded_text_encoding_length")
    if padded is not None and (type(padded) is not int or padded < 0):
        return ComputeUnitEstimate(None, "Invalid published token-length metadata.")
    tokens = padded or (256 if version == "krea_2" else 77 if version == "cosmos2.5_2b" else 512)

    if version == "qwen_image_2.1":
        main, fixed = _qwen21(height * width, tokens, steps, cfg)
        instruction_units = main * steps + fixed
    elif version == "krea_2":
        main, fixed = _krea(image, tokens, cfg)  # includes CFG and once-only text fusion
        instruction_units = main * steps + fixed
    else:
        if version == "flux1":
            main, fixed = _flux1(image, tokens, guidance_embed)
        elif version in {"flux2", "flux2_4b", "flux2_9b"}:
            main, fixed = _flux2(image, tokens, version, guidance_embed)
        elif version == "z_image":
            main, fixed = _z_image(image, tokens, 3840, 30)
        elif version == "cosmos2.5_2b":
            main, fixed = _z_image(image, tokens, 2048, 28)
        elif version == "qwen_image":
            main, fixed = _qwen(image, tokens)
        elif version == "ideogram_4":
            main, fixed = _ideogram(image, tokens)
        else:
            return ComputeUnitEstimate(None, "Unsupported model version.")
        instruction_units = (main * steps + fixed) * cfg
    units = math.ceil(instruction_units * CALIBRATION)
    if units <= 0:
        return ComputeUnitEstimate(None, "The instruction estimate could not be resolved.")
    if base_estimate:
        reason = "Base architecture estimate; unknown LoRA modifiers or missing metadata are not included."
    else:
        reason = "Local open-source estimate, not a DT+ server quote or billing proof."
    return ComputeUnitEstimate(units, reason)

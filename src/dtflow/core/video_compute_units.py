"""Offline instruction-count proxy for Draw Things video model families.

Source: drawthingsai/draw-things-community at
82bd56c81a313060dceb34092bcf9d671c39a739, including
ModelZoo/Sources/ComputeUnits.swift and the matching SwiftDiffusion
Models/InstructionCount files.

This estimator is read-only and does not imply that DTFlow can submit a
video job or that DT+ will bill exactly this number.
"""

from __future__ import annotations

import math

from dtflow.core.compute_units import (
    CALIBRATION,
    ComputeUnitEstimate,
    _architecture_registry,
    _attention,
    _dense,
)
from dtflow.core.model_profiles import exact_model_spec

VIDEO_STRATEGIES = frozenset(
    {"video_hunyuan", "video_wan", "video_ltx2", "video_minimax_h3"}
)


def _video_size(width_px: int, height_px: int, scale: int) -> tuple[int, int]:
    """Return upstream latent width/height derived from pixel dimensions."""
    return width_px // 64 * scale, height_px // 64 * scale


def _effective_frames(frames: int, strategy: str) -> int:
    """Pack requested output frames exactly as ComputeUnits.swift does."""
    if strategy in ("video_wan", "video_hunyuan"):
        return max(1, (frames - 1) // 4 + 1)
    if strategy == "video_ltx2":
        return max(1, (frames - 1) // 8 + 1)
    if strategy == "video_minimax_h3":
        return 1 if frames == 1 else ((max(frames, 5) - 5 + 16) // 17) * 5 + 2
    raise ValueError("Unsupported video frame-packing strategy.")


def _wan(
    *,
    time: int,
    height: int,
    width: int,
    tokens: int,
    image: bool,
    causal_frames: int,
    causal_pad: int,
    architecture: dict,
) -> tuple[int, int]:
    """WanInstructionCount + WanFixedInstructionCount with empty VACE layers."""
    c = architecture["channels"]
    layers, intermediate = architecture["layers"], architecture["intermediate_size"]
    output_channels = architecture["output_channels"]
    h, w = height // 2, width // 2
    length = time * h * w
    heads = c // 128
    main = time * h * w * c * 4 * output_channels

    if causal_frames > 0 and causal_frames + causal_pad < time:
        frame_size = h * w
        self_attn = 0
        for index in range((time + causal_frames - 1) // causal_frames):
            query = min(length - index * frame_size * causal_frames, frame_size * causal_frames)
            keys = min(length, (index + 1) * frame_size * causal_frames + frame_size * causal_pad)
            self_attn += _attention(1, heads, 128, query, keys)
    else:
        self_attn = _attention(1, heads, 128, length, length)

    per_layer = 6 * _dense(length, c, c) + self_attn
    per_layer += _attention(1, heads, 128, length, tokens)
    if image:
        per_layer += _attention(1, heads, 128, length, 257)
    per_layer += _dense(length, c, intermediate) + _dense(length, intermediate, c)
    main += layers * per_layer
    main += _dense(length, c, 4 * output_channels)

    fixed = _dense(tokens, 4096, c) + _dense(tokens, c, c)
    fixed += _dense(1, 256, c) + 7 * _dense(1, c, c)
    fixed += 2 * layers * _dense(tokens, c, c)
    if image:
        fixed += _dense(257, 1280, 1280) + _dense(257, 1280, c)
        fixed += 2 * layers * _dense(257, c, c)
    return main, fixed


def _hunyuan(time: int, height: int, width: int, tokens: int, cfg: int, architecture: dict) -> tuple[int, int]:
    """HunyuanInstructionCount and once-only HunyuanFixedInstructionCount."""
    c = architecture["channels"]
    double, single = architecture["double_layers"], architecture["single_layers"]
    image = time * (height // 2) * (width // 2)
    total = image + tokens
    heads = c // 128
    main = image * c * 4 * 16

    joint = (
        4 * _dense(tokens, c, c) + _dense(tokens, c, c * 4) + _dense(tokens, c * 4, c)
        + 4 * _dense(image, c, c) + _dense(image, c, c * 4) + _dense(image, c * 4, c)
        + _attention(1, heads, 128, total, total)
    )
    main += double * joint
    for index in range(single):
        projected = image if index == single - 1 else total
        main += 3 * _dense(total, c, c) + _dense(projected, c, c)
        main += _dense(projected, c, 4 * c) + _dense(projected, 4 * c, c)
        main += _attention(1, heads, 128, total, total)
    main += _dense(image, c, 64)

    text_lengths = (tokens, tokens) if cfg > 1 else (0, tokens)
    vec_rows = cfg
    text_rows = sum(text_lengths)
    fixed = 2 * _dense(vec_rows, 256, c) + 2 * _dense(vec_rows, c, c)
    fixed += _dense(vec_rows, 4096, c) + _dense(text_rows, 4096, c)
    for _ in range(2):
        fixed += 4 * _dense(text_rows, 3072, 3072)
        for length in text_lengths:
            if length:
                fixed += _attention(1, 24, 128, length, length)
        fixed += 2 * _dense(vec_rows, c, 3072)
        fixed += _dense(text_rows, 3072, 3072 * 4) + _dense(text_rows, 3072 * 4, 3072)
    fixed += _dense(vec_rows, 256, c) + _dense(vec_rows, c, c)
    fixed += _dense(vec_rows, 768, c) + _dense(vec_rows, c, c)
    fixed += _dense(vec_rows, 256, c) + _dense(vec_rows, c, c)
    fixed += (double * 12 + single * 3 + 2) * _dense(vec_rows, c, c)
    return main, fixed


def _ltx_self(length: int, c: int, gated: bool) -> int:
    count = 4 * _dense(length, c, c) + _attention(1, 32, c // 32, length, length)
    if gated:
        count += _dense(length, c, 32)
    return count


def _ltx_cross(
    queries: int,
    keys: int,
    query_size: int,
    key_size: int,
    attn_size: int,
    output_size: int,
    *,
    kv_cached: bool,
    gated: bool,
) -> int:
    count = _dense(queries, query_size, attn_size)
    if not kv_cached:
        count += 2 * _dense(keys, key_size, attn_size)
    count += _attention(1, 32, attn_size // 32, queries, keys)
    count += _dense(queries, attn_size, output_size)
    if gated:
        count += _dense(queries, query_size, 32)
    return count


def _ltx_feed(length: int, c: int) -> int:
    return _dense(length, c, 4 * c) + _dense(length, 4 * c, c)


def _ltx_ada(timesteps: int, c: int, count: int) -> int:
    return _dense(timesteps, 256, c) + (1 + count) * _dense(timesteps, c, c)


def _ltx2(time: int, height: int, width: int, tokens: int, architecture: dict) -> tuple[int, int]:
    """Count video, audio, cross-modal attention and AdaLN for LTX2/2.3."""
    cv, ca = architecture["channels"], architecture["audio_channels"]
    layers = architecture["layers"]
    gated = architecture["gated_attention"]
    kv = architecture["kv_cache"]
    cross_ada = architecture["ada_ln_cross"]
    audio_frames = (time - 1) * 8 + 1
    video_rows = time * height * width
    padded_tokens = max(tokens, 1024)

    main = time * height * width * cv * 128 + _dense(audio_frames, 128, ca)
    for _ in range(layers):
        main += _ltx_self(video_rows, cv, gated)
        main += _ltx_cross(video_rows, padded_tokens, cv, cv, cv, cv, kv_cached=kv, gated=gated)
        main += _ltx_self(audio_frames, ca, gated)
        main += _ltx_cross(audio_frames, padded_tokens, ca, ca, ca, ca, kv_cached=kv, gated=gated)
        main += _ltx_cross(video_rows, audio_frames, cv, ca, ca, cv, kv_cached=False, gated=gated)
        main += _ltx_cross(audio_frames, video_rows, ca, cv, ca, ca, kv_cached=False, gated=gated)
        main += _ltx_feed(video_rows, cv) + _ltx_feed(audio_frames, ca)
    main += _dense(video_rows, cv, 128) + _dense(audio_frames, ca, 128)

    fixed = 0
    if architecture["context_projection"]:
        fixed += _dense(padded_tokens, cv, cv) * 2
        fixed += _dense(padded_tokens, ca, ca) * 2
    for c in (cv, ca):
        fixed += _ltx_ada(1, c, 9 if cross_ada else 6)
        if cross_ada:
            fixed += _ltx_ada(1, c, 2)
        fixed += _ltx_ada(1, c, 4) + _ltx_ada(1, c, 1)
    if kv:
        fixed += layers * 2 * (
            _dense(padded_tokens, cv, cv) + _dense(padded_tokens, ca, ca)
        )
    return main, fixed


def _minimax_h3(time: int, height: int, width: int, tokens: int, cfg: int, architecture: dict) -> tuple[int, int]:
    """Count MiniMax H3's video/audio network and its once-only context refinement."""
    c, layers = architecture["channels"], architecture["layers"]
    intermediate = architecture["intermediate_size"]
    packed_frames = 1 if time == 1 else (time - 2) // 5 * 17 + 5
    # Swift Double.rounded() uses the nearest integer, ties away from zero.
    audio = 2 * math.floor(packed_frames / architecture["fps"] * 40 + 0.5)
    video = time * (height // 2) * (width // 2)
    rows = tokens + audio + video
    attention_width = 56 * 128

    def transformer(length: int) -> int:
        return (
            3 * _dense(length, c, attention_width)
            + _attention(1, 56, 128, length, length)
            + _dense(length, attention_width, c)
            + 2 * _dense(length, c, intermediate) + _dense(length, intermediate, c)
        )

    main = _dense(video, 96, c) + _dense(audio, 32, c)
    main += layers * transformer(rows)
    main += _dense(video, c, 96) + _dense(audio, c, 32)

    # This fixed count is NOT multiplied by CFG in upstream ComputeUnits.swift.
    text_lengths = (tokens, tokens) if cfg > 1 else (0, tokens)
    fixed_rows = 2
    fixed = _dense(fixed_rows, 256, c) + _dense(fixed_rows, c, 2688)
    fixed += (layers * 18 + 2) * _dense(fixed_rows, 2688, c)
    fixed += _dense(sum(text_lengths), 5120, c)
    for length in text_lengths:
        if length:
            fixed += 2 * transformer(length)
    return main, fixed


def estimate_video_compute_units(
    *,
    model_id: str,
    width_px: int,
    height_px: int,
    steps: int,
    guidance: float,
    num_frames: int,
    has_image: bool = False,
    has_loras: bool = False,
    causal_frames: int = 0,
    causal_padding: int = 0,
) -> ComputeUnitEstimate:
    """Estimate one video from explicit parameters without submitting generation.

    LoRA-specific sampler modifiers and unmodelled conditioning are omitted.
    This function does not make a checkpoint or a video mode provider-compatible.
    """
    spec = exact_model_spec(model_id) if model_id else None
    if spec is None:
        return ComputeUnitEstimate(None, "Exact video checkpoint metadata is required.")
    architecture = _architecture_registry().get(spec.get("version"))
    if architecture is None or architecture["strategy"] not in VIDEO_STRATEGIES:
        return ComputeUnitEstimate(None, "Video architecture is not implemented.")
    if any(type(x) is not int or x <= 0 for x in (width_px, height_px, steps, num_frames)):
        return ComputeUnitEstimate(None, "Invalid dimensions, step count or frame count.")
    if width_px % 64 or height_px % 64:
        return ComputeUnitEstimate(None, "Video dimensions must be multiples of 64 pixels.")
    if type(guidance) not in (int, float) or not math.isfinite(guidance):
        return ComputeUnitEstimate(None, "Invalid CFG guidance value.")
    if any(type(x) is not int or x < 0 for x in (causal_frames, causal_padding)):
        return ComputeUnitEstimate(None, "Invalid causal frame configuration.")
    if causal_padding and not causal_frames:
        return ComputeUnitEstimate(None, "Causal padding requires causal inference.")
    strategy = architecture["strategy"]
    if (causal_frames or causal_padding) and strategy != "video_wan":
        return ComputeUnitEstimate(None, "Causal inference is unsupported for this architecture.")
    if has_image and strategy == "video_minimax_h3":
        return ComputeUnitEstimate(None, "MiniMax reference-image modifiers require additional metadata.")

    width, height = _video_size(width_px, height_px, architecture["latent_scale"])
    time = _effective_frames(num_frames, strategy)
    tokens = spec.get("padded_text_encoding_length") or architecture["default_text_tokens"]
    if type(tokens) is not int or tokens <= 0:
        return ComputeUnitEstimate(None, "Invalid text token metadata.")
    guidance_embedded = spec.get("guidance_embed") is True
    cfg = 1 if spec.get("is_consistency_model") is True or guidance_embedded else (2 if guidance != 1 else 1)

    if strategy == "video_wan":
        inject_image = has_image and architecture["supports_image"]
        main, fixed = _wan(
            time=time,
            height=height,
            width=width,
            tokens=tokens,
            image=inject_image,
            causal_frames=causal_frames,
            causal_pad=causal_padding,
            architecture=architecture,
        )
    elif strategy == "video_hunyuan":
        main, fixed = _hunyuan(time, height, width, tokens, cfg, architecture)
    elif strategy == "video_ltx2":
        main, fixed = _ltx2(time, height, width, tokens, architecture)
    elif strategy == "video_minimax_h3":
        main, fixed = _minimax_h3(time, height, width, tokens, cfg, architecture)
    else:
        return ComputeUnitEstimate(None, "Unsupported video strategy.")

    fixed_factor = 1 if strategy == "video_minimax_h3" else cfg
    instructions = main * steps * cfg + fixed * fixed_factor
    units = math.ceil(instructions * CALIBRATION * architecture["usage_bias"])
    if units <= 0:
        return ComputeUnitEstimate(None, "The video instruction count could not be resolved.")
    reason = (
        "Base checkpoint video estimate; LoRA modifier effects are omitted."
        if has_loras
        else "Offline video estimate; not a DT+ billing quote or availability guarantee."
    )
    return ComputeUnitEstimate(units, reason)

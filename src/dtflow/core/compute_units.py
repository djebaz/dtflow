"""Purely local Draw Things Compute Units estimate for supported text-to-image models.

The arithmetic follows the instruction-count path of drawthingsai/draw-things-community:
ModelZoo/Sources/ComputeUnits.swift (blob b4d64fd012d3d603c04a623fa18943c9fdd15307)
and SwiftDiffusion/Sources/Models/InstructionCount/*.swift, all at source commit
82bd56c81a313060dceb34092bcf9d671c39a739. It is not a server
quote, credit balance, hardware benchmark, compatibility check, or request permit.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dtflow.core.model_profiles import exact_model_spec, profile_for

CALIBRATION = 5.14816e-12
_REGISTRY_FILE = Path(__file__).parents[1] / "resources" / "compute_unit_architectures.json"
_SOURCE_COMMIT = "82bd56c81a313060dceb34092bcf9d671c39a739"
_REQUIRED_PARAMETERS = {
    "flux1": ("channels", "double_layers", "single_layers"),
    "flux2": ("channels", "double_layers", "single_layers"),
    "z_image": ("channels", "layers", "include_fixed"),
    "qwen_image": ("channels", "layers"),
    "qwen_image_2.1": ("channels", "layers"),
    "krea_2": ("channels", "layers"),
    "ideogram_4": ("channels", "layers"),
    "mmdit": ("channels", "layers"),
    "pixart": ("channels", "layers"),
    "auraflow": ("channels", "double_layers", "single_layers"),
    "ernie": ("channels", "layers", "intermediate_size"),
    "flux3": ("channels", "double_layers", "single_layers"),
    "moss": ("channels", "layers", "intermediate_size"),
    "hidream_o1": ("channels", "layers", "intermediate_size"),
    "unet_v1": ("channels", "num_repeat"),
    "unet_v2": ("channels", "num_repeat"),
    "unet_xl": ("channels", "middle_attention", "label_input_channels"),
}


@cache
def _architecture_registry() -> dict[str, dict]:
    """Load pinned instruction parameters; fail closed on missing or invalid entries."""
    try:
        document = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or (
            document.get("schema_version") != 1
            or document.get("source_commit") != _SOURCE_COMMIT
            or document.get("calibration") != CALIBRATION
        ):
            return {}
        entries = document["architectures"]
        if not isinstance(entries, dict):
            return {}
        accepted = {}
        for name, entry in entries.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            strategy = entry.get("strategy")
            fields = _REQUIRED_PARAMETERS.get(strategy)
            if fields is None or any(field not in entry for field in fields):
                continue
            if not all(
                type(entry.get(field)) is int and entry[field] > 0
                for field in ("channels", "latent_scale", "default_text_tokens")
            ):
                continue
            if not all(
                type(entry[field]) is int and (entry[field] >= 0 if field == "middle_attention" else entry[field] > 0)
                for field in fields
                if field != "include_fixed"
            ):
                continue
            if strategy == "z_image" and type(entry["include_fixed"]) is not bool:
                continue
            if strategy in {"unet_v1", "unet_v2", "unet_xl"}:
                schedule = entry.get("channel_schedule")
                if not isinstance(schedule, list) or len(schedule) < 2 or any(
                    type(channel) is not int or channel <= 0 for channel in schedule
                ) or schedule[0] != entry["channels"]:
                    continue
                if strategy == "unet_xl":
                    maps = (("input_attention", 2), ("output_attention", 3))
                    if any(
                        not isinstance(entry.get(key), dict)
                        or any(
                            not isinstance(ds, str) or not ds.isdecimal()
                            or not isinstance(depths, list) or len(depths) != count
                            or any(type(depth) is not int or depth < 0 for depth in depths)
                            for ds, depths in entry[key].items()
                        )
                        for key, count in maps
                    ):
                        continue
                else:
                    downsamples = entry.get("attention_downsamples")
                    if not isinstance(downsamples, list) or any(
                        type(ds) is not int or ds < 1 for ds in downsamples
                    ) or entry.get("heads_mode") not in ("fixed8", "channel64"):
                        continue
            if type(entry.get("usage_bias")) not in (int, float) or entry["usage_bias"] not in (1, 2.5):
                continue
            accepted[name] = entry
        return accepted
    except (OSError, ValueError, TypeError, KeyError):
        return {}


SUPPORTED = frozenset(_architecture_registry())
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


def _flux1(image: int, tokens: int, embed: bool, architecture: dict) -> tuple[int, int]:
    c = architecture["channels"]
    double, single = architecture["double_layers"], architecture["single_layers"]
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


def _flux2(image: int, tokens: int, embed: bool, architecture: dict) -> tuple[int, int]:
    c = architecture["channels"]
    double, single = architecture["double_layers"], architecture["single_layers"]
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


def _z_image(image: int, tokens: int, c: int, layers: int, include_fixed: bool) -> tuple[int, int]:
    rounded = (image + 31) // 32 * 32
    seq = rounded + tokens
    main = (
        _dense(image, 64, c)
        + 2 * _z_block(rounded, rounded, c)
        + (layers - 1) * _z_block(seq, seq, c)
        + _z_block(seq, image, c)
        + _dense(image, c, 64)
    )
    if not include_fixed:  # Cosmos 2.5 shares the main path without the fixed text encoder.
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


def _qwen21(count: int, tokens: int, steps: int, cfg: int, architecture: dict) -> tuple[int, int]:
    c, layers = architecture["channels"], architecture["layers"]
    rows = cfg * count
    main = (
        _dense(rows, 64, c)
        + layers * (4 * _dense(rows, c, c) + 3 * _dense(rows, c, 12288) + _attention(cfg, 32, 128, count, count + tokens))
        + _dense(rows, c, 64)
    )
    text = cfg * tokens
    fixed = (
        2 * _dense(text, c, c)
        + _dense(cfg * (steps + 1), 256, c)
        + 6 * _dense(cfg * (steps + 1), c, c)
        + (layers - 1) * (4 * _dense(text, c, c) + 3 * _dense(text, c, 12288) + _attention(cfg, 32, 128, tokens, tokens))
        + 2 * _dense(text, c, c)
    )
    return main, fixed


def _qwen(image: int, tokens: int, architecture: dict) -> tuple[int, int]:
    c, layers = architecture["channels"], architecture["layers"]
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


def _krea(image: int, tokens: int, cfg: int, architecture: dict) -> tuple[int, int]:
    c, layers = architecture["channels"], architecture["layers"]
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


def _ideogram(image: int, tokens: int, architecture: dict) -> tuple[int, int]:
    c, layers = architecture["channels"], architecture["layers"]
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


def _convolution(height: int, width: int, output_channels: int, kernel: int, input_channels: int) -> int:
    """Count one single-image convolution using the upstream instruction proxy."""
    return height * width * output_channels * kernel * kernel * input_channels


def _unet_res(height: int, width: int, incoming: int, channels: int, embedding: int) -> int:
    count = _convolution(height, width, channels, 3, incoming)
    count += _dense(1, embedding, channels)
    count += _convolution(height, width, channels, 3, channels)
    if incoming != channels:
        count += _convolution(height, width, channels, 1, incoming)
    return count


def _unet_transformer(height: int, width: int, channels: int, heads: int, tokens: int) -> int:
    """SD1/SD2 transformer: cross-attention K/V projections remain in the main path."""
    hw = height * width
    count = 2 * _convolution(height, width, channels, 1, channels)
    count += 4 * _dense(hw, channels, channels)
    count += _attention(1, heads, channels // heads, hw, hw)
    count += 2 * _dense(hw, channels, channels)
    count += 2 * _dense(tokens, channels, channels)
    count += _attention(1, heads, channels // heads, hw, tokens)
    count += 2 * _dense(hw, channels, 4 * channels)
    count += _dense(hw, 4 * channels, channels)
    return count


def _unet_legacy(height: int, width: int, tokens: int, architecture: dict) -> tuple[int, int]:
    """Port the upstream SD1/SD2 encoder/middle/decoder UNet instruction count."""
    channels = architecture["channel_schedule"]
    repeats = architecture["num_repeat"]
    attention_ds = frozenset(architecture["attention_downsamples"])
    emb = channels[0] * 4

    def heads(c: int) -> int:
        return 8 if architecture["heads_mode"] == "fixed8" else c // 64

    def layer(h: int, w: int, incoming: int, c: int, ds: int) -> int:
        count = _unet_res(h, w, incoming, c, emb)
        if ds in attention_ds:
            count += _unet_transformer(h, w, c, heads(c), tokens)
        return count

    main = _dense(1, channels[0], emb) + _dense(1, emb, emb)
    main += _convolution(height, width, channels[0], 3, 4)
    skips = [(channels[0], height, width)]
    h, w, ds, previous = height, width, 1, channels[0]
    for index, channel in enumerate(channels):
        for _ in range(repeats):
            main += layer(h, w, previous, channel, ds)
            previous = channel
            skips.append((channel, h, w))
        if index < len(channels) - 1:
            h //= 2
            w //= 2
            main += _convolution(h, w, channel, 3, channel)
            skips.append((channel, h, w))
            ds *= 2

    main += _unet_res(h, w, previous, previous, emb)
    main += _unet_transformer(h, w, previous, heads(previous), tokens)
    main += _unet_res(h, w, previous, previous, emb)

    out = previous
    for index in range(len(channels) - 1, -1, -1):
        channel = channels[index]
        h = height // (2 ** index)
        w = width // (2 ** index)
        ds = 2 ** index
        for step in range(repeats + 1):
            skip, _, _ = skips.pop()
            main += layer(h, w, out + skip, channel, ds)
            out = channel
            if index > 0 and step == repeats:
                main += _convolution(h * 2, w * 2, channel, 3, channel)
    main += _convolution(height, width, 4, 3, channels[0])
    return main, 0


def _unet_xl(height: int, width: int, tokens: int, architecture: dict) -> tuple[int, int]:
    """Count SDXL/SSD UNetXL blocks and the separate once-only cross-attention K/V."""
    channels = architecture["channel_schedule"]
    emb = channels[0] * 4
    input_depths = {int(key): value for key, value in architecture["input_attention"].items()}
    output_depths = {int(key): value for key, value in architecture["output_attention"].items()}
    middle_depth = architecture["middle_attention"]
    fixed = 0

    def spatial(h: int, w: int, c: int, depth: int) -> tuple[int, int]:
        if depth <= 0:
            return 0, 0
        hw = h * w
        heads = c // 64
        # Two spatial 1x1 projections around all transformer blocks.
        main_count = 2 * _convolution(h, w, c, 1, c)
        block = 4 * _dense(hw, c, c) + _attention(1, heads, c // heads, hw, hw)
        # Cross q/o; cross k/v are computed once in the fixed path.
        block += 2 * _dense(hw, c, c) + _attention(1, heads, c // heads, hw, tokens)
        block += 2 * _dense(hw, c, 4 * c) + _dense(hw, 4 * c, c)
        return main_count + depth * block, depth * 2 * _dense(tokens, c, c)

    def layer(h: int, w: int, incoming: int, c: int, depth: int) -> tuple[int, int]:
        res = _unet_res(h, w, incoming, c, emb)
        transformer, text_kv = spatial(h, w, c, depth)
        return res + transformer, text_kv

    main = (
        _dense(1, channels[0], emb) + _dense(1, emb, emb)
        + _dense(1, architecture["label_input_channels"], emb) + _dense(1, emb, emb)
        + _convolution(height, width, channels[0], 3, 4)
    )
    skips = [(channels[0], height, width)]
    h, w, ds, previous = height, width, 1, channels[0]
    for index, channel in enumerate(channels):
        depths = input_depths.get(ds, [0, 0])
        for depth in depths:
            block_main, block_fixed = layer(h, w, previous, channel, depth)
            main += block_main
            fixed += block_fixed
            previous = channel
            skips.append((channel, h, w))
        if index < len(channels) - 1:
            h //= 2
            w //= 2
            main += _convolution(h, w, channel, 3, channel)
            skips.append((channel, h, w))
            ds *= 2

    main += _unet_res(h, w, previous, previous, emb)
    if middle_depth:
        block_main, block_fixed = spatial(h, w, previous, middle_depth)
        main += block_main + _unet_res(h, w, previous, previous, emb)
        fixed += block_fixed

    out = previous
    for index in range(len(channels) - 1, -1, -1):
        channel = channels[index]
        h = height // (2 ** index)
        w = width // (2 ** index)
        depths = output_depths.get(2 ** index, [0, 0, 0])
        for step, depth in enumerate(depths):
            skip, _, _ = skips.pop()
            block_main, block_fixed = layer(h, w, out + skip, channel, depth)
            main += block_main
            fixed += block_fixed
            out = channel
            if index > 0 and step == 2:
                main += _convolution(h * 2, w * 2, channel, 3, channel)
    main += _convolution(height, width, 4, 3, channels[0])
    return main, fixed


def _mmdit(image: int, tokens: int, architecture: dict, dual_layers: frozenset[int]) -> tuple[int, int]:
    """Count the pinned SD3/SD3-Large MMDiT joint-attention and fixed paths."""
    c, layers = architecture["channels"], architecture["layers"]
    total_len = tokens + image
    main = image * c * 2 * 2 * 16  # Patchify convolution, 16 input channels.
    fixed = (
        _dense(1, 256, c)
        + 2 * _dense(1, c, c)
        + _dense(1, 2048, c)
        + _dense(tokens, 4096, c)
        + 2 * _dense(1, c, c)
    )
    for index in range(layers):
        dual = index in dual_layers
        context_pre_only = index == layers - 1
        main += 3 * _dense(tokens, c, c) + 3 * _dense(image, c, c)
        main += _attention(1, c // 64, 64, total_len, total_len)
        if dual:
            main += 3 * _dense(image, c, c)
            main += _attention(1, c // 64, 64, image, image)
        if not context_pre_only:
            main += _dense(tokens, c, c) + _dense(tokens, c, 4 * c) + _dense(tokens, 4 * c, c)
        main += _dense(image, c, c) + _dense(image, c, 4 * c) + _dense(image, 4 * c, c)
        if dual:
            main += _dense(image, c, c)
        fixed += (2 if context_pre_only else 6) * _dense(1, c, c)
        fixed += (9 if dual else 6) * _dense(1, c, c)
    main += _dense(image, c, 64)
    return main, fixed


def _pixart(image: int, tokens: int, architecture: dict) -> tuple[int, int]:
    """Count PixArt self/cross attention and one-time caption projections."""
    c, layers = architecture["channels"], architecture["layers"]
    main = image * c * 2 * 2 * 8
    for _ in range(layers):
        main += 6 * _dense(image, c, c)
        main += _attention(1, 16, c // 16, image, image)
        main += _attention(1, 16, c // 16, image, tokens)
        main += _dense(image, c, 4 * c) + _dense(image, 4 * c, c)
    main += _dense(image, c, 32)
    fixed = (
        _dense(1, 256, c) + 7 * _dense(1, c, c)
        + _dense(tokens, 4096, c) + _dense(tokens, c, c)
        + layers * 2 * _dense(tokens, c, c)
    )
    return main, fixed


def _auraflow(image: int, tokens: int, architecture: dict) -> tuple[int, int]:
    """Count AuraFlow joint/single blocks and fixed context modulation."""
    c = architecture["channels"]
    double, single = architecture["double_layers"], architecture["single_layers"]
    context = tokens + 8
    seq = image + context
    intermediate = c * 8 // 3
    main = image * c * 2 * 2 * 4
    attention = _attention(1, c // 256, 256, seq, seq)
    for _ in range(double):
        main += 4 * _dense(context, c, c) + 4 * _dense(image, c, c) + attention
        for rows in (context, image):
            main += 2 * _dense(rows, c, intermediate) + _dense(rows, intermediate, c)
    for index in range(single):
        projected = image if index == single - 1 else seq
        main += 3 * _dense(seq, c, c) + attention + _dense(projected, c, c)
        main += 2 * _dense(projected, c, intermediate) + _dense(projected, intermediate, c)
    main += _dense(image, c, 16)
    fixed = (
        _dense(1, 256, c) + _dense(1, c, c) + _dense(tokens, 4096, c)
        + (double * 12 + single * 6 + 2) * _dense(1, c, c)
    )
    return main, fixed


def _ernie(image: int, tokens: int, architecture: dict, *, include_fixed: bool = True) -> tuple[int, int]:
    """Count ERNIE blocks, also shared with SeedVR-style DiT variants."""
    c, layers = architecture["channels"], architecture["layers"]
    intermediate = architecture["intermediate_size"]
    seq = image + tokens
    main = image * c * 128 + 10 * _dense(1, c, c)
    main += layers * (
        4 * _dense(seq, c, c)
        + 2 * _dense(seq, c, intermediate) + _dense(seq, intermediate, c)
        + _attention(1, c // 128, 128, seq, seq)
    )
    main += _dense(image, c, 128)
    return main, _dense(tokens, 3072, c) if include_fixed else 0


def _flux3(image: int, tokens: int, architecture: dict) -> tuple[int, int]:
    """Count FLUX.3 single-transformer refiners without reference images."""
    c = architecture["channels"]
    double, single = architecture["double_layers"], architecture["single_layers"]
    seq = image + tokens

    def block(length: int, projected: int) -> int:
        return (
            3 * _dense(length, c, c) + _dense(projected, c, c)
            + 2 * _dense(projected, c, 3 * c) + _dense(projected, 3 * c, c)
            + _attention(1, c // 128, 128, length, length)
        )

    main = _dense(image, 128, c) + double * (block(tokens, tokens) + block(image, image))
    main += (single - 1) * block(seq, seq) + block(seq, image)
    main += _dense(image, c, 128)
    fixed = _dense(tokens, 20480, c)
    fixed += _dense(2, 256, c) + _dense(2, c, c)
    fixed += _dense(1, 256, c) + _dense(1, c, c)
    fixed += 4 * _dense(1, c, 3 * c) + _dense(1, c, 2 * c)
    return main, fixed


def _moss_block(length: int, projected: int, c: int, intermediate: int) -> int:
    return (
        3 * _dense(length, c, c) + _dense(projected, c, c)
        + 2 * _dense(projected, c, intermediate) + _dense(projected, intermediate, c)
        + _attention(1, c // 128, 128, length, length)
    )


def _moss(image: int, tokens: int, cfg: int, architecture: dict) -> tuple[int, int]:
    """Count MOSS image blocks plus CFG-packed, fixed text refinement."""
    c, layers = architecture["channels"], architecture["layers"]
    intermediate = architecture["intermediate_size"]
    seq = image + tokens
    main = _dense(image, 128, c)
    main += (layers - 1) * _moss_block(seq, seq, c, intermediate)
    main += _moss_block(seq, image, c, intermediate) + _dense(image, c, 128)
    text = tokens * cfg
    fixed = _dense(text, 4096, c) + 2 * _moss_block(text, text, c, intermediate)
    fixed += _dense(1, 256, c) + 9 * _dense(1, c, c)
    return main, fixed


def _hidream_o1(image: int, tokens: int, architecture: dict) -> tuple[int, int]:
    """Count HiDream O1 image/text transformer branches."""
    c, layers = architecture["channels"], architecture["layers"]
    intermediate = architecture["intermediate_size"]
    dynamic = image + 1

    def block(queries: int, keys: int) -> int:
        return (
            2 * _dense(queries, c, c) + 2 * _dense(queries, c, 1024)
            + _attention(1, 32, 128, queries, keys)
            + 2 * _dense(queries, c, intermediate) + _dense(queries, intermediate, c)
        )

    main = _dense(image, 3072, 1024) + _dense(image, 1024, c)
    main += _dense(1, 256, c) + _dense(1, c, c)
    main += layers * block(dynamic, tokens + dynamic) + _dense(image, c, 3072)
    fixed = layers * block(tokens, tokens)
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
        version = {
            "flux_1": "flux1",
            "flux_2": "flux2",
            "sd_1": "v1",
            "sd_2": "v2",
            "sdxl": "sdxl_base_v0.9",
        }.get(base_model, base_model)
        if version not in SUPPORTED:
            return ComputeUnitEstimate(None, "No supported base architecture is known for this checkpoint.")
        profile = profile_for(model_id, version)
        extras = dict(profile.extras) if profile else {}
        spec = {"version": version, "guidance_embed": "guidance_embed" in extras}
    version = spec.get("version")
    architecture = _architecture_registry().get(version)
    if architecture is None:
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
    # Architecture-specific latent scaling comes from the pinned registry.
    scale = architecture["latent_scale"]
    height, width = height_px // 64 * scale, width_px // 64 * scale
    image = height // 2 * (width // 2)
    padded = spec.get("padded_text_encoding_length")
    if padded is not None and (type(padded) is not int or padded < 0):
        return ComputeUnitEstimate(None, "Invalid published token-length metadata.")
    tokens = padded or architecture["default_text_tokens"]
    strategy = architecture["strategy"]

    if strategy == "mmdit":
        mmdit = spec.get("mmdit")
        raw_dual = mmdit.get("dual_attention_layers", []) if isinstance(mmdit, dict) else []
        if raw_dual is None:
            raw_dual = []
        if not isinstance(raw_dual, list) or any(type(i) is not int or i < 0 or i >= architecture["layers"] for i in raw_dual):
            return ComputeUnitEstimate(None, "Invalid MMDiT dual-attention metadata.")
        main, fixed = _mmdit(image, tokens, architecture, frozenset(raw_dual))
        instruction_units = (main * steps + fixed) * cfg
    elif strategy == "moss":
        main, fixed = _moss(image, tokens, cfg, architecture)
        instruction_units = (main * steps + fixed) * cfg
    elif strategy == "qwen_image_2.1":
        main, fixed = _qwen21(height * width, tokens, steps, cfg, architecture)
        instruction_units = main * steps + fixed
    elif strategy == "krea_2":
        main, fixed = _krea(image, tokens, cfg, architecture)  # includes CFG and once-only text fusion
        instruction_units = main * steps + fixed
    else:
        if strategy == "flux1":
            main, fixed = _flux1(image, tokens, guidance_embed, architecture)
        elif strategy == "flux2":
            main, fixed = _flux2(image, tokens, guidance_embed, architecture)
        elif strategy == "z_image":
            main, fixed = _z_image(
                image, tokens, architecture["channels"], architecture["layers"], architecture["include_fixed"]
            )
        elif strategy == "qwen_image":
            main, fixed = _qwen(image, tokens, architecture)
        elif strategy == "ideogram_4":
            main, fixed = _ideogram(image, tokens, architecture)
        elif strategy in {"unet_v1", "unet_v2"}:
            main, fixed = _unet_legacy(height, width, tokens, architecture)
        elif strategy == "unet_xl":
            main, fixed = _unet_xl(height, width, tokens, architecture)
        elif strategy == "pixart":
            main, fixed = _pixart(image, tokens, architecture)
        elif strategy == "auraflow":
            main, fixed = _auraflow(image, max(256, tokens), architecture)
        elif strategy == "ernie":
            main, fixed = _ernie(image, tokens, architecture)
        elif strategy == "flux3":
            main, fixed = _flux3(image, tokens, architecture)
        elif strategy == "hidream_o1":
            main, fixed = _hidream_o1(height * width, tokens, architecture)
        else:
            return ComputeUnitEstimate(None, "Unsupported model version.")
        instruction_units = (main * steps + fixed) * cfg
    units = math.ceil(instruction_units * CALIBRATION * architecture["usage_bias"])
    if units <= 0:
        return ComputeUnitEstimate(None, "The instruction estimate could not be resolved.")
    if base_estimate:
        if has_loras:
            reason = "Base checkpoint estimate; selected LoRA modifier effects are not included."
        else:
            reason = "Base architecture estimate; exact metadata or checkpoint modifier effects are not included."
    else:
        reason = "Local open-source estimate, not a DT+ server quote or billing proof."
    return ComputeUnitEstimate(units, reason)

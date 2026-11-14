"""Numeric helpers: fused-tensor splitting, Draw Things float16 truncation and scaling.

Moved verbatim from the old single-file converter; do not "improve" the arithmetic, the
output bytes are verified against Draw Things.
"""

from __future__ import annotations

import math

import numpy as np

from dtflow.services.lora_convert.errors import (
    COLAB_HINT,
    DependencyError,
    ShapeMismatchError,
    UnsupportedLoraFormatError,
)

MIN_NUMPY = (1, 21)


def check_numpy() -> None:
    try:
        major, minor = (int(part) for part in np.__version__.split(".")[:2])
    except ValueError:
        return
    if (major, minor) < MIN_NUMPY:
        raise DependencyError(
            f"numpy {np.__version__} is too old, this converter needs numpy >= {MIN_NUMPY[0]}.{MIN_NUMPY[1]} "
            "(pip install --upgrade numpy)."
        )


def _row_bounds(rows: int, count: int, offsets: tuple[int, ...] | None) -> list[tuple[int, int]]:
    starts = list(offsets) if offsets is not None else [i * (rows // count) for i in range(count)]
    ends = [*starts[1:], rows]
    return list(zip(starts, ends, strict=True))


def is_diagonal_up(up: np.ndarray, count: int, offsets: tuple[int, ...] | None = None) -> bool:
    """True when ``up`` is block diagonal over ``count`` parts (off-diagonal blocks all zero bytes)."""
    if up.ndim != 2 or up.shape[1] % count != 0:
        return False
    cols = up.shape[1] // count
    bits = up.view(np.uint16)
    for i, (start, end) in enumerate(_row_bounds(up.shape[0], count, offsets)):
        for j in range(count):
            if i != j and bits[start:end, j * cols : (j + 1) * cols].any():
                return False
    return True


def split_fused(
    up: np.ndarray, down: np.ndarray, count: int, offsets: tuple[int, ...] | None = None
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split one fused LoRA pair into ``count`` pairs, as the Swift ``ModelWeightElement.write`` does."""
    if count == 1:
        return [(up, down)]
    if offsets is None and up.shape[0] % count != 0:
        raise ShapeMismatchError(f"Cannot split {up.shape[0]} output rows into {count} equal parts.", hint=COLAB_HINT)
    bounds = _row_bounds(up.shape[0], count, offsets)
    if is_diagonal_up(up, count, offsets):
        cols = up.shape[1] // count
        if down.shape[0] % count != 0:
            raise ShapeMismatchError(f"Cannot split {down.shape[0]} rank rows into {count} parts.", hint=COLAB_HINT)
        down_rows = down.shape[0] // count
        return [
            (
                up[start:end, i * cols : (i + 1) * cols],
                down[i * down_rows : (i + 1) * down_rows],
            )
            for i, (start, end) in enumerate(bounds)
        ]
    return [(up[start:end], down) for start, end in bounds]


def split_fused_inputs(
    up: np.ndarray, down: np.ndarray, offsets: tuple[int, ...]
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split a LoRA over a layer that fuses several INPUTS (Swift ``ModelWeightElement`` with format ``.I``).

    ``up`` goes unchanged to every target, ``down`` is cut by columns at ``offsets``.
    """
    if down.shape[1] <= offsets[-1]:
        raise ShapeMismatchError(f"Cannot split {down.shape[1]} input columns at {list(offsets)}.", hint=COLAB_HINT)
    count = len(offsets)
    # Swift treats a block diagonal ``down`` specially (it then slices the rank rows too); that needs
    # a redefine step that is not ported, so refuse it instead of writing something different.
    rank_rows = down.shape[0] // count
    bits = down.view(np.uint16)
    if down.shape[0] % count == 0 and down.shape[1] % count == 0:
        bounds = [*offsets[1:], down.shape[1]]
        off_diagonal = False
        for i in range(count):
            for j in range(count):
                if i != j and bits[i * rank_rows : (i + 1) * rank_rows, offsets[j] : bounds[j]].any():
                    off_diagonal = True
        if not off_diagonal:
            raise UnsupportedLoraFormatError(
                "A block diagonal down tensor over fused inputs is not supported.", hint=COLAB_HINT
            )
    ends = [*offsets[1:], down.shape[1]]
    return [(up, down[:, begin:end]) for begin, end in zip(offsets, ends, strict=True)]


# --------------------------------------------------------------------------- float16


def f32_to_f16_bits(values: np.ndarray) -> np.ndarray:
    """float32 to float16 bit patterns (uint16), truncating toward zero, integer arithmetic only.

    This is the conversion Draw Things (ccv) does: no rounding, so tiny values (the float16
    subnormal range, below about 6e-5) lose their last bit instead of rounding up. Doing it with
    explicit bit operations keeps the result independent of the numpy version and the CPU.
    Infinity stays infinity, NaN becomes a quiet NaN, finite values above 65504 saturate to 65504.
    """
    bits = np.ascontiguousarray(values, dtype="<f4").view("<u4").astype(np.uint32)
    sign = ((bits >> np.uint32(16)) & np.uint32(0x8000)).astype(np.uint32)
    exponent = ((bits >> np.uint32(23)) & np.uint32(0xFF)).astype(np.int32)
    mantissa = (bits & np.uint32(0x7FFFFF)).astype(np.uint32)
    e = exponent - 112  # rebias from 127 to 15
    normal = (e << 10).astype(np.uint32) | (mantissa >> np.uint32(13))
    shift = np.clip(14 - e, 0, 31).astype(np.uint32)
    subnormal = np.where((e <= 0) & (e > -10), (mantissa | np.uint32(0x800000)) >> shift, np.uint32(0)).astype(
        np.uint32
    )
    magnitude = np.where(e >= 1, normal, subnormal).astype(np.uint32)
    magnitude = np.where(e >= 31, np.uint32(0x7BFF), magnitude)
    special = exponent == 255
    magnitude = np.where(special, np.where(mantissa != 0, np.uint32(0x7E00), np.uint32(0x7C00)), magnitude)
    return (sign | magnitude).astype("<u2")


def to_f16(values: np.ndarray) -> np.ndarray:
    """float32 array to a float16 array using the Draw Things truncating conversion."""
    return f32_to_f16_bits(values).view("<f2")


# --------------------------------------------------------------------------- scaling


def scalar(array: np.ndarray) -> np.float32:
    """Read one LoRA alpha value, rejecting malformed alpha tensors."""
    values = np.asarray(array, dtype=np.float32).reshape(-1)
    if values.size != 1:
        raise UnsupportedLoraFormatError(
            f"LoRA alpha must contain exactly one value; found {values.size}.",
            alpha_value_count=int(values.size),
        )
    return np.float32(values[0])


def scale_up(tensor_f16: np.ndarray, alpha: np.float32 | None, scale: float, rank: int) -> np.ndarray:
    """Same arithmetic as the Swift importer for ``lora_up`` tensors."""
    rank = np.float32(rank)
    if alpha is not None and abs(float(alpha) - float(rank)) > 1e-5:
        factor = np.float32(float(alpha / rank) * math.sqrt(scale))
    elif scale != 1:
        factor = np.float32(math.sqrt(scale))
    else:
        return tensor_f16
    return to_f16(tensor_f16.astype(np.float32) * factor)


def scale_down(tensor_f16: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1:
        return tensor_f16
    return to_f16(tensor_f16.astype(np.float32) * np.float32(math.sqrt(scale)))

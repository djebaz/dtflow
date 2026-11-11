"""Convert a safetensors LoRA to a Draw Things float16 LoRA ``.ckpt``, pure Python.

Runs on native Windows: needs only ``numpy`` and the standard library (the
safetensors container is parsed here, no ``torch``).

It is a hand port of the relevant part of Draw Things' ``ModelOp.LoRAImporter``
(pinned revision db37e65, the same one as the Colab notebook). Only the families
listed in ``FAMILIES`` are supported. Anything else, and any key the converter
does not understand, stops with an error that points to the Colab notebook
(``tools/colab/dtflow_lora_minimal_convert.ipynb``), because the Swift importer
silently ignores unknown keys and a half converted LoRA is worse than none.

Usage:
    python lora_to_ckpt.py LORA.safetensors OUT.ckpt --family krea2 [--scale 1.0]

Check the result against a Swift made file with ``inspect_ckpt.py A.ckpt B.ckpt``
(the goal is 0 differing tensor data hashes).
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import sqlite3
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MIN_NUMPY = (1, 21)


COLAB_HINT = (
    "Use the Colab notebook instead: tools/colab/dtflow_lora_minimal_convert.ipynb "
    "(it supports every Draw Things family)."
)

# ccv flags as stored in the tensors table (see inspect_ckpt.py for the datatype list).
DATATYPE_F16 = 0x20000
FORMAT_NHWC = 0x01  # what Draw Things writes for these 2-D weights (checked on a Krea 2 ckpt)
TYPE_CPU = 0x01
MAX_DIM_ALLOC = 12

_SAFETENSORS_DTYPES = {"F16": "<f2", "F32": "<f4"}


class ConversionError(Exception):
    """Raised for anything the converter refuses to do."""


def _check_numpy() -> None:
    try:
        major, minor = (int(part) for part in np.__version__.split(".")[:2])
    except ValueError:
        return
    if (major, minor) < MIN_NUMPY:
        raise ConversionError(
            f"numpy {np.__version__} is too old, this converter needs numpy >= {MIN_NUMPY[0]}.{MIN_NUMPY[1]} "
            "(pip install --upgrade numpy)."
        )


# --------------------------------------------------------------------------- reading


class SafeTensors:
    """Minimal read-only safetensors reader.

    Use it as a context manager: the file is closed on exit, because on Windows an open
    file cannot be deleted or moved. Tensors are read one at a time (no memory mapping).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None
        try:
            size = path.stat().st_size
            if size < 8:
                raise ConversionError(f"{path.name}: not a safetensors file (too small).")
            with path.open("rb") as handle:
                (header_len,) = struct.unpack("<Q", handle.read(8))
                if header_len > 100 * 1024 * 1024 or 8 + header_len > size:
                    raise ConversionError(f"{path.name}: not a safetensors file (bad header size).")
                header = json.loads(handle.read(header_len))
            if not isinstance(header, dict):
                raise ConversionError(f"{path.name}: not a safetensors file (bad header).")
            self._base = 8 + header_len
            self.entries = {k: v for k, v in header.items() if k != "__metadata__"}
            for key, entry in self.entries.items():
                begin, end = entry["data_offsets"]
                if not 0 <= begin <= end <= size - self._base:
                    raise ConversionError(f"{path.name}: {key} points outside the file (truncated?).")
        except (OSError, struct.error, ValueError, KeyError, TypeError) as error:
            raise ConversionError(f"{path.name}: cannot read this safetensors file ({error}).") from error
        self._file = path.open("rb")

    def close(self) -> None:
        handle, self._file = self._file, None
        if handle is not None:
            handle.close()

    def __enter__(self) -> SafeTensors:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def keys(self) -> list[str]:
        return list(self.entries)

    def shape(self, key: str) -> tuple[int, ...]:
        return tuple(self.entries[key]["shape"])

    def _raw(self, key: str) -> tuple[str, tuple[int, ...], bytes]:
        entry = self.entries[key]
        begin, end = entry["data_offsets"]
        dtype = entry["dtype"]
        if dtype != "BF16" and dtype not in _SAFETENSORS_DTYPES:
            raise ConversionError(f"{key}: unsupported dtype {dtype}. {COLAB_HINT}")
        if self._file is None:
            raise ConversionError(f"{self.path.name}: the file was already closed.")
        shape = tuple(entry["shape"])
        width = {"F16": 2, "BF16": 2, "F32": 4}[dtype]
        if (end - begin) != width * math.prod(shape):
            raise ConversionError(f"{key}: data size does not match shape {shape} (corrupt file?).")
        self._file.seek(self._base + begin)
        raw = self._file.read(end - begin)
        if len(raw) != end - begin:
            raise ConversionError(f"{key}: the file ended early (truncated?).")
        return dtype, shape, raw

    def read(self, key: str) -> np.ndarray:
        """The tensor as float32 (exact for F16 and BF16)."""
        dtype, shape, raw = self._raw(key)
        if dtype == "BF16":
            bits = np.frombuffer(raw, dtype="<u2").astype("<u4") << np.uint32(16)
            return bits.astype("<u4").view("<f4").reshape(shape)
        return np.frombuffer(raw, dtype=_SAFETENSORS_DTYPES[dtype]).astype("<f4").reshape(shape)

    def read_f16(self, key: str) -> np.ndarray:
        """The tensor as float16 the way Draw Things converts it (F16 is kept as it is)."""
        dtype, shape, raw = self._raw(key)
        if dtype == "F16":
            return np.frombuffer(raw, dtype="<f2").reshape(shape)
        return to_f16(self.read(key))


# --------------------------------------------------------------------------- key handling


@dataclass(frozen=True)
class LoraKey:
    """One source tensor: which module it belongs to and what it is."""

    module: str  # module path with every '.' turned into '_', prefixes stripped
    role: str  # "up", "down" or "alpha"
    key: str  # original key in the file


_ROLE_SUFFIXES: tuple[tuple[str, str], ...] = (
    (".lora_B.default.weight", "up"),
    (".lora_A.default.weight", "down"),
    (".lora_B.weight", "up"),
    (".lora_A.weight", "down"),
    (".lora.up.weight", "up"),
    (".lora.down.weight", "down"),
    (".lora_up.weight", "up"),
    (".lora_down.weight", "down"),
    ("::lora::0", "up"),
    ("::lora::1", "down"),
    (".alpha", "alpha"),
    ("::alpha", "alpha"),
)

_PREFIXES = (
    "base_model.model.",
    "base_model.",
    "diffusion_model.",
    "transformer.",
    "lora_unet__",
    "lora_unet_",
    "lora_transformer_",
)


def split_key(key: str) -> LoraKey | None:
    """Split a source key into (module, role). Returns None for keys that are not LoRA parts."""
    match = next(((s, r) for s, r in _ROLE_SUFFIXES if key.endswith(s)), None)
    if match is None:
        return None
    suffix, role = match
    module = key[: -len(suffix)]
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if module.startswith(prefix):
                module = module[len(prefix) :]
                changed = True
    return LoraKey(module.replace(".", "_"), role, key)


# --------------------------------------------------------------------------- families

# A resolver maps a normalized module path to the Draw Things layer ("dit" prefix) it
# targets, or None when the module is not recognised.
Resolver = Callable[[str], "Target | None"]


@dataclass(frozen=True)
class Target:
    prefix: str  # "dit"
    names: tuple[str, ...]  # NNC parameter names, e.g. ("t-to_q-0-0",)
    # Column offsets in the (fused) ``down`` tensor when the source layer fuses several INPUTS
    # (Flux 1 single block linear2). Every target then gets the whole ``up`` and its slice of ``down``.
    input_offsets: tuple[int, ...] | None = None
    # Row offsets in the fused ``up`` tensor when the parts differ in size (Flux 1 single block linear1);
    # None means equal parts.
    output_offsets: tuple[int, ...] | None = None


_KREA2_ATTN = {
    "to_q": "to_q",
    "wq": "to_q",
    "to_k": "to_k",
    "wk": "to_k",
    "to_v": "to_v",
    "wv": "to_v",
    "to_gate": "to_gate",
    "gate": "to_gate",
    "to_out_0": "to_out",
    "to_out": "to_out",
    "wo": "to_out",
}
_KREA2_FF = ("gate", "up", "down")

_KREA2_BLOCK = re.compile(r"^(?:transformer_)?blocks_(\d+)_(attn|ff|mlp)_(\w+)$")
_KREA2_FUSION = re.compile(r"^(?:text_fusion|txtfusion)_(layerwise|refiner)_blocks_(\d+)_(attn|ff|mlp)_(\w+)$")


def _krea2_leaf(kind: str, leaf: str) -> str | None:
    if kind == "attn":
        return _KREA2_ATTN.get(leaf)
    return leaf if leaf in _KREA2_FF else None


def resolve_krea2(module: str) -> Target | None:
    """Krea 2 DiT blocks (28) and the text fusion adapter (2 layerwise + 2 refiner blocks).

    Parameter names follow the Swift model: ``t-<layer name>-<block index>-0`` where the
    layer name carries the group prefix (``text_fusion_layerwise_attn_to_q`` and so on).
    The time embedding, text projection and image in/out layers live in the "fixed"
    model and are not handled here.
    """
    match = _KREA2_BLOCK.match(module)
    if match:
        index, kind, leaf = int(match.group(1)), match.group(2), match.group(3)
        name = _krea2_leaf(kind, leaf)
        if name is None or index >= 28:
            return None
        return Target("dit", (f"t-{name}-{index}-0",))
    match = _KREA2_FUSION.match(module)
    if match:
        group, index = match.group(1), int(match.group(2))
        kind, leaf = match.group(3), match.group(4)
        name = _krea2_leaf(kind, leaf)
        if name is None or index >= 2:
            return None
        sub = "attn" if kind == "attn" else "ff"
        return Target("dit", (f"t-text_fusion_{group}_{sub}_{name}-{index}-0",))
    return None


_ZIMAGE_BLOCK = re.compile(r"^(layers|noise_refiner|context_refiner)_(\d+)_(\w+)$")
_ZIMAGE_COUNTS = {"layers": 30, "noise_refiner": 2, "context_refiner": 2}
_ZIMAGE_LEAVES = {
    "attention_to_q": "q",
    "attention_to_k": "k",
    "attention_to_v": "v",
    "attention_to_out_0": "o",
    "attention_to_out": "o",
    "attention_out": "o",
    "feed_forward_w1": "ffn_gate_proj",
    "feed_forward_w3": "ffn_up_proj",
    "feed_forward_w2": "ffn_down_proj",
}
_ZIMAGE_FFN = {"ffn_gate_proj", "ffn_up_proj", "ffn_down_proj"}


def resolve_zimage(module: str) -> Target | None:
    """Z Image (Turbo): 30 main layers plus 2 noise refiner and 2 context refiner blocks.

    Names follow the Swift model: ``t-<group>_<layer>-<block index>-0`` with an empty group
    for the main layers; the three feed forward layers live in a named sub-model
    (``t-ffn-<i>-ffn_down_proj-0-0``). Main layers match the Swift output; the refiner blocks
    follow the same rule but were not part of the reference LoRA. Fused sources (``attention.qkv`` and ``adaLN_modulation.0``) map
    to several targets and are split by ``split_fused``.
    """
    match = _ZIMAGE_BLOCK.match(module)
    if match is None:
        return None
    group, index, leaf = match.group(1), int(match.group(2)), match.group(3)
    if index >= _ZIMAGE_COUNTS[group]:
        return None
    prefix = "" if group == "layers" else f"{group}_"

    def name(layer: str) -> str:
        if layer in _ZIMAGE_FFN:
            # The feed forward is its own named sub-model: t-<ffn model>-<block>-<layer>-0-0
            # (checked against the Swift output for the 30 main layers).
            return f"t-{prefix}ffn-{index}-{prefix}{layer}-0-0"
        return f"t-{prefix}{layer}-{index}-0"

    if leaf in _ZIMAGE_LEAVES:
        return Target("dit", (name(_ZIMAGE_LEAVES[leaf]),))
    if leaf == "attention_qkv":
        return Target("dit", (name("q"), name("k"), name("v")))
    if leaf == "adaLN_modulation_0" and group != "context_refiner":
        return Target("dit", tuple(name(f"ada_ln_{k}") for k in range(4)))
    return None


# Flux 1 (dev/schnell): 19 double blocks then 38 single blocks, hidden size 3072. Draw Things numbers the
# blocks in one sequence, so single block j is block 19 + j. Names follow Flux1.swift / Flux1Fixed:
# ``t-<layer>-<block>-0`` with c_* (text stream) and x_* (image stream) layers.
FLUX1_DOUBLE = 19
FLUX1_SINGLE = 38
FLUX1_HIDDEN = 3072

_FLUX1_DOUBLE = re.compile(r"^(?:double_blocks|transformer_blocks)_(\d+)_(\w+)$")
_FLUX1_SINGLE = re.compile(r"^(?:single_blocks|single_transformer_blocks)_(\d+)_(\w+)$")
# leaf -> (stream, layer names) for the double blocks; original (BFL) names first, then diffusers.
_FLUX1_DOUBLE_LEAVES: dict[str, tuple[str, ...]] = {
    "img_attn_qkv": ("t-x_q", "t-x_k", "t-x_v"),
    "txt_attn_qkv": ("t-c_q", "t-c_k", "t-c_v"),
    "img_attn_proj": ("t-x_o",),
    "txt_attn_proj": ("t-c_o",),
    "img_mlp_0": ("t-x_linear1",),
    "img_mlp_2": ("t-x_out_proj",),
    "txt_mlp_0": ("t-c_linear1",),
    "txt_mlp_2": ("t-c_out_proj",),
    "img_mod_lin": tuple(f"t-x_ada_ln_{i}" for i in range(6)),
    "txt_mod_lin": tuple(f"t-context_ada_ln_{i}" for i in range(6)),
    "attn_to_q": ("t-x_q",),
    "attn_to_k": ("t-x_k",),
    "attn_to_v": ("t-x_v",),
    "attn_add_q_proj": ("t-c_q",),
    "attn_add_k_proj": ("t-c_k",),
    "attn_add_v_proj": ("t-c_v",),
    "attn_to_out_0": ("t-x_o",),
    "attn_to_add_out": ("t-c_o",),
    "ff_net_0_proj": ("t-x_linear1",),
    "ff_net_2": ("t-x_out_proj",),
    "ff_context_net_0_proj": ("t-c_linear1",),
    "ff_context_net_2": ("t-c_out_proj",),
    "norm1_linear": tuple(f"t-x_ada_ln_{i}" for i in range(6)),
    "norm1_context_linear": tuple(f"t-context_ada_ln_{i}" for i in range(6)),
}
_FLUX1_SINGLE_LEAVES: dict[str, tuple[str, ...]] = {
    "linear1": ("t-x_q", "t-x_k", "t-x_v", "t-x_linear1"),
    "modulation_lin": tuple(f"t-x_ada_ln_{i}" for i in range(3)),
    "attn_to_q": ("t-x_q",),
    "attn_to_k": ("t-x_k",),
    "attn_to_v": ("t-x_v",),
    "proj_mlp": ("t-x_linear1",),
    "norm_linear": tuple(f"t-x_ada_ln_{i}" for i in range(3)),
}


def resolve_flux1(module: str) -> Target | None:
    """Flux 1 blocks, original (``double_blocks.N.img_attn.qkv``) and diffusers (``transformer_blocks.N.attn.to_q``) names."""
    match = _FLUX1_DOUBLE.match(module)
    if match:
        index, leaf = int(match.group(1)), match.group(2)
        names = _FLUX1_DOUBLE_LEAVES.get(leaf)
        if names is None or index >= FLUX1_DOUBLE:
            return None
        return Target("dit", tuple(f"{name}-{index}-0" for name in names))
    match = _FLUX1_SINGLE.match(module)
    if match:
        index, leaf = int(match.group(1)), match.group(2)
        if index >= FLUX1_SINGLE:
            return None
        block = FLUX1_DOUBLE + index
        if leaf in ("linear2", "proj_out"):
            # linear2 fuses two inputs: attention output (3072 columns) and the MLP output (12288).
            return Target(
                "dit",
                (f"t-x_o-{block}-0", f"t-x_out_proj-{block}-0"),
                input_offsets=(0, FLUX1_HIDDEN),
            )
        names = _FLUX1_SINGLE_LEAVES.get(leaf)
        if names is None:
            return None
        offsets = (0, FLUX1_HIDDEN, 2 * FLUX1_HIDDEN, 3 * FLUX1_HIDDEN) if leaf == "linear1" else None
        return Target("dit", tuple(f"{name}-{block}-0" for name in names), output_offsets=offsets)
    return None


FAMILIES: dict[str, Resolver] = {
    "krea2": resolve_krea2,
    "zimage": resolve_zimage,
    "flux1": resolve_flux1,
}


# --------------------------------------------------------------------------- fused targets


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
        raise ConversionError(f"Cannot split {up.shape[0]} output rows into {count} equal parts. {COLAB_HINT}")
    bounds = _row_bounds(up.shape[0], count, offsets)
    if is_diagonal_up(up, count, offsets):
        cols = up.shape[1] // count
        if down.shape[0] % count != 0:
            raise ConversionError(f"Cannot split {down.shape[0]} rank rows into {count} parts. {COLAB_HINT}")
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
        raise ConversionError(f"Cannot split {down.shape[1]} input columns at {list(offsets)}. {COLAB_HINT}")
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
            raise ConversionError(f"A block diagonal down tensor over fused inputs is not supported. {COLAB_HINT}")
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


def _scalar(array: np.ndarray) -> np.float32:
    return np.float32(np.asarray(array, dtype=np.float32).reshape(-1)[0])


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


# --------------------------------------------------------------------------- writing


def _encode_dims(shape: tuple[int, ...]) -> bytes:
    dims = list(shape) + [0] * (MAX_DIM_ALLOC - len(shape))
    return struct.pack(f"<{MAX_DIM_ALLOC}i", *dims)


def write_ckpt(path: Path, tensors: dict[str, np.ndarray]) -> None:
    if path.exists():
        raise ConversionError(f"{path} already exists; refusing to overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.part")
    tmp.unlink(missing_ok=True)
    done = False
    try:
        # The connection must be closed before the file is renamed or deleted (Windows locks it).
        con = sqlite3.connect(str(tmp))
        try:
            con.execute(
                "CREATE TABLE tensors (name TEXT, type INTEGER, format INTEGER, datatype INTEGER, "
                "dim BLOB, data BLOB, PRIMARY KEY (name))"
            )
            rows = (
                (
                    name,
                    TYPE_CPU,
                    FORMAT_NHWC if array.ndim <= 2 else FORMAT_NCHW,
                    DATATYPE_F16,
                    _encode_dims(tuple(array.shape)),
                    np.ascontiguousarray(array, dtype="<f2").tobytes(),
                )
                for name, array in tensors.items()
            )
            con.executemany("INSERT INTO tensors VALUES (?, ?, ?, ?, ?, ?)", rows)
            con.commit()
        finally:
            con.close()
        if path.exists():  # appeared while we were writing: never overwrite
            raise ConversionError(f"{path} already exists; refusing to overwrite.")
        tmp.rename(path)  # fails on Windows if the target exists; on POSIX it was checked above
        done = True
    except OSError as error:
        raise ConversionError(f"Cannot write {path}: {error}") from error
    except sqlite3.Error as error:
        raise ConversionError(f"Cannot write {path}: {error}") from error
    finally:
        if not done:
            tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- Swift style tables
#
# SD 1.x and SDXL map to Draw Things layers through big literal tables (``lora_mappings.json``,
# extracted from ``ModelMapping.swift`` by ``extract_swift_mappings.py``). The lookup below is a port
# of the Swift importer's loops, including its quirks, because the key names found in the wild
# (kohya ``lora_unet_...``, diffusers, PEFT) all go through the same fuzzy matching.

TABLE_FAMILIES = ("sd1", "sdxl")
EXPERIMENTAL_FAMILIES = ("sdxl",)
FORMAT_NCHW = 0x02  # assumed for 4-D (convolution) tensors, not checked against a reference


@functools.cache
def _mappings() -> dict:
    return json.loads(Path(__file__).with_name("lora_mappings.json").read_text(encoding="utf-8"))


def _sdxl_partials() -> list[tuple[str, str]]:
    """Original UNet names to diffusers names for the SDXL layout (3 levels, no attention on the first)."""
    pairs: list[tuple[str, str]] = []
    for block, (level, index) in {1: (0, 0), 2: (0, 1), 4: (1, 0), 5: (1, 1), 7: (2, 0), 8: (2, 1)}.items():
        pairs.append((f"input_blocks.{block}.0.", f"down_blocks.{level}.resnets.{index}."))
        if level > 0:
            pairs.append((f"input_blocks.{block}.1.", f"down_blocks.{level}.attentions.{index}."))
    for block, level in {3: 0, 6: 1}.items():
        pairs.append((f"input_blocks.{block}.0.op.", f"down_blocks.{level}.downsamplers.0.conv."))
    pairs += [
        ("middle_block.0.", "mid_block.resnets.0."),
        ("middle_block.1.", "mid_block.attentions.0."),
        ("middle_block.2.", "mid_block.resnets.1."),
    ]
    for block in range(9):
        level, index = divmod(block, 3)
        pairs.append((f"output_blocks.{block}.0.", f"up_blocks.{level}.resnets.{index}."))
        if level < 2:
            pairs.append((f"output_blocks.{block}.1.", f"up_blocks.{level}.attentions.{index}."))
    pairs += [
        ("output_blocks.2.2.", "up_blocks.0.upsamplers.0."),
        ("output_blocks.5.2.", "up_blocks.1.upsamplers.0."),
        ("output_blocks.8.1.", "up_blocks.2.upsamplers.0."),
    ]
    # Anchored and last: a bare "out.0." would also match every "to_out.0." attention key.
    pairs += [
        ("diffusion_model.time_embed.0.", "diffusion_model.time_embedding.linear_1."),
        ("diffusion_model.time_embed.2.", "diffusion_model.time_embedding.linear_2."),
        ("diffusion_model.label_emb.0.0.", "diffusion_model.add_embedding.linear_1."),
        ("diffusion_model.label_emb.0.2.", "diffusion_model.add_embedding.linear_2."),
        ("diffusion_model.input_blocks.0.0.", "diffusion_model.conv_in."),
        ("diffusion_model.out.0.", "diffusion_model.conv_norm_out."),
        ("diffusion_model.out.2.", "diffusion_model.conv_out."),
    ]
    return pairs


def _with_diffusers_names(
    table: dict[str, list[str]], partials: list[tuple[str, str]], resnets: list[tuple[str, str]]
) -> dict[str, tuple[str, ...]]:
    out = {key: tuple(names) for key, names in table.items()}
    for key, names in table.items():
        diffusers = key
        for old, new in partials:
            if old in key:
                diffusers = key.replace(old, new)
                break
        if ".resnets." in diffusers:
            for old, new in resnets:
                if old in diffusers:
                    diffusers = diffusers.replace(old, new)
                    break
        if diffusers != key:
            out[diffusers] = tuple(names)
    return out


def _expand_unet(mapping: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    out = dict(mapping)
    for key, value in mapping.items():
        parts = key.split(".")
        if parts[0] == "model":
            if len(parts) > 3:
                out["_".join(parts[2:-1]) + "." + parts[-1]] = value
            if len(parts) > 2:
                out["_".join(parts[1:-1]) + "." + parts[-1]] = value
        elif parts[0] == "diffusion_model":
            if len(parts) > 2:
                out["_".join(parts[1:-1]) + "." + parts[-1]] = value
        elif len(parts) > 1:
            out["_".join(parts[:-1]) + "." + parts[-1]] = value
    return out


def _clip_text(layers: int) -> dict[str, tuple[str, ...]]:
    """HF style CLIP text encoder table (``CLIPTextModel`` and ``OpenCLIPTextModelGTransformers``)."""
    prefix = "cond_stage_model.transformer.text_model"
    out: dict[str, tuple[str, ...]] = {
        f"{prefix}.embeddings.position_embedding.weight": ("t-0-0",),
        f"{prefix}.embeddings.token_embedding.weight": ("t-1-0",),
    }
    leaves = (
        ("layer_norm1", 2),
        ("self_attn.q_proj", 3),
        ("self_attn.k_proj", 4),
        ("self_attn.v_proj", 5),
        ("self_attn.out_proj", 6),
        ("layer_norm2", 7),
        ("mlp.fc1", 8),
        ("mlp.fc2", 9),
    )
    for i in range(layers):
        for leaf, offset in leaves:
            out[f"{prefix}.encoder.layers.{i}.{leaf}.weight"] = (f"t-{i * 8 + offset}-0",)
            out[f"{prefix}.encoder.layers.{i}.{leaf}.bias"] = (f"t-{i * 8 + offset}-1",)
    final = layers * 8 + 2
    out[f"{prefix}.final_layer_norm.weight"] = (f"t-{final}-0",)
    out[f"{prefix}.final_layer_norm.bias"] = (f"t-{final}-1",)
    return out


def _expand_text(mapping: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    out = dict(mapping)
    for key, value in mapping.items():
        parts = key.split(".")
        for i in range(3):
            if len(parts) - 1 <= i:
                break
            out["_".join(parts[i:-1]) + "." + parts[-1]] = value
    return out


def _normalize_state(keys: list[str], te1_tag: str = "te") -> dict[str, str]:
    """The Swift importer's key fix-ups: returns normalized key -> real key in the file."""
    state = {k: k for k in keys}

    def copy_alpha(key: str, suffix: str, alpha: str, new_key: str) -> None:
        if key.endswith(suffix) and key[: -len(suffix)] + alpha in state:
            state[new_key + ".alpha"] = state[key[: -len(suffix)] + alpha]

    for key in keys:  # lora_A / lora_B (PEFT, LyCORIS)
        if not (key.endswith(".lora_A.weight") or key.endswith(".lora_B.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 3:
            continue
        if comps[1] == "base_model":
            del comps[1]
        if comps[0] == "base_model":
            del comps[0]
        new_key = "_".join(comps[:-2])
        if key.endswith(".lora_B.weight"):
            copy_alpha(key, ".lora_B.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:  # diffusers ".lora.up.weight"
        if not (key.endswith(".lora.up.weight") or key.endswith(".lora.down.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 4:
            continue
        if comps[1] == "base_model":
            del comps[1]
        if comps[0] == "base_model":
            del comps[0]
        new_key = "_".join(comps[:-3])
        if key.endswith(".lora.up.weight"):
            copy_alpha(key, ".lora.up.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:  # OneTrainer
        if not (key.endswith(".lora_up.weight") or key.endswith(".lora_down.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 4:
            continue
        if comps[0] == "transformer":
            del comps[0]
        new_key = "_".join(comps[:-2])
        if key.endswith(".lora_up.weight"):
            copy_alpha(key, ".lora_up.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:  # ".lora_A.default.weight"
        if not (key.endswith(".lora_A.default.weight") or key.endswith(".lora_B.default.weight")):
            continue
        comps = key.split(".")
        if len(comps) < 3:
            continue
        if comps[1] == "base_model":
            del comps[1]
        if comps[0] == "base_model":
            del comps[0]
        new_key = "_".join(comps[:-3])
        if key.endswith(".lora_B.default.weight"):
            copy_alpha(key, ".lora_B.default.weight", ".alpha", new_key)
            state[new_key + ".lora_up.weight"] = key
        else:
            state[new_key + ".lora_down.weight"] = key
    for key in keys:
        if key.startswith("lora_unet__"):
            state[key[11:]] = key
    # Diffusers/PEFT text encoder names -> the kohya spelling the tables are keyed on:
    # text_encoder_2_* is the second encoder (te2), text_encoder_* the first (te / te1).
    for key in list(state):
        for prefix, tag in (("text_encoder_2_", "te2"), ("text_encoder_", te1_tag)):
            if key.startswith(prefix):
                state[f"lora_{tag}_{key[len(prefix) :]}"] = state.pop(key)
                break
    return state


def _derive_keys(parts: list[str]) -> list[str]:
    out = []
    for index in range(3):
        new_parts = "_".join(parts[index:]).split(".")
        if len(new_parts) <= 1:
            continue
        end = len(new_parts) - 2 if len(new_parts) > 2 else len(new_parts) - 1
        out.append(".".join(new_parts[:end]) + ".weight")
    return out


def _find(mapping: dict[str, tuple[str, ...]], keys: list[str]) -> tuple[str, tuple[str, ...]] | None:
    for key in keys:
        if key in mapping:
            return key, mapping[key]
        parts = key.split(".")
        if parts[0] == "model":
            if len(parts) > 3 and parts[1] == "diffusion_model":
                stripped = ".".join(parts[2:])
                if stripped in mapping:
                    return stripped, mapping[stripped]
            if len(parts) > 2:
                stripped = ".".join(parts[1:])
                if stripped in mapping:
                    return stripped, mapping[stripped]
        elif parts[0] == "diffusion_model" and len(parts) > 2:
            stripped = ".".join(parts[1:])
            if stripped in mapping:
                return stripped, mapping[stripped]
    return None


def _split_up(up: np.ndarray, count: int) -> tuple[list[np.ndarray], bool]:
    """Rows of a fused ``up`` tensor per target (the Swift ``write`` for ``.O`` elements)."""
    if count == 1:
        return [up], False
    if up.ndim != 2:
        raise ConversionError(f"Fused 4-D LoRA tensors are not supported. {COLAB_HINT}")
    if up.shape[0] % count != 0:
        raise ConversionError(f"Cannot split {up.shape[0]} rows into {count} parts. {COLAB_HINT}")
    rows = up.shape[0] // count
    if is_diagonal_up(up, count):
        cols = up.shape[1] // count
        return [up[i * rows : (i + 1) * rows, i * cols : (i + 1) * cols] for i in range(count)], True
    return [up[i * rows : (i + 1) * rows] for i in range(count)], False


def _split_down(down: np.ndarray, count: int, diagonal: bool) -> list[np.ndarray]:
    if count == 1 or not diagonal:
        return [down] * count
    rows = down.shape[0] // count
    return [down[i * rows : (i + 1) * rows] for i in range(count)]


def convert_tables(
    st: SafeTensors, family: str, scale: float, ignore_unmatched: bool, log: Callable[[str], None]
) -> dict[str, np.ndarray]:
    data = _mappings()
    resnets = [tuple(p) for p in data["resnets_partials"]]
    if family == "sd1":
        unet_table, fixed_table = data["sd1_unet"], {}
        partials = [tuple(p) for p in data["sd1_unet_partials"]]
        te1_map, te2_map = _clip_text(12), {}
    else:
        unet_table, fixed_table = data["sdxl_unet"], data["sdxl_unet_fixed"]
        partials = _sdxl_partials()
        te1_map = _clip_text(12)
        te2_map = _clip_text(32)
    unet_map = _expand_unet(_with_diffusers_names(unet_table, partials, resnets))
    fixed_map = _expand_unet(_with_diffusers_names(fixed_table, partials, resnets)) if fixed_table else {}
    te1_map, te2_map = _expand_text(te1_map), _expand_text(te2_map)

    state = _normalize_state(st.keys(), "te1" if family == "sdxl" else "te")
    # Every file key must be an up, down or alpha tensor (Draw Things drops the rest silently).
    stray = sorted(k for k in st.keys() if split_key(k) is None)
    if stray and not ignore_unmatched:
        raise ConversionError(
            f"{len(stray)} tensors are not plain LoRA up/down/alpha keys (for example {', '.join(stray[:4])}); "
            f"DoRA, bias and other extra keys are not supported. Pass --ignore-unmatched to skip them, "
            f"or {COLAB_HINT}"
        )
    if stray:
        log(f"warning: skipped {len(stray)} tensors that are not up/down/alpha keys (for example {stray[0]}).")
    for key in state:
        if (
            any(tag in key for tag in ("hada_", "lokr_", "::diff"))
            or key.endswith((".diff", ".diff_b", ".mid.weight"))
            or key in ("emb_params", "clip_g", "clip_l")
        ):
            raise ConversionError(f"{key}: LoHa, LoKr, Tucker, diff and embedding keys are not supported. {COLAB_HINT}")

    swap_te2 = False
    if family == "sdxl":

        def k_proj(tag: str) -> int | None:
            for key in sorted(state):
                if key.endswith("encoder_layers_0_self_attn_k_proj.lora_down.weight") and f"_{tag}_" in key:
                    return st.shape(state[key])[-1]
            return None

        te1, te2, te = k_proj("te1"), k_proj("te2"), k_proj("te")
        if te1 is not None and te2 is not None:
            swap_te2 = te2 != 1280 and te1 == 1280
        elif te2 is not None:
            swap_te2 = te2 == 768
        elif te1 == 1280 or te == 1280:
            swap_te2 = True

    ordered = sorted(state)

    def alpha_of(key: str) -> np.float32 | None:
        alpha_key = key[:-14] + "alpha"
        return _scalar(st.read(state[alpha_key])) if alpha_key in state else None

    # The last (sorted) source key per table entry wins, like the Swift ``mappingUpKeys``.
    def last_by_entry(table: dict[str, tuple[str, ...]], suffix: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for key in ordered:
            parts = key.split("_")
            if len(parts) <= 2:
                continue
            hit = _find(table, _derive_keys(parts))
            if hit is not None and key.endswith(suffix):
                found[hit[0]] = key
        return found

    up_for = last_by_entry(unet_map, "up.weight")
    down_for = last_by_entry(unet_map, "down.weight")

    out: dict[str, np.ndarray] = {}
    diagonal: set[str] = set()
    unmatched: list[str] = []

    def text_target(key: str, parts: list[str]) -> tuple[tuple[str, ...], bool] | None:
        te2 = parts[1] == "te2"
        table = te2_map if te2 != swap_te2 else te1_map
        hit = _find(table, _derive_keys(parts))
        if hit is None:
            return None
        return hit[1][:1], te2 != swap_te2

    consumed: set[str] = set()
    for key in ordered:
        parts = key.split("_")
        if len(parts) <= 2 or not key.endswith("up.weight"):
            continue
        hit = _find(unet_map, _derive_keys(parts))
        fixed = False
        if hit is not None and hit[0] in consumed:
            continue
        if hit is None and fixed_map:
            hit, fixed = _find(fixed_map, _derive_keys(parts)), True
        if hit is not None:
            entry, names = hit
            real = state[up_for.get(entry, key)] if not fixed and entry in up_for else state[key]
            if not fixed:
                consumed.add(entry)
            up = st.read_f16(real)
            pieces, is_diag = _split_up(up, len(names))
            if is_diag:
                diagonal.add(entry)
            prefix = "unet_fixed" if fixed else "unet"
            alpha = alpha_of(key)
            for name, piece in zip(names, pieces, strict=True):
                out[f"__{prefix}__[{name}]__up__"] = scale_up(piece, alpha, scale, rank=up.shape[1])
            continue
        target = text_target(key, parts)
        if target is None:
            unmatched.append(key)
            continue
        names, is_te2 = target
        up = st.read_f16(state[key])
        prefix = "__te2__text_model__" if is_te2 else "__text_model__"
        out[f"{prefix}[{names[0]}]__up__"] = scale_up(up, alpha_of(key), scale, rank=up.shape[1])

    consumed.clear()
    for key in ordered:
        parts = key.split("_")
        if len(parts) <= 2 or not key.endswith("down.weight"):
            continue
        hit = _find(unet_map, _derive_keys(parts))
        fixed = False
        if hit is not None and hit[0] in consumed:
            continue
        if hit is None and fixed_map:
            hit, fixed = _find(fixed_map, _derive_keys(parts)), True
        if hit is not None:
            entry, names = hit
            real = state[down_for.get(entry, key)] if not fixed and entry in down_for else state[key]
            if not fixed:
                consumed.add(entry)
            down = scale_down(st.read_f16(real), scale)
            prefix = "unet_fixed" if fixed else "unet"
            for name, piece in zip(names, _split_down(down, len(names), entry in diagonal), strict=True):
                out[f"__{prefix}__[{name}]__down__"] = piece
            continue
        target = text_target(key, parts)
        if target is None:
            unmatched.append(key)
            continue
        names, is_te2 = target
        prefix = "__te2__text_model__" if is_te2 else "__text_model__"
        out[f"{prefix}[{names[0]}]__down__"] = scale_down(st.read_f16(state[key]), scale)

    for name, array in sorted(out.items()):
        if not name.endswith("__up__"):
            continue
        down = out.get(name[: -len("__up__")] + "__down__")
        if down is None:
            raise ConversionError(f"{name[:-6]}: has an up tensor but no down tensor in the file. {COLAB_HINT}")
        if array.ndim != down.ndim or array.shape[1] != down.shape[0]:
            raise ConversionError(
                f"{name[:-6]}: rank mismatch, up {array.shape} does not fit down {down.shape}. {COLAB_HINT}"
            )
    for name in sorted(out):
        if name.endswith("__down__") and name[: -len("__down__")] + "__up__" not in out:
            raise ConversionError(f"{name[:-8]}: has a down tensor but no up tensor in the file. {COLAB_HINT}")
    if unmatched:
        sample = ", ".join(sorted(set(unmatched))[:4])
        message = f"{len(set(unmatched))} LoRA tensors have no place in the {family} layers (for example {sample})."
        if not ignore_unmatched:
            raise ConversionError(
                f"{message} Pass --ignore-unmatched to skip them like Draw Things does, or {COLAB_HINT}"
            )
        log(f"warning: {message} Skipped.")
    return out


# --------------------------------------------------------------------------- conversion


def convert(
    source: Path,
    destination: Path,
    family: str,
    scale: float = 1.0,
    log: Callable[[str], None] = print,
    ignore_unmatched: bool = False,
    allow_experimental: bool = False,
) -> int:
    """Convert ``source`` to ``destination``. Returns the number of tensors written."""
    if scale <= 0:
        raise ConversionError("The scale must be greater than 0.")
    if family in EXPERIMENTAL_FAMILIES:
        if not allow_experimental:
            raise ConversionError(
                f"{family} is experimental: its output has not been compared with Draw Things yet. "
                "Pass --experimental to convert anyway, or " + COLAB_HINT
            )
        log(f"warning: {family} is experimental, its output has not been compared with Draw Things yet.")
    if family in TABLE_FAMILIES:
        _check_numpy()
        with SafeTensors(source) as st:
            tensors = convert_tables(st, family, scale, ignore_unmatched, log)
        if not tensors:
            raise ConversionError("No LoRA tensors found in the file.")
        write_ckpt(destination, tensors)
        log(f"wrote {len(tensors)} tensors to {destination}")
        return len(tensors)
    resolver = FAMILIES.get(family)
    if resolver is None:
        raise ConversionError(f"Family {family!r} is not supported by the Python converter. {COLAB_HINT}")
    _check_numpy()
    with SafeTensors(source) as st:
        return _convert_open(st, destination, family, resolver, scale, log)


def _convert_open(
    st: SafeTensors,
    destination: Path,
    family: str,
    resolver: Resolver,
    scale: float,
    log: Callable[[str], None],
) -> int:
    parts: dict[str, dict[str, LoraKey]] = {}
    unknown: list[str] = []
    for key in st.keys():
        parsed = split_key(key)
        if parsed is None:
            unknown.append(key)
            continue
        roles = parts.setdefault(parsed.module, {})
        if parsed.role in roles:
            raise ConversionError(
                f"{key} and {roles[parsed.role].key} are both the {parsed.role} of the same module "
                f"({parsed.module}). {COLAB_HINT}"
            )
        roles[parsed.role] = parsed
    if unknown:
        sample = ", ".join(unknown[:5])
        raise ConversionError(
            f"{len(unknown)} tensors are not plain LoRA up/down/alpha keys (for example {sample}). "
            f"LoHa, LoKr, bias and diff keys are not supported. {COLAB_HINT}"
        )

    out: dict[str, np.ndarray] = {}
    unmatched: list[str] = []
    for module, roles in sorted(parts.items()):
        target = resolver(module)
        if target is None:
            unmatched.append(module)
            continue
        if "up" not in roles or "down" not in roles:
            raise ConversionError(f"{module}: needs both an up and a down tensor.")
        alpha = _scalar(st.read(roles["alpha"].key)) if "alpha" in roles else None
        up = st.read_f16(roles["up"].key)
        down = st.read_f16(roles["down"].key)
        if up.ndim != 2 or down.ndim != 2 or up.shape[1] != down.shape[0]:
            raise ConversionError(f"{module}: unexpected shapes up {up.shape} and down {down.shape}. {COLAB_HINT}")
        if target.input_offsets is not None:
            pieces = split_fused_inputs(up, down, target.input_offsets)
        else:
            pieces = split_fused(up, down, len(target.names), target.output_offsets)
        for name, (piece_up, piece_down) in zip(target.names, pieces, strict=True):
            # The rank used for alpha scaling is the one of the whole fused tensor, as in Swift.
            out[f"__{target.prefix}__[{name}]__up__"] = scale_up(piece_up, alpha, scale, rank=up.shape[1])
            out[f"__{target.prefix}__[{name}]__down__"] = scale_down(piece_down, scale)
    if unmatched:
        sample = ", ".join(unmatched[:5])
        raise ConversionError(
            f"{len(unmatched)} modules do not belong to the {family} layers this converter knows "
            f"(for example {sample}). The file may be for another family or train layers that are "
            f"not ported yet. {COLAB_HINT}"
        )
    if not out:
        raise ConversionError("No LoRA tensors found in the file.")
    write_ckpt(destination, out)
    log(f"wrote {len(out)} tensors to {destination}")
    return len(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("destination", type=Path)
    ap.add_argument("--family", required=True, choices=sorted([*FAMILIES, *TABLE_FAMILIES]))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument(
        "--ignore-unmatched",
        action="store_true",
        help="sd1 and sdxl only: skip LoRA tensors that have no place in the model, as Draw Things does",
    )
    ap.add_argument(
        "--experimental",
        action="store_true",
        help="allow families that are not verified against Draw Things yet (sdxl)",
    )
    args = ap.parse_args()
    try:
        convert(
            args.source,
            args.destination,
            args.family,
            args.scale,
            ignore_unmatched=args.ignore_unmatched,
            allow_experimental=args.experimental,
        )
    except ConversionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

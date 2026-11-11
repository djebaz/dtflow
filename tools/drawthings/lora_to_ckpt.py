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
import json
import math
import re
import sqlite3
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

COLAB_HINT = (
    "Use the Colab notebook instead: tools/colab/dtflow_lora_minimal_convert.ipynb "
    "(it supports every Draw Things family)."
)

# ccv flags as stored in the tensors table (see inspect_ckpt.py for the datatype list).
DATATYPE_F16 = 0x20000
FORMAT_NCHW = 0x02
TYPE_CPU = 0x01
MAX_DIM_ALLOC = 12

_SAFETENSORS_DTYPES = {"F16": np.float16, "F32": np.float32, "F64": np.float64}


class ConversionError(Exception):
    """Raised for anything the converter refuses to do."""


# --------------------------------------------------------------------------- reading


class SafeTensors:
    """Minimal read-only safetensors reader (memory mapped)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with path.open("rb") as handle:
            (header_len,) = struct.unpack("<Q", handle.read(8))
            if header_len > 100 * 1024 * 1024:
                raise ConversionError(f"{path.name}: not a safetensors file (header too large).")
            header = json.loads(handle.read(header_len))
        self._base = 8 + header_len
        self.entries = {k: v for k, v in header.items() if k != "__metadata__"}
        self._map = np.memmap(path, dtype=np.uint8, mode="r")

    def keys(self) -> list[str]:
        return list(self.entries)

    def shape(self, key: str) -> tuple[int, ...]:
        return tuple(self.entries[key]["shape"])

    def read(self, key: str) -> np.ndarray:
        entry = self.entries[key]
        begin, end = entry["data_offsets"]
        raw = self._map[self._base + begin : self._base + end]
        dtype = entry["dtype"]
        shape = tuple(entry["shape"])
        if dtype == "BF16":
            bits = np.frombuffer(raw, dtype=np.uint16).astype(np.uint32) << 16
            return bits.view(np.float32).reshape(shape)
        if dtype not in _SAFETENSORS_DTYPES:
            raise ConversionError(f"{key}: unsupported dtype {dtype}. {COLAB_HINT}")
        return np.frombuffer(raw, dtype=_SAFETENSORS_DTYPES[dtype]).reshape(shape)


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


def resolve_zimage(module: str) -> Target | None:
    """Z Image (Turbo): 30 main layers plus 2 noise refiner and 2 context refiner blocks.

    Names follow the Swift model: ``t-<group>_<layer>-<block index>-0`` with an empty group
    for the main layers. Fused sources (``attention.qkv`` and ``adaLN_modulation.0``) map
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
        return f"t-{prefix}{layer}-{index}-0"

    if leaf in _ZIMAGE_LEAVES:
        return Target("dit", (name(_ZIMAGE_LEAVES[leaf]),))
    if leaf == "attention_qkv":
        return Target("dit", (name("q"), name("k"), name("v")))
    if leaf == "adaLN_modulation_0" and group != "context_refiner":
        return Target("dit", tuple(name(f"ada_ln_{k}") for k in range(4)))
    return None


FAMILIES: dict[str, Resolver] = {
    "krea2": resolve_krea2,
    "zimage": resolve_zimage,
}


# --------------------------------------------------------------------------- fused targets


def is_diagonal_up(up: np.ndarray, count: int) -> bool:
    """True when ``up`` is block diagonal over ``count`` parts (off-diagonal blocks all zero bytes)."""
    if up.ndim != 2 or up.shape[1] % count != 0:
        return False
    rows = up.shape[0] // count
    cols = up.shape[1] // count
    bits = up.view(np.uint16)
    for i in range(count):
        start = i * rows
        end = (i + 1) * rows if i < count - 1 else up.shape[0]
        for j in range(count):
            if i != j and bits[start:end, j * cols : (j + 1) * cols].any():
                return False
    return True


def split_fused(up: np.ndarray, down: np.ndarray, count: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split one fused LoRA pair into ``count`` pairs, as the Swift ``ModelWeightElement.write`` does."""
    if count == 1:
        return [(up, down)]
    if up.shape[0] % count != 0:
        raise ConversionError(f"Cannot split {up.shape[0]} output rows into {count} equal parts. {COLAB_HINT}")
    rows = up.shape[0] // count
    if is_diagonal_up(up, count):
        cols = up.shape[1] // count
        if down.shape[0] % count != 0:
            raise ConversionError(f"Cannot split {down.shape[0]} rank rows into {count} parts. {COLAB_HINT}")
        down_rows = down.shape[0] // count
        return [
            (
                up[i * rows : (i + 1) * rows, i * cols : (i + 1) * cols],
                down[i * down_rows : (i + 1) * down_rows],
            )
            for i in range(count)
        ]
    return [(up[i * rows : (i + 1) * rows], down) for i in range(count)]


# --------------------------------------------------------------------------- float16


def to_f16(values: np.ndarray) -> np.ndarray:
    """Convert to float16 the way Draw Things (ccv) does: truncate toward zero, no rounding.

    numpy rounds to nearest. The two only differ when bits are lost, which for BF16 sources
    means the float16 subnormal range (below about 6e-5), where Draw Things drops the last bit
    that numpy would round up.
    """
    x = np.asarray(values, dtype=np.float32)
    rounded = x.astype(np.float16)
    too_big = np.abs(rounded.astype(np.float32)) > np.abs(x)
    bits = rounded.view(np.uint16)
    # Sign-magnitude: one less in the magnitude bits is one step toward zero.
    fix = too_big & ((bits & 0x7FFF) != 0)
    bits = np.where(fix, bits - 1, bits).astype(np.uint16)
    # Finite values above the float16 range round to infinity; truncation saturates instead.
    overflow = np.isinf(rounded) & np.isfinite(x)
    bits = np.where(overflow, (bits & 0x8000) | 0x7BFF, bits).astype(np.uint16)
    return bits.view(np.float16)


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
    tmp = path.with_name(path.name + ".part")
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
                    FORMAT_NCHW,
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
        tmp.replace(path)
        done = True
    except sqlite3.Error as error:
        raise ConversionError(f"Cannot write {path}: {error}") from error
    finally:
        if not done:
            tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- conversion


def convert(
    source: Path, destination: Path, family: str, scale: float = 1.0, log: Callable[[str], None] = print
) -> int:
    """Convert ``source`` to ``destination``. Returns the number of tensors written."""
    resolver = FAMILIES.get(family)
    if resolver is None:
        raise ConversionError(f"Family {family!r} is not supported by the Python converter. {COLAB_HINT}")
    if scale <= 0:
        raise ConversionError("The scale must be greater than 0.")
    st = SafeTensors(source)

    parts: dict[str, dict[str, LoraKey]] = {}
    unknown: list[str] = []
    for key in st.keys():
        parsed = split_key(key)
        if parsed is None:
            unknown.append(key)
            continue
        parts.setdefault(parsed.module, {})[parsed.role] = parsed
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
        up = to_f16(st.read(roles["up"].key))
        down = to_f16(st.read(roles["down"].key))
        if up.ndim != 2 or down.ndim != 2 or up.shape[1] != down.shape[0]:
            raise ConversionError(f"{module}: unexpected shapes up {up.shape} and down {down.shape}. {COLAB_HINT}")
        pieces = split_fused(up, down, len(target.names))
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
    ap.add_argument("--family", required=True, choices=sorted(FAMILIES))
    ap.add_argument("--scale", type=float, default=1.0)
    args = ap.parse_args()
    try:
        convert(args.source, args.destination, args.family, args.scale)
    except ConversionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

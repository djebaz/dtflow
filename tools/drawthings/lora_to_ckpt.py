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
                    FORMAT_NHWC,
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

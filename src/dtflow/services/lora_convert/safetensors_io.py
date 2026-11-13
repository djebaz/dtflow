"""Minimal read-only safetensors reader (no torch, no memory mapping)."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np

from dtflow.services.lora_convert.errors import COLAB_HINT, SourceReadError, UnsupportedLoraFormatError
from dtflow.services.lora_convert.numeric import to_f16

_SAFETENSORS_DTYPES = {"F16": "<f2", "F32": "<f4"}


class SafeTensors:
    """Minimal read-only safetensors reader.

    Use it as a context manager: the file is closed on exit, because on Windows an open
    file cannot be deleted or moved. Tensors are read one at a time (no memory mapping).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None
        self.metadata: dict[str, str] = {}
        try:
            size = path.stat().st_size
            if size < 8:
                raise SourceReadError(f"{path.name}: not a safetensors file (too small).")
            with path.open("rb") as handle:
                (header_len,) = struct.unpack("<Q", handle.read(8))
                if header_len > 100 * 1024 * 1024 or 8 + header_len > size:
                    raise SourceReadError(f"{path.name}: not a safetensors file (bad header size).")
                header = json.loads(handle.read(header_len))
            if not isinstance(header, dict):
                raise SourceReadError(f"{path.name}: not a safetensors file (bad header).")
            self._base = 8 + header_len
            self.entries = {k: v for k, v in header.items() if k != "__metadata__"}
            declared = header.get("__metadata__")
            # Descriptive only: a flat string map. Anything else is ignored, never an error.
            self.metadata = (
                {k: v for k, v in declared.items() if isinstance(k, str) and isinstance(v, str)}
                if isinstance(declared, dict)
                else {}
            )
            for key, entry in self.entries.items():
                begin, end = entry["data_offsets"]
                if not 0 <= begin <= end <= size - self._base:
                    raise SourceReadError(f"{path.name}: {key} points outside the file (truncated?).")
        except (OSError, struct.error, ValueError, KeyError, TypeError) as error:
            raise SourceReadError(f"{path.name}: cannot read this safetensors file ({error}).") from error
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
            raise UnsupportedLoraFormatError(f"{key}: unsupported dtype {dtype}.", hint=COLAB_HINT)
        if self._file is None:
            raise SourceReadError(f"{self.path.name}: the file was already closed.")
        shape = tuple(entry["shape"])
        width = {"F16": 2, "BF16": 2, "F32": 4}[dtype]
        if (end - begin) != width * math.prod(shape):
            raise SourceReadError(f"{key}: data size does not match shape {shape} (corrupt file?).")
        self._file.seek(self._base + begin)
        raw = self._file.read(end - begin)
        if len(raw) != end - begin:
            raise SourceReadError(f"{key}: the file ended early (truncated?).")
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

"""Experimental F16 storage codecs, independent of model family and provider work.

Layout/rounding: s4nnc 50d060dba795a6d9ca8497ed0222dde7492a8d12,
nnc/Store.swift. Clustering: optional MIT-licensed kmeans1d 0.5.1,
not a claim of bit-identical ccv cluster/tie decisions.
"""

from __future__ import annotations

import struct
import signal
import zlib
from collections.abc import Callable

import numpy as np

from dtflow.core.models import ValidationError
from dtflow.services.lora_convert.numeric import f32_to_f16_bits

BLOCK = 16_384
Q8P = 0x8A1E8B
EZM7 = 0x511
CODECS = {0: "f16", Q8P: "q8p", EZM7: "ezm7"}


class QuantizationError(ValidationError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def __reduce__(self):
        return type(self), (self.code, str(self))


def start_encoder_worker():
    # The parent owns interruption and drains at most one admitted block per worker.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def encode_q8p_block(block: np.ndarray) -> bytes:
    """Encode one independent palette block; workers never open files or state."""
    try:
        import kmeans1d
    except (ImportError, OSError) as exc:
        raise QuantizationError(
            "clustering_unavailable", "Install the optional quantization group: uv sync --group quantization."
        ) from exc
    try:
        clustered = kmeans1d.cluster(block.astype(np.float64).tolist(), min(256, len(block)))
    except (AssertionError, RuntimeError) as exc:
        raise QuantizationError("clustering_failed", "Native clustering could not process this block.") from exc
    labels = np.asarray(clustered.clusters, dtype=np.int64)
    centers = np.asarray(clustered.centroids, dtype=np.float32)
    if (
        len(labels) != len(block)
        or not 1 <= len(centers) <= 256
        or ((labels < 0).any() or (labels >= len(centers)).any() or not np.isfinite(centers).all())
    ):
        raise QuantizationError("clustering_failed", "Clustering returned an invalid palette.")
    palette = np.zeros(256, dtype="<u2")
    palette[: len(centers)] = f32_to_f16_bits(centers)
    return palette.tobytes() + labels.astype("u1").tobytes()


def q8p_fits(count: int) -> bool:
    blocks = (count + BLOCK - 1) // BLOCK
    return 4 + count + blocks * 512 <= count * 2 + 4


def encode_chunk(values: np.ndarray, whole_tensor: bool) -> tuple[int, bytes]:
    """One pool task, including small-tensor fallback; never schedules other work."""
    if whole_tensor:
        return encode(values, lambda: None)
    return Q8P, encode_q8p_block(values)


def decode(payload: bytes, count: int, codec: int) -> np.ndarray:
    """Return flat F16 values, rejecting malformed, oversized or unknown encodings."""
    if codec == 0:
        if len(payload) != count * 2:
            raise QuantizationError("payload_size", "Raw F16 payload does not match its dimensions.")
        result = np.frombuffer(payload, dtype="<f2")
    elif codec == Q8P:
        blocks = (count + BLOCK - 1) // BLOCK
        if len(payload) != 4 + count + blocks * 512 or struct.unpack_from("<I", payload)[0] != BLOCK:
            raise QuantizationError("q8p_layout", "Unsupported or malformed q8p block layout.")
        result = np.empty(count, dtype="<f2")
        cursor = 4
        for start in range(0, count, BLOCK):
            length = min(BLOCK, count - start)
            palette = np.frombuffer(payload, dtype="<f2", count=256, offset=cursor)
            indices = np.frombuffer(payload, dtype="u1", count=length, offset=cursor + 512)
            result[start : start + length] = palette[indices]
            cursor += 512 + length
    elif codec == EZM7:
        if len(payload) < 4 + count:
            raise QuantizationError("ezm7_layout", "Malformed ezm7 payload.")
        compressed = struct.unpack_from("<I", payload)[0]
        if compressed == 0 or len(payload) != 4 + compressed + count:
            raise QuantizationError("ezm7_layout", "Malformed ezm7 exponent stream.")
        stream = zlib.decompressobj(wbits=-15)
        try:
            exponents = stream.decompress(payload[4 : 4 + compressed], count + 1)
        except zlib.error as exc:
            raise QuantizationError("ezm7_layout", "Invalid ezm7 compressed exponents.") from exc
        if len(exponents) != count or not stream.eof or stream.unused_data or stream.unconsumed_tail:
            raise QuantizationError("ezm7_layout", "Invalid ezm7 exponent length.")
        exponent = np.frombuffer(exponents, dtype="u1").astype("<u2")
        if (exponent > 31).any():
            raise QuantizationError("ezm7_layout", "Invalid ezm7 exponent value.")
        packed = np.frombuffer(payload, dtype="u1", count=count, offset=4 + compressed).astype("<u2")
        result = ((packed >> 7) << 15 | exponent << 10 | (packed & 127) << 3).astype("<u2").view("<f2")
    else:
        raise QuantizationError("unsupported_codec", "Only raw F16, q8p and ezm7 are supported.")
    if not np.isfinite(result).all():
        raise QuantizationError("nonfinite", "Non-finite tensor values are not supported by this experiment.")
    return result


def encode(values: np.ndarray, tick: Callable[[], None]) -> tuple[int, bytes]:
    """Use q8p if it fits upstream's raw-size+4 buffer, then ezm7, then raw F16."""
    count = len(values)
    capacity = count * 2 + 4  # ccv_nnc_tensor_io.c's CPU encoder capacity.
    if q8p_fits(count):
        output = bytearray(struct.pack("<I", BLOCK))
        for start in range(0, count, BLOCK):
            output.extend(encode_q8p_block(values[start : start + BLOCK]))
            tick()
        return Q8P, bytes(output)

    bits = values.view("<u2")
    mantissa = bits & 1023
    shifted = mantissa >> 3
    discarded = mantissa & 7
    rounded = shifted + ((discarded > 4) | ((discarded == 4) & ((shifted & 1) != 0)))
    rounded = np.where(rounded >= 128, shifted, rounded)
    packed = (((bits >> 15) << 7) | rounded).astype("u1").tobytes()
    exponent = ((bits >> 10) & 31).astype("u1").tobytes()
    compressor = zlib.compressobj(level=-1, wbits=-15, memLevel=9)
    compressed = compressor.compress(exponent) + compressor.flush()
    payload = struct.pack("<I", len(compressed)) + compressed + packed
    tick()
    return (EZM7, payload) if len(payload) <= capacity else (0, values.astype("<f2", copy=False).tobytes())

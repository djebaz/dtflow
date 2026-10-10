"""Received-tensor checks and decoding.

Limits are enforced after a payload has been received, never during transport, and only on what can be read without
decoding: the 68-byte CCV header that ``drawthings-py`` itself reads (dimensions and channels) plus byte counts. fpzip
sizes its own allocation from its stream header, which this module does not inspect, so a hostile or corrupt server
could still request a large allocation; the mitigations are the strict-TLS, user-chosen server and the caps below.
"""

from __future__ import annotations

import logging
import struct

from drawthings_py.image._preview_decoders import decode_preview
from drawthings_py.image.image_buffer import ImageBuffer

from dtflow.core.models import Designation, ImageArtifact, ResolvedGenerationRequest, ResourceLimits
from dtflow.core.png import encode_png

log = logging.getLogger(__name__)
HEADER_BYTES = 68
FINAL_CHANNELS = (3, 4)


class TensorRejected(ValueError):
    """A received tensor failed a pre-decode check; ``code`` is a stable, provider-text-free identifier."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def header(tensor: bytes) -> tuple[int, int, int]:
    """Return (width, height, channels) from the CCV header, or raise ``TensorRejected``."""
    if len(tensor) < HEADER_BYTES:
        raise TensorRejected("tensor_truncated")
    values = struct.unpack_from("<17I", tensor)
    height, width, channels = values[6:9]
    if width <= 0 or height <= 0:
        raise TensorRejected("tensor_invalid_dimensions")
    return width, height, channels


def check_final(tensor: bytes, request: ResolvedGenerationRequest, limits: ResourceLimits) -> tuple[int, int, int]:
    if len(tensor) > limits.transport_bytes:
        raise TensorRejected("tensor_transport_limit")
    width, height, channels = header(tensor)
    if (width, height) != (request.width_px, request.height_px):
        raise TensorRejected("tensor_dimensions_mismatch")
    if channels not in FINAL_CHANNELS:
        raise TensorRejected("tensor_unsupported_channels")
    if width * height * channels * 2 > limits.tensor_bytes:  # fp16 values before dequantization
        raise TensorRejected("tensor_decoded_limit")
    return width, height, channels


def pixel_stats(data: bytes, width: int, height: int, channels: int) -> dict[str, float | int]:
    """Development diagnostics only: coarse statistics of a few rows that tell noise (high neighbour difference, flat
    spread) from an image, without logging pixel data."""
    rows = [data[y * width * channels : (y + 1) * width * channels] for y in range(0, height, max(1, height // 8))]
    flat = b"".join(rows)
    diffs = [abs(row[i] - row[i + channels]) for row in rows for i in range(0, len(row) - channels)]
    mean = sum(flat) / len(flat)
    return {
        "mean": round(mean, 1),
        "std": round((sum((value - mean) ** 2 for value in flat) / len(flat)) ** 0.5, 1),
        "min": min(flat),
        "max": max(flat),
        "neighbour_diff": round(sum(diffs) / len(diffs), 1),
    }


def decode_final(tensor: bytes, width: int, height: int, channels: int, index: int, limits: ResourceLimits):
    """Decode one checked final tensor to a PNG artifact; runs in a worker thread."""
    try:
        buffer = ImageBuffer.from_tensor(tensor)
        if (buffer.width, buffer.height, buffer.channels) != (width, height, channels):
            raise TensorRejected("tensor_dimensions_mismatch")
        if log.isEnabledFor(logging.DEBUG):
            log.debug("final tensor %s", pixel_stats(buffer.data, width, height, channels))
        encoded = encode_png(buffer.data, width, height, channels)
    except TensorRejected:
        raise
    except Exception as exc:
        raise TensorRejected("tensor_decode_failed") from exc
    if len(encoded) > limits.artifact_bytes:
        raise TensorRejected("artifact_encoded_limit")
    return ImageArtifact(
        f"final-{index}", Designation.FINAL, "image/png", encoded, width, height, (("tensor_channels", str(channels)),)
    )


def decode_preview_artifact(data: bytes, index: int, limits: ResourceLimits) -> ImageArtifact | None:
    """Best-effort preview through the private drawthings-py decoder; any problem drops the preview silently."""
    try:
        if len(data) > limits.transport_bytes:
            return None
        width, height, channels = header(data)
        if width * height * channels * 2 > limits.tensor_bytes:
            return None
        buffer = decode_preview(data)
        if buffer is None:
            return None
        encoded = encode_png(buffer.data, buffer.width, buffer.height, buffer.channels)
    except Exception:
        return None
    if len(encoded) > limits.preview_bytes:
        return None
    return ImageArtifact(f"preview-{index}", Designation.PREVIEW, "image/png", encoded, buffer.width, buffer.height)

"""Video request pieces for the Draw Things wire: frame count and the single start-image tensor.

``GenConfig`` of drawthings-py 0.4.0 has no frame-count argument, so ``num_frames`` is set on the serialized table
(proven on DT+ by the POC). ``fps`` is never sent: playback rate is DTFlow-side data (``core.video``).
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import flatbuffers
from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration, GenerationConfigurationT

from dtflow.core.models import ResolvedGenerationRequest, ValidationError

HEADER_BYTES = 68
MAX_START_IMAGE_BYTES = 32 * 1024 * 1024


def with_num_frames(configuration: bytes, frames: int) -> bytes:
    table = GenerationConfigurationT.InitFromObj(GenerationConfiguration.GetRootAs(configuration, 0))
    table.numFrames = frames
    builder = flatbuffers.Builder(0)
    builder.Finish(table.Pack(builder))
    return bytes(builder.Output())


def start_image_tensor(request: ResolvedGenerationRequest) -> bytes:
    """One start frame as a one-frame CCV tensor (float16 in [-1, 1], NHWC), cover-fitted to the request size.

    Runs on a worker. The file must still be the one the request recorded (same SHA-256)."""
    import numpy as np
    from PIL import Image, ImageOps

    path = Path(request.start_image or "")
    try:
        if path.stat().st_size > MAX_START_IMAGE_BYTES:
            raise ValidationError("The start image is too large.")
        data = path.read_bytes()
    except OSError as exc:
        raise ValidationError("The start image can no longer be read.") from exc
    if hashlib.sha256(data).hexdigest() != request.start_image_sha256:
        raise ValidationError("The start image changed after the request was made.")
    import io

    with Image.open(io.BytesIO(data)) as image:
        frame = np.asarray(
            ImageOps.fit(image.convert("RGB"), (request.width_px, request.height_px), Image.Resampling.LANCZOS)
        )
    values = (frame.astype("<f2") / 255.0 * 2.0 - 1.0).astype("<f2")
    header = bytearray(HEADER_BYTES)
    struct.pack_into("<9I", header, 0, 0, 1, 2, 0x20000, 0, 1, request.height_px, request.width_px, 3)
    return bytes(header) + np.ascontiguousarray(values).tobytes()

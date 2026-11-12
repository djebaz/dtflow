"""``convert_lora``: the single, synchronous, Qt-free entry point.

Callers decide the thread (the GUI uses ``asyncio.to_thread``, the CLI calls it directly).
Thread-safe for independent requests; one request is not re-entrant; the only shared state is
the ``functools.cache`` of the mapping JSON. Output is written to ``<destination>.<pid>.part``
and renamed at the end, so a half converted file never carries the final name.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from dtflow.services.lora_convert.ckpt_writer import write_ckpt
from dtflow.services.lora_convert.context import ConversionContext, ConversionProgress
from dtflow.services.lora_convert.errors import (
    COLAB_HINT,
    ConversionCancelled,
    ConversionError,
    ExperimentalFamilyError,
    UnsupportedLoraFormatError,
)
from dtflow.services.lora_convert.models import ConversionRequest, ConversionResult
from dtflow.services.lora_convert.numeric import check_numpy
from dtflow.services.lora_convert.provenance import (
    CONVERTER_NAME,
    CONVERTER_VERSION,
    DT_REVISION,
    ConversionProvenance,
)
from dtflow.services.lora_convert.registry import lookup
from dtflow.services.lora_convert.safetensors_io import SafeTensors

log = logging.getLogger(__name__)


def _sha256_source(path: Path, ctx: ConversionContext) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            ctx.check_cancel()
            digest.update(chunk)
    return digest.hexdigest()


def convert_lora(
    request: ConversionRequest,
    *,
    progress: Callable[[ConversionProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> ConversionResult:
    """Convert ``request.source`` to a Draw Things float16 LoRA ``.ckpt`` at ``request.destination``."""
    ctx = ConversionContext(progress=progress, cancel=cancel)
    log.info("lora_convert.started family=%s", request.family)
    try:
        result = _convert(request, ctx)
    except ConversionCancelled:
        log.info("lora_convert.cancelled family=%s", request.family)
        raise
    except ConversionError as error:
        log.info("lora_convert.failed family=%s code=%s", request.family, error.code)
        raise
    log.info("lora_convert.finished family=%s tensors=%d", request.family, result.tensors_written)
    return result


def _convert(request: ConversionRequest, ctx: ConversionContext) -> ConversionResult:
    if request.scale <= 0:
        raise ConversionError("The scale must be greater than 0.")
    spec = lookup(request.family)
    if spec.experimental:
        if not request.allow_experimental:
            raise ExperimentalFamilyError(
                f"{request.family} is experimental: its output has not been compared with Draw Things yet. "
                "Pass --experimental to convert anyway, or " + COLAB_HINT
            )
        ctx.log(f"warning: {request.family} is experimental, its output has not been compared with Draw Things yet.")
    check_numpy()
    ctx.stage("reading")
    with SafeTensors(request.source) as st:
        source_size = request.source.stat().st_size
        ctx.stage("converting")
        tensors = spec.run(st, request, ctx)
    if not tensors:
        raise UnsupportedLoraFormatError("No LoRA tensors found in the file.")
    ctx.stage("writing", 0, len(tensors))
    destination_sha256, destination_size = write_ckpt(request.destination, tensors, ctx)
    ctx.stage("finishing", len(tensors), len(tensors))
    source_sha256 = _sha256_source(request.source, ctx) if request.hash_source else None
    ctx.log(f"wrote {len(tensors)} tensors to {request.destination}")
    provenance = ConversionProvenance(
        converter=CONVERTER_NAME,
        converter_version=CONVERTER_VERSION,
        draw_things_revision=DT_REVISION,
        family=spec.key,
        scale=request.scale,
        source_name=request.source.name,
        source_sha256=source_sha256,
        source_size=source_size,
        created_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        verification=spec.status,
    )
    return ConversionResult(
        destination=request.destination,
        family=spec.key,
        exact_architecture=spec.exact_architecture,
        tensors_written=len(tensors),
        destination_sha256=destination_sha256,
        destination_size=destination_size,
        source_sha256=source_sha256,
        source_size=source_size,
        experimental=spec.experimental,
        warnings=tuple(ctx.warnings),
        provenance=provenance,
    )

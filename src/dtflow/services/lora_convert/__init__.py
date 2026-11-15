"""Pure-Python LoRA converter: safetensors LoRA to a Draw Things float16 LoRA ``.ckpt``.

Qt-free and synchronous; callers decide the thread. Importing the package does not import
numpy: the engines load on the first ``convert_lora`` or ``supported_families`` call.

Hand port of the relevant part of Draw Things' ``ModelOp.LoRAImporter`` (revision db37e65,
the same one as the Colab notebook). Unknown keys and unsupported families stop with an error
that points to the Colab notebook, because the Swift importer silently ignores unknown keys and
a half converted LoRA is worse than none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dtflow.services.lora_convert.context import ConversionProgress
from dtflow.services.lora_convert.errors import (
    COLAB_HINT,
    ConversionCancelled,
    ConversionError,
    DependencyError,
    DestinationError,
    ExperimentalFamilyError,
    FamilyDataError,
    ShapeMismatchError,
    SourceReadError,
    UnmatchedModulesError,
    UnsupportedFamilyError,
    UnsupportedLoraFormatError,
)
from dtflow.services.lora_convert.models import ConversionRequest, ConversionResult
from dtflow.services.lora_convert.provenance import CONVERTER_VERSION, DT_REVISION, ConversionProvenance

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from dtflow.services.lora_convert.registry import FamilySpec

__all__ = [
    "COLAB_HINT",
    "CONVERTER_VERSION",
    "DT_REVISION",
    "ConversionCancelled",
    "ConversionError",
    "ConversionProgress",
    "ConversionProvenance",
    "ConversionRequest",
    "ConversionResult",
    "DependencyError",
    "DestinationError",
    "ExperimentalFamilyError",
    "FamilyDataError",
    "FamilySpec",
    "ShapeMismatchError",
    "SourceReadError",
    "UnmatchedModulesError",
    "UnsupportedFamilyError",
    "UnsupportedLoraFormatError",
    "convert_lora",
    "supported_families",
]


def convert_lora(
    request: ConversionRequest,
    *,
    progress: Callable[[ConversionProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> ConversionResult:
    """Convert one safetensors LoRA to a Draw Things ``.ckpt``; see ``api.convert_lora``."""
    from dtflow.services.lora_convert.api import convert_lora as _convert_lora

    return _convert_lora(request, progress=progress, cancel=cancel)


def supported_families() -> tuple[FamilySpec, ...]:
    """Family specs in the stable display order (the only list a dropdown or ``--family`` should use)."""
    from dtflow.services.lora_convert.registry import supported_families as _supported

    return _supported()


def __getattr__(name: str) -> object:
    if name == "FamilySpec":
        from dtflow.services.lora_convert.registry import FamilySpec

        return FamilySpec
    raise AttributeError(name)

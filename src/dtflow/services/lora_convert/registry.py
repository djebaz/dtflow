"""Family registry: metadata and the conversion entry of every supported family.

Registration is explicit (``families/__init__.py`` imports each family module once); there is
no directory scanning, so PyInstaller sees static imports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from dtflow.services.lora_convert.errors import UnsupportedFamilyError

if TYPE_CHECKING:
    import numpy as np

    from dtflow.services.lora_convert.context import ConversionContext
    from dtflow.services.lora_convert.models import ConversionRequest
    from dtflow.services.lora_convert.safetensors_io import SafeTensors

Status = Literal["verified", "unverified_on_dt_plus", "experimental"]
# (open source file, request, context) -> converted tensors by checkpoint name
FamilyRunner = Callable[["SafeTensors", "ConversionRequest", "ConversionContext"], "dict[str, np.ndarray]"]


@dataclass(frozen=True, slots=True)
class FamilySpec:
    key: str  # "krea2"
    label: str  # "Krea 2"
    exact_architecture: str  # Draw Things version string, "krea_2"
    status: Status
    experimental: bool  # True only for sdxl: needs allow_experimental
    kind: Literal["resolver", "tables"]
    run: FamilyRunner
    notes: str = ""


_REGISTRY: dict[str, FamilySpec] = {}


def register(spec: FamilySpec) -> FamilySpec:
    if spec.key in _REGISTRY:
        raise ValueError(f"LoRA family {spec.key!r} is registered twice.")
    _REGISTRY[spec.key] = spec
    return spec


def _load() -> None:
    from dtflow.services.lora_convert import families  # noqa: F401  (registers every family)


def lookup(key: str) -> FamilySpec:
    _load()
    spec = _REGISTRY.get(key)
    if spec is None:
        from dtflow.services.lora_convert.errors import COLAB_HINT

        raise UnsupportedFamilyError(f"Family {key!r} is not supported by the Python converter.", hint=COLAB_HINT)
    return spec


def supported_families() -> tuple[FamilySpec, ...]:
    """Specs in the stable display order (registration order)."""
    _load()
    return tuple(_REGISTRY.values())

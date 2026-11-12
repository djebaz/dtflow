"""Request and result types of ``convert_lora``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dtflow.services.lora_convert.provenance import ConversionProvenance


@dataclass(frozen=True, slots=True)
class ConversionRequest:
    source: Path  # .safetensors
    destination: Path  # .ckpt; must not exist (never overwritten)
    family: str  # converter key: "krea2" | "zimage" | "flux1" | "sd1" | "sdxl"
    scale: float = 1.0
    allow_experimental: bool = False
    ignore_unmatched: bool = False  # table families only
    hash_source: bool = True  # one extra pass over the source for the provenance record


@dataclass(frozen=True, slots=True)
class ConversionResult:
    destination: Path
    family: str
    exact_architecture: str  # Draw Things version string, e.g. "krea_2"
    tensors_written: int
    destination_sha256: str
    destination_size: int
    source_sha256: str | None  # None unless request.hash_source
    source_size: int
    experimental: bool
    warnings: tuple[str, ...]
    provenance: ConversionProvenance

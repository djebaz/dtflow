"""Provenance of a converted LoRA: which converter, which Draw Things revision, which source."""

from __future__ import annotations

from dataclasses import dataclass

# Bumped by hand whenever the output bytes of any family could change.
CONVERTER_VERSION = "1"
# Draw Things revision the converter was ported from (same one as the Colab notebook).
DT_REVISION = "db37e65"
CONVERTER_NAME = "dtflow.lora_convert"


@dataclass(frozen=True, slots=True)
class ConversionProvenance:
    """JSON-safe record (``dataclasses.asdict``); only the source base name, never a full path."""

    converter: str
    converter_version: str
    draw_things_revision: str
    family: str
    scale: float
    source_name: str
    source_sha256: str | None
    source_size: int
    created_at_utc: str
    verification: str  # "verified" | "unverified_on_dt_plus" | "experimental"

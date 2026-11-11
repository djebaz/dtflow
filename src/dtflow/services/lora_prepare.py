"""Local, read-only preparation of editable Draw Things LoRA batch rows.

File evidence is gathered without contacting DT+, persisting state or inferring
architecture from filename text. Final Prepare revalidates the same bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dtflow.services.assets import _observe
from dtflow.services.lora_family import LoraFamily, detect_lora_family, validate_converted_lora


# Exact architectures with confirmed converted-LoRA file signatures in #369/#371.
# This is not a claim that every backend supports every reference architecture.
INSPECTED_LORA_VERSIONS = (
    "flux1",
    "flux2_9b",
    "krea_2",
    "moss_image",
    "qwen_image_2.1",
    "sdxl_base_v0.9",
    "v1",
    "z_image",
)
_Q8P_SUFFIX = re.compile(r"(?i)(?:[_ .-]+(?:lora[_ .-]*)?)q8p\.ckpt$")
_F16_SUFFIX = re.compile(r"(?i)(?:[_ .-]+(?:lora[_ .-]*)?)(?:f16|fp16)\.ckpt$")


@dataclass(frozen=True, slots=True)
class LoraFileInspection:
    """Read-only local content identity, validity and independently detected family."""

    sha256: str
    size: int
    detected: LoraFamily


def suggested_format(filename: str) -> tuple[str, str]:
    """Return editable format and its source, without claiming tensor precision."""
    name = Path(filename).name
    if _Q8P_SUFFIX.search(name):
        return "Q8P", "filename_hint"
    if _F16_SUFFIX.search(name):
        return "F16", "filename_hint"
    return "F16", "converter_default"


def inspect_prepare_file(path: Path) -> LoraFileInspection:
    """Validate converted bytes and inspect their architecture without any provider call."""
    validate_converted_lora(path)
    stat, sha256 = _observe(path)
    return LoraFileInspection(sha256, stat.st_size, detect_lora_family(path))

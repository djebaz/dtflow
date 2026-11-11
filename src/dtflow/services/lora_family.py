"""Read-only family detection from converted Draw Things LoRA tensor headers.

Only SQLite tensor names and shape metadata are inspected, never weights. A
filename, current checkpoint, or generic hidden-size match is not proof of
family. Unknown and ambiguous structures remain explicitly unknown.

Z-Image, Krea 2, MOSS Image, FLUX.1, FLUX.2 Klein 9B, Qwen Image 2.1,
SD 1.x and SDXL Base signatures are grounded in owner-provided converted
CKPT reports and upstream Draw Things definitions (#369).
Unidentified or partial adapters stay unknown.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from contextlib import closing
from dataclasses import dataclass
from collections.abc import Iterable

from dtflow.core.models import ValidationError
from dtflow.services.lora_store import LoraStore
from pathlib import Path

MAX_TENSORS = 20_000
# Exact Draw Things architectures ``detect_lora_family`` can recognise from a file (keep in step with its matches).
DETECTABLE_FAMILIES = (
    "z_image",
    "krea_2",
    "moss_image",
    "flux1",
    "flux2_9b",
    "qwen_image_2.1",
    "v1",
    "sdxl_base_v0.9",
)
_SHAPE = re.compile(r"^\s*[\[(]\s*\d+(?:\s*,\s*\d+)*\s*[\])]\s*$")
_DIT_LORA_NAME = re.compile(r"^__dit__\[t-([^\]]+)\]__(?:up|down)__$")


@dataclass(frozen=True, slots=True)
class LoraFamily:
    """Exact Draw Things version supported by file evidence, or None if unknown."""

    family: str | None = None
    source: str = "unknown"


def _dimensions(value: object) -> tuple[int, ...]:
    """Decode shape metadata only, never the tensor's weight payload.

    Draw Things s4nnc stores dimensions as little-endian 32-bit integers in
    the `dim` column; other converted SQLite files may use JSON text.
    """
    if isinstance(value, bytes):
        if not value or len(value) > 64 or len(value) % 4:
            return ()
        values = struct.unpack(f"<{len(value) // 4}i", value)
        return tuple(d for d in values if d > 0)
    if isinstance(value, str) and _SHAPE.fullmatch(value):
        try:
            values = json.loads(value.replace("(", "[").replace(")", "]"))
            if isinstance(values, list) and all(type(d) is int and d > 0 for d in values):
                return tuple(values)
        except ValueError:
            pass
    return ()


def detect_lora_family(path: Path) -> LoraFamily:
    """Detect the exact Draw Things version from converted SQLite metadata.

    A family is returned only when independent architectural landmarks agree.
    Unknown or unsupported files remain unclassified; cloud dispatch then requires an explicit architecture.
    """
    path = Path(path)
    if not path.is_file():
        return LoraFamily()
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as db:
            db.execute("PRAGMA query_only=ON")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tensors'").fetchone():
                return LoraFamily()
            columns = {row[1] for row in db.execute("PRAGMA table_info(tensors)")}
            name_column = next((c for c in ("name", "key", "tensor_name") if c in columns), None)
            shape_column = next((c for c in ("dim", "shape", "dims", "dimensions") if c in columns), None)
            if name_column is None:
                return LoraFamily()
            # Names come from a fixed whitelist of schema columns, not from the DB.
            select = name_column + (", " + shape_column if shape_column else "")
            rows = db.execute(f"SELECT {select} FROM tensors LIMIT ?", (MAX_TENSORS + 1,)).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return LoraFamily()
    if not rows or len(rows) > MAX_TENSORS:
        return LoraFamily()

    names = [str(row[0]).lower() for row in rows if isinstance(row[0], str)]
    # A full model checkpoint may share its architecture's dimensions. Require
    # actual adapter up/down tensors, and use only their names and shapes.
    dit_modules = [match.group(1) for name in names if (match := _DIT_LORA_NAME.fullmatch(name)) is not None]

    def adapter_dimensions(prefix: str) -> set[int]:
        """Only dimensions of LoRA up/down tensors from this model namespace."""
        return {
            dimension
            for row in rows
            if len(row) > 1
            and isinstance(row[0], str)
            and row[0].lower().startswith(prefix)
            and row[0].lower().endswith(("__up__", "__down__"))
            for dimension in _dimensions(row[1])
        }

    dit_dimensions = adapter_dimensions("__dit__[t-")
    unet_dimensions = adapter_dimensions("__unet__[t-")
    text_dimensions = adapter_dimensions("__text_model__[t-")
    te2_dimensions = adapter_dimensions("__te2__text_model__[t-")
    fixed_unet_dimensions = adapter_dimensions("__unet_fixed__[t-")

    # Draw Things encodes DiT module names as "t-<module>-<layer>-...".
    # Do not require a specific last layer; a LoRA can adapt only a subset.
    # Some Z-Image adapters target feed-forward layers and the fixed
    # modulation path, but no attention projections. Both are valid layouts
    # in the upstream Z-Image model; require all four modulation groups when
    # attention modules are absent to keep this a structural signature.
    z_image = (
        3840 in dit_dimensions
        and 10240 in dit_dimensions
        and any(re.fullmatch(r"ffn-\d+-ffn_(?:gate|down|up)_proj-\d+-\d+", m) for m in dit_modules)
        and (
            any(re.fullmatch(r"(?:k|q|v|o)-\d+-\d+", m) for m in dit_modules)
            or all(any(re.fullmatch(rf"ada_ln_{index}-\d+-\d+", m) for m in dit_modules) for index in range(4))
        )
    )
    krea_2 = (
        6144 in dit_dimensions
        and 1536 in dit_dimensions
        and 16384 in dit_dimensions
        and any(re.fullmatch(r"to_(?:gate|k|q|v|out)-\d+-\d+", m) for m in dit_modules)
        and any(m.startswith(("text_fusion_", "textfusion_", "txtfusion_")) for m in dit_modules)
    )
    # The upstream MOSS Image LoRA blocks use these exact named projections,
    # with 3072 hidden and 8192 FFN width. A generic width match is insufficient.
    moss_image = (
        3072 in dit_dimensions
        and 8192 in dit_dimensions
        and all(
            any(re.fullmatch(rf"{module}-\d+-\d+", m) for m in dit_modules)
            for module in ("ffn_down_proj", "ffn_up_proj", "ffn_gate_proj", "x_o")
        )
    )
    # The converted FLUX.1 LoRA mapping uses 19 joint + 38 single blocks,
    # hidden width 3072 and 4x feed-forward width 12288. Require the complete
    # block counts to distinguish it from other 3072-wide dual-stream models,
    # including HunyuanVideo (20 + 40 blocks). Partial adapters remain unknown.
    flux_1 = (
        {3072, 12288} <= dit_dimensions
        and all(
            sum(module.startswith(f"{name}-") for module in dit_modules) == 2 * (19 + 38)
            for name in ("x_q", "x_k", "x_v", "x_o", "x_linear1", "x_out_proj")
        )
        and all(sum(module.startswith(f"x_ada_ln_{index}-") for module in dit_modules) == 2 * 19 for index in (3, 4, 5))
    )
    # FLUX.2 Klein 9B has 8 double-stream and 24 single-stream blocks,
    # hidden width 4096 and 3x feed-forward width 12288. The projection
    # counts distinguish it from FLUX.2 base (6144 wide, 8+48 blocks)
    # and Klein 4B (3072 wide, 5+20 blocks). Sparse adapters remain unknown.
    flux2_9b = (
        {4096, 12288} <= dit_dimensions
        and all(
            sum(module.startswith(f"{name}-") for module in dit_modules) == 2 * (8 + 24)
            for name in ("x_q", "x_k", "x_v", "x_o")
        )
        and all(
            sum(module.startswith(f"{name}-") for module in dit_modules) == 2 * 24 for name in ("x_w1", "x_w2", "x_w3")
        )
        and all(
            sum(module.startswith(f"{name}-") for module in dit_modules) == 2 * 8
            for name in ("x_gate_proj", "x_up_proj", "x_down_proj", "c_q", "c_o")
        )
    )
    # Qwen Image 2.1 has 32 transformer blocks, hidden width 4096 and
    # 3x feed-forward width 12288. The converted LoRA sample adapts these
    # five projections in all 32 blocks; a partial adapter stays unknown.
    # Qwen Image (original) uses a different 3072-wide, 60-layer model.
    qwen_image_2_1 = {4096, 12288} <= dit_dimensions and all(
        sum(module.startswith(f"{name}-") for module in dit_modules) == 2 * 32
        for name in ("x_q", "x_k", "x_v", "x_o", "ffn_down_proj")
    )
    # SD1.x and SDXL share U-Net channel widths (320/640/1280), and SDXL
    # can include a 768-wide CLIP-L text model. Require 768 also in U-Net LoRA
    # shapes (SD1.x cross-attention), plus absence of SD2/SDXL conditioning
    # widths and the SDXL second text-encoder namespace. Deliberately leaves
    # partial text-only or U-Net-only adapters as unknown.
    sd_1 = (
        768 in text_dimensions
        and {320, 640, 1280, 768} <= unet_dimensions
        and not ({1024, 2048} & unet_dimensions)
        and not ({1024, 1280} & text_dimensions)
        and not any(name.startswith("__te2__text_model__[") for name in names)
    )
    # SDXL Base has CLIP-L (768) and OpenCLIP-G (1280) adapters plus
    # a fixed UNet conditioning path (2048). Refiner uses different UNet
    # widths and no separate CLIP-L encoder. Only classify adapters with
    # all these independent landmarks; partial SDXL LoRAs stay unknown.
    sdxl = (
        768 in text_dimensions
        and 1280 in te2_dimensions
        and {640, 1280} <= unet_dimensions
        and 2048 in fixed_unet_dimensions
        and not ({384, 1536} & unet_dimensions)
    )
    matches = [
        family
        for family, matched in zip(
            DETECTABLE_FAMILIES,
            (z_image, krea_2, moss_image, flux_1, flux2_9b, qwen_image_2_1, sd_1, sdxl),
            strict=True,
        )
        if matched
    ]
    return LoraFamily(matches[0], "file") if len(matches) == 1 else LoraFamily()


def validate_converted_lora(path: Path) -> None:
    """Reject malformed converted LoRA containers before retention or cloud negotiation.

    Container validity is separate from architecture classification: a genuine
    sparse adapter can be structurally sound even when its family is Unknown.
    Only SQLite metadata and blob lengths are read, never tensor weight bytes.
    """
    path = Path(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as db:
            db.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tensors)")}
            name_column = next((name for name in ("name", "key", "tensor_name") if name in columns), None)
            shape_column = next((name for name in ("dim", "shape", "dims", "dimensions") if name in columns), None)
            if not name_column or not shape_column or "data" not in columns:
                raise ValidationError("Not a converted Draw Things LoRA: tensor metadata is missing.")
            # Names/shape columns are drawn exclusively from the fixed allowlists.
            rows = db.execute(
                f"SELECT {name_column}, {shape_column}, length(data), typeof(data) FROM tensors LIMIT ?",
                (MAX_TENSORS + 1,),
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ValidationError("Not a readable Draw Things LoRA checkpoint.") from exc
    if not rows or len(rows) > MAX_TENSORS:
        raise ValidationError("Converted LoRA has no usable tensor entries.")

    pairs: dict[str, set[str]] = {}
    for name, shape, length, storage in rows:
        if not isinstance(name, str):
            raise ValidationError("Converted LoRA has an invalid tensor entry.")
        suffix = "up" if name.endswith("__up__") else "down" if name.endswith("__down__") else None
        if suffix is None or not name.startswith("__"):
            continue
        if storage != "blob" or not isinstance(length, int) or length <= 0 or not _dimensions(shape):
            raise ValidationError("Converted LoRA has missing or invalid tensor data.")
        stem = name[: -len(suffix) - 4]
        pairs.setdefault(stem, set()).add(suffix)
    if not any(parts == {"up", "down"} for parts in pairs.values()):
        raise ValidationError("Converted LoRA has no complete up/down tensor pair.")


def retained_families(state: Path, identities: Iterable[tuple[str, int]]) -> dict[tuple[str, int], LoraFamily]:
    """Inspect legacy retained LoRAs locally, once per content identity; never upload.

    Used only to enrich read-only lists and preflight. Dispatch separately
    verifies the retained checksum before trusting these bytes for a request.
    Missing or unreadable files stay Unknown without hiding other receipts.
    """
    store = LoraStore(state)
    result: dict[tuple[str, int], LoraFamily] = {}
    for sha256, size in dict.fromkeys(identities):
        try:
            retained = store.find(sha256, size)
            detected = LoraFamily()
            if retained is not None and retained.path.stat().st_size == size:
                detected = detect_lora_family(retained.path)
                if detected.family is not None:
                    store.verify(retained)
            result[(sha256, size)] = detected
        except (OSError, ValidationError):
            result[(sha256, size)] = LoraFamily()
    return result

"""Local managed-library adapter around the standalone experimental q8p writer."""

import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from dtflow.core.models import ValidationError
from dtflow.services.lora_store import LoraStore
from dtflow.services.lora_naming import architecture_shortcode, managed_filename


async def confirmed_contents(state: Path, owner: str) -> frozenset[tuple[str, int]]:
    """Selected-key local confirmations only; never query the provider or run startup recovery."""
    from dtflow.persistence.assets import AssetRegistry

    registry = AssetRegistry(state, recover=False)
    await registry.open()
    try:
        return await registry.confirmed_lora_contents(owner)
    finally:
        await registry.aclose()


def library_formats(state: Path, entries: list[dict]) -> dict[tuple[str, int], str]:
    """Bounded tensor-header observations only; no weight decoding or whole-file hashing."""
    store = LoraStore(state)
    formats = {}
    for entry in entries:
        identity = (entry.get("sha256"), entry.get("size"))
        try:
            retained = store.find(*identity)
            if retained is None or not retained.path.is_file() or retained.path.stat().st_size != retained.size:
                continue
            with closing(sqlite3.connect(retained.path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                rows = db.execute("SELECT type, datatype FROM tensors LIMIT 20001").fetchall()
            if not rows or len(rows) > 20000:
                continue
            if all(kind == 1 and datatype == 0x20000 for kind, datatype in rows):
                formats[identity] = "F16"
            elif all(isinstance(kind, int) and datatype == 0x20000 for kind, datatype in rows):
                formats[identity] = "Q8P" if any(kind >> 32 == 0x8A1E8B for kind, _ in rows) else "Unknown"
        except (OSError, sqlite3.Error, ValidationError, TypeError):
            continue
    return formats


def available_quantizations(state: Path, entries: list[dict]) -> frozenset[tuple[str, int]]:
    """Only a verified, still-managed output disables quantization of its recorded source."""
    store = LoraStore(state)
    result = set()
    for entry in entries:
        sources = entry.get("quantized_from")
        if not isinstance(sources, list):
            continue
        try:
            retained = store.find(entry["sha256"], entry["size"])
            if retained is None:
                continue
            store.verify(retained)
        except (OSError, ValidationError):
            continue
        result.update(
            (item["sha256"], item["size"])
            for item in sources
            if isinstance(item, dict) and isinstance(item.get("sha256"), str) and isinstance(item.get("size"), int)
        )
    return frozenset(result)


def quantize_managed(state: Path, identity: tuple[str, int], metadata: dict, progress, *, on_start, cancel):
    """Verify the managed input, encode outside state, then retain a separate verified output."""
    from dtflow.services.lora_quantization import quantize_file

    store = LoraStore(state)
    source = store.find(*identity)
    if source is None:
        raise ValidationError("The selected managed LoRA is no longer available.")
    store.verify(source)
    if identity in available_quantizations(state, store.entries()):
        raise ValidationError("A verified q8p conversion of this LoRA is already in the managed library.")
    with tempfile.TemporaryDirectory(prefix="dtflow-q8p-") as directory:
        target = Path(directory) / "quantized.ckpt"
        report = quantize_file(source.path, target, progress, jobs=0, on_start=on_start, cancel=cancel)
        if cancel.is_set():
            from dtflow.services.lora_q8p_codec import QuantizationError

            raise QuantizationError("cancelled", "Cancelled before retaining the q8p output.")
        store.verify(source)
        family = report["input"]["family"]
        tag = architecture_shortcode(family)
        match = re.fullmatch(r"(.+)_([A-Za-z0-9]+)_lora_f16\.ckpt", source.name, flags=re.IGNORECASE)
        short = metadata.get("short_name") or (match[1] if match else source.path.stem)
        short = re.sub(r"[^A-Za-z0-9_]", "_", short)[:20] or "LoRA"
        name = managed_filename(short, tag, "Q8P")
        taken = {item.get("name", "").casefold() for item in store.entries()}
        if name.casefold() in taken or (store.directory / name).exists():
            # Keep the format suffix recognizable by the existing Prepare editor.
            short = short[:11] + "_" + report["output"]["sha256"][:8]
            name = managed_filename(short, tag, "Q8P")
        retained = store.retain(target, report["output"]["sha256"], report["output"]["file_bytes"], managed_name=name)
        final_name = re.fullmatch(r"(.+)_([A-Za-z0-9]+)_lora_q8p\.ckpt", retained.name, flags=re.IGNORECASE)
        if final_name:
            short = final_name[1]
        copied = {key: metadata[key] for key in ("family", "short_name", "trigger_words") if metadata.get(key)}
        copied["format_code"] = "Q8P"
        copied.update(family=family, short_name=short)
        store.record_quantization(retained, source, copied)
        return report, retained

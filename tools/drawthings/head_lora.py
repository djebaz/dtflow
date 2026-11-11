"""Summarize LoRA metadata and architectural clues without reading tensor weights.

Examples:
    python tools/drawthings/head_lora.py "C:\LoRAs\adapter.safetensors"
    python tools/drawthings/head_lora.py "C:\LoRAs\adapter_lora_f16.ckpt"
    python tools/drawthings/head_lora.py -d "C:\LoRAs" --json lora-headers.json
    python tools/drawthings/head_lora.py -d "C:\LoRAs" --outdir headers

Supports safetensors, Draw Things SQLite .ckpt and conversion-manifest JSON.
Output is a concise summary, not a dump of tensor names or shapes. Converted
CKPT files do not embed the model version. A matching JSON sidecar can provide
a *declared*, unverified version; it is never treated as file-derived evidence.
Read-only, non-recursive, standard-library only. Directory mode includes
conversion *_lora_f16.json manifests and custom_lora.json, not arbitrary JSON.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import struct

from inspect_lora_families import inspect as inspect_ckpt

MAX_HEADER_BYTES = 64 * 1024 * 1024
INPUT_EXTENSIONS = frozenset((".safetensors", ".ckpt", ".json"))
METADATA_KEYS = (
    "modelspec.architecture",
    "modelspec.title",
    "modelspec.trigger_phrase",
    "ss_base_model_version",
    "ss_output_name",
    "ss_network_module",
    "ss_network_dim",
    "ss_network_alpha",
    "trigger_word",
)


def safetensors_summary(path: Path) -> dict:
    """Read the JSON header only; omit individual tensor names and shapes."""
    with path.open("rb") as handle:
        length_bytes = handle.read(8)
        if len(length_bytes) != 8:
            raise ValueError("Missing safetensors header length.")
        length = struct.unpack("<Q", length_bytes)[0]
        if not 2 <= length <= MAX_HEADER_BYTES or length > path.stat().st_size - 8:
            raise ValueError("Invalid or excessively large safetensors header.")
        header = json.loads(handle.read(length).decode("utf-8"))

    if not isinstance(header, dict):
        raise ValueError("Safetensors header must be a JSON object.")
    raw_metadata = header.get("__metadata__")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    # Training metadata can contain huge embedded captions, datasets and tags.
    # Retain only small, directly useful identification/configuration fields.
    useful_metadata = {
        key: metadata[key]
        for key in METADATA_KEYS
        if key in metadata and isinstance(metadata[key], (str, int, float, bool))
        and len(str(metadata[key])) <= 240
    }

    counts: Counter[str] = Counter()
    sizes: Counter[int] = Counter()
    for name, details in header.items():
        if name == "__metadata__":
            continue
        if name.startswith("lora_unet_"):
            group = "unet"
        elif name.startswith(("lora_te1_", "lora_te_")):
            group = "text_encoder_1"
        elif name.startswith("lora_te2_"):
            group = "text_encoder_2"
        elif name.startswith(("lora_transformer_", "transformer.")):
            group = "transformer"
        elif name.startswith("text_encoder"):
            group = "text_encoder"
        else:
            group = "other"
        counts[group] += 1
        if isinstance(details, dict) and isinstance(details.get("shape"), list):
            sizes.update(dim for dim in details["shape"] if type(dim) is int and dim > 0)

    return {
        "format": "safetensors",
        "tensor_count": sum(counts.values()),
        "tensor_groups": dict(counts),
        "common_dimensions": [dim for dim, _ in sizes.most_common(10)],
        "metadata": useful_metadata,
    }


def ckpt_summary(path: Path) -> dict:
    """Reuse the existing family inspector; retain only meaningful evidence."""
    report = inspect_ckpt(path, sample_count=0)
    if "error" in report:
        raise ValueError(report["error"])
    family = report["current_detector_result"]
    result = {
        "format": "draw_things_ckpt",
        "tensor_count": report["lora_tensor_count"],
        "detected_family": None if family == "unknown" else family,
        "family_evidence": "ckpt_signature" if family != "unknown" else "unknown",
        "namespaces": report["namespaces"],
        "dimensions_by_namespace": report["adapter_dimensions_by_namespace"],
    }
    if report["dit_lora_tensor_count"]:
        result["module_groups"] = dict(report["module_groups"][:12])
    # These markers help recognize SDXL-like layouts without pretending that
    # SDXL Base/Refiner is already supported by the production classifier.
    markers = {
        "second_text_encoder": "__te2__text_model__" in report["namespaces"],
        "fixed_unet": "__unet_fixed__" in report["namespaces"],
    }
    positive = [key for key, present in markers.items() if present]
    if positive:
        result["structural_clues_not_classification"] = positive
    return result


def manifest_summary(payload: object) -> dict:
    """Extract useful declared fields without claiming source-file verification."""
    if isinstance(payload, dict) and "model_family" in payload:
        fields = ("name", "input", "output", "model_family", "scale",
                  "trigger_word", "sha256", "input_sha256")
        result = {key: payload[key] for key in fields if key in payload}
        return {"format": "conversion_manifest", "declaration": result}
    if isinstance(payload, list):
        # Draw Things custom_lora.json lists explicit version declarations.
        return {
            "format": "lora_registry",
            "entry_count": len(payload),
            "entries": [
                {key: value for key in ("name", "file", "version", "prefix")
                 if (value := entry.get(key)) is not None}
                for entry in payload if isinstance(entry, dict)
            ],
        }
    if isinstance(payload, dict):
        fields = ("name", "file", "version", "prefix", "model_family", "output")
        return {"format": "json_metadata",
                "fields": {key: payload[key] for key in fields if key in payload}}
    raise ValueError("Unsupported JSON metadata document.")


def extract(path: Path) -> dict:
    """Summarize one file and attach matching sidecar declarations when present."""
    kind = path.suffix.lower()
    if kind == ".safetensors":
        summary = safetensors_summary(path)
    elif kind == ".ckpt":
        summary = ckpt_summary(path)
    elif kind == ".json":
        summary = manifest_summary(json.loads(path.read_text(encoding="utf-8")))
    else:
        raise ValueError("Supported formats: .safetensors, .ckpt, .json.")

    result = {"file": path.name, "size_bytes": path.stat().st_size, **summary}
    if kind != ".json":
        sidecar = path.with_suffix(".json")
        if sidecar.is_file():
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                name_matches = payload.get("output") == path.name if "output" in payload else None
                size_matches = (
                    payload["size_bytes"] == path.stat().st_size
                    if type(payload.get("size_bytes")) is int else None
                )
                result["conversion_manifest"] = {
                    "file": sidecar.name,
                    "declared_family": payload.get("model_family"),
                    "filename_matches": name_matches,
                    "size_matches": size_matches,
                    "sha256_verified": False,
                }
    return result


def main() -> int:
    """Export concise JSON summaries for files or a non-recursive directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="Files to inspect.")
    parser.add_argument("-d", "--directory", type=Path, help="Scan a directory (non-recursive).")
    parser.add_argument("--outdir", type=Path, help="Write one JSON summary per input.")
    parser.add_argument("--json", type=Path, help="Write one combined JSON report.")
    args = parser.parse_args()

    paths = [item.expanduser() for item in args.files]
    if args.directory is not None:
        folder = args.directory.expanduser()
        if not folder.is_dir():
            parser.error(f"Not a directory: {folder}")
        try:
            candidates = sorted(folder.iterdir(), key=lambda item: item.name.casefold())
            for item in candidates:
                if not item.is_file() or item.suffix.lower() not in INPUT_EXTENSIONS:
                    continue
                if item.suffix.lower() == ".json":
                    # Skip arbitrary JSON files, including previous reports.
                    # Conversion manifests can also be passed explicitly.
                    if item.name.lower() != "custom_lora.json" and not item.stem.lower().endswith("_lora_f16"):
                        continue
                    if any(item.with_suffix(extension).is_file()
                           for extension in (".safetensors", ".ckpt")):
                        continue
                paths.append(item)
        except OSError as exc:
            parser.error(f"Could not scan directory: {exc}")

    if not paths:
        parser.error("Provide files or a directory containing supported files with -d.")

    results: list[dict] = []
    seen: set[Path] = set()
    failed = False
    for path in paths:
        try:
            canonical = path.resolve()
            if canonical in seen:
                continue
            seen.add(canonical)
            results.append(extract(path))
        except (OSError, ValueError, UnicodeError, sqlite3.Error, struct.error) as exc:
            failed = True
            results.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})

    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)
        for result in results:
            target = args.outdir / (result["file"] + ".header.json")
            target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {len(results)} summaries to {args.outdir}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {len(results)} summaries to {args.json}")
    if not args.outdir and not args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

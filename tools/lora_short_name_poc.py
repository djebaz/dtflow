#!/usr/bin/env python3
"""Explore LoRA short-name suggestions using DTFlow's shared naming service."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Allow running this development tool directly from a repository checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dtflow.services.lora_naming import (  # noqa: E402
    NamingRequest,
    allocate_batch_names,
    suggest_short_name,
)


def _basename(filename: str) -> str:
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def _is_probably_lora(entry: dict[str, Any]) -> bool:
    filename = str(entry.get("file", "")).casefold()
    if "lora" in filename:
        return True
    if entry.get("module_groups") and int(entry.get("tensor_count") or 0) > 0:
        return True
    if entry.get("format") == "safetensors" and int(entry.get("tensor_count") or 0) > 0:
        return True
    return False


def _model_family(entry: dict[str, Any]) -> str | None:
    """Use reported architecture evidence, never infer compatibility from a filename."""
    family = entry.get("detected_family")
    if not isinstance(family, str) or not family.strip():
        metadata = entry.get("metadata")
        family = metadata.get("ss_base_model_version") if isinstance(metadata, dict) else None
    if not isinstance(family, str) or not family.strip():
        return None
    aliases = {"krea2": "krea_2"}
    normalized = family.strip().casefold()
    return aliases.get(normalized, normalized)


def _quantization(entry: dict[str, Any]) -> tuple[str | None, str]:
    """Identify the LoRA format from the suffix, or use the converter's CKPT default."""
    filename = _basename(str(entry.get("file", "")))
    stem = Path(filename).stem
    matches = re.findall(r"(?i)(?:^|[_\s.-])(?:lora[_\s.-]+)?(f16|fp16|q8p)(?=$|[_\s.-])", stem)
    if matches:
        code = matches[-1].upper()
        return ("F16" if code == "FP16" else code), "filename_hint"
    if filename.casefold().endswith(".ckpt"):
        return "F16", "converter_default"
    return None, "unknown"




def analyze(entries: list[dict[str, Any]], *, include_other: bool = False) -> list[dict[str, Any]]:
    """Describe filename suggestions without reading LoRA bytes or calling DT+."""
    rows: list[dict[str, Any]] = []
    requests: list[NamingRequest] = []
    for entry in entries:
        filename = entry.get("file")
        if not isinstance(filename, str) or not filename:
            continue
        if not include_other and not entry.get("_manual_name") and not _is_probably_lora(entry):
            continue
        suggestion = suggest_short_name(filename)
        family = _model_family(entry)
        quantization, source = _quantization(entry)
        rows.append({
            "source_file": _basename(filename),
            "suggestion": suggestion.name,
            "alternatives": list(suggestion.alternatives),
            "confidence": suggestion.confidence,
            "needs_review": suggestion.needs_review,
            "reason": suggestion.reason,
            "detected_family_evidence": entry.get("detected_family"),
            "model_family": family,
            "quantization": quantization,
            "quantization_source": source,
            "likely_lora": _is_probably_lora(entry),
        })
        requests.append(NamingRequest(
            short_name=suggestion.name,
            exact_architecture=family,
            format_code=quantization,
            alternatives=suggestion.alternatives,
        ))

    for row, result in zip(rows, allocate_batch_names(requests), strict=True):
        if result.short_name != row["suggestion"] and result.conflict is None:
            row["suggestion"] = result.short_name
            row["needs_review"] = True
            row["reason"] = "Same architecture and format: review the numbered suggestion."
            suffix = re.search(r"_(\d+)$", result.short_name or "")
            if suffix:
                row["collision_suffix"] = int(suffix.group(1))
        if row["model_family"] is None or row["quantization"] is None:
            row["collision_scope_incomplete"] = True
            row["needs_review"] = True
        if result.conflict and result.conflict not in {"missing_short_name", "missing_architecture", "missing_format"}:
            row["allocation_issue"] = result.conflict
            row["needs_review"] = True
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore optional LoRA short-name suggestions (no file writes).")
    parser.add_argument("--catalog", type=Path, help="JSON report containing a list of records with a file field")
    parser.add_argument("--name", action="append", default=[], help="Additional source filename (repeatable)")
    parser.add_argument("--include-other", action="store_true", help="Include likely non-LoRA entries")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of the compact table")
    args = parser.parse_args()
    if args.catalog is None and not args.name:
        parser.error("pass --catalog and/or at least one --name")
    entries: list[dict[str, Any]] = []
    if args.catalog is not None:
        with args.catalog.open(encoding="utf-8") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, list):
            parser.error("catalog must contain a JSON list of records")
        entries.extend(entry for entry in loaded if isinstance(entry, dict))
    entries.extend({"file": name, "_manual_name": True} for name in args.name)
    rows = analyze(entries, include_other=args.include_other)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=True))
        return
    print(f"{'Suggestion':20}  {'Confidence':10} {'Review':6} Source")
    print("-" * 100)
    for row in rows:
        name = row["suggestion"] or "(no suggestion)"
        mark = "yes" if row["needs_review"] else ""
        print(f"{name:20}  {row['confidence']:10} {mark:6} {row['source_file']}")


if __name__ == "__main__":
    main()

"""Inspect converted Draw Things LoRA .ckpt metadata to diagnose family detection.

Usage (PowerShell):
    python tools/drawthings/inspect_lora_families.py "C:\LoRAs\krea.ckpt" "C:\LoRAs\zimage.ckpt" --json lora-report.json

Requires only Python's standard library. Opens each SQLite file read-only, reads
tensor names and dimension metadata, and NEVER selects or hashes tensor data.
Reports exactly which provisional PR #371 fingerprint conditions are absent.
The report contains sampled tensor names and dimensions, but no weight bytes,
credentials, prompts, absolute file paths, or full-file hashes. Only --json
writes a local output file. It does not identify a model family independently.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import struct
import sys

MAX_TENSORS = 20_000  # Match the current DTFlow family detector's limit.
SHAPE_TEXT = re.compile(r"^\s*[\[(]\s*\d+(?:\s*,\s*\d+)*\s*[\])]\s*$")
BLOCK = re.compile(r"(?:^|[._/\[-])(?:layers?|blocks)[._/\[-](\d+)(?:[._/\]-]|$)")
OTHER_BLOCK = re.compile(
    r"(?:^|[._/\[-])"
    r"(?:transformer_blocks?|single_transformer_blocks?|double_blocks?|joint_blocks?|"
    r"single_blocks?|double_blocks?|layers?|blocks?)"
    r"[._/\[-](\d+)(?:[._/\]-]|$)"
)
MARKERS = (
    "ada_ln",
    "ffn_",
    "proj",
    "txtfusion",
    "textfusion",
    "text_fusion",
    "attn",
    "attention",
    "lora",
    "transformer",
    "diffusion_model",
    "single_blocks",
    "double_blocks",
)


def dimensions(value: object) -> tuple[int, ...]:
    """Replicate PR #371's dimension parser for a comparable diagnosis."""
    if isinstance(value, bytes):
        if not value or len(value) > 64 or len(value) % 4:
            return ()
        values = struct.unpack(f"<{len(value) // 4}i", value)
        return tuple(d for d in values if d > 0)
    if isinstance(value, str) and SHAPE_TEXT.fullmatch(value):
        try:
            parsed = json.loads(value.replace("(", "[").replace(")", "]"))
            if isinstance(parsed, list) and all(type(d) is int and d > 0 for d in parsed):
                return tuple(parsed)
        except ValueError:
            pass
    return ()


def shape_storage(value: object) -> str:
    """Describe the raw SQLite dimension representation without tensor data."""
    if value is None:
        return "null"
    if isinstance(value, bytes):
        return f"blob:{len(value)} bytes"
    if isinstance(value, str):
        return "text"
    return type(value).__name__


def sample_dimension(value: object) -> str:
    """Show a small dimension-column prefix to diagnose binary/text decoding."""
    if isinstance(value, bytes):
        return value[:32].hex()
    if isinstance(value, str):
        return value[:64]
    return str(value)[:64]


def inspect(path: Path, sample_count: int) -> dict:
    """Read metadata only and evaluate the current Krea 2 and Z-Image gates."""
    if not path.is_file():
        raise ValueError("File not found or not a regular file.")

    report: dict = {"file": path.name, "size_bytes": path.stat().st_size}
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=2.0) as db:
        db.execute("PRAGMA query_only=ON")
        report["tables"] = [
            item[0]
            for item in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        columns = [
            {"name": row[1], "type": row[2]}
            for row in db.execute("PRAGMA table_info(tensors)")
        ]
        report["tensor_columns"] = columns
        column_names = {item["name"] for item in columns}
        name_column = next(
            (item for item in ("name", "key", "tensor_name") if item in column_names),
            None,
        )
        dim_column = next(
            (item for item in ("dim", "shape", "dims", "dimensions") if item in column_names),
            None,
        )
        report["name_column"] = name_column
        report["dimension_column"] = dim_column
        if name_column is None:
            report["error"] = "No supported tensor-name column; classifier returns Unknown."
            return report

        # Whitelisted column names only. Never select, read, hash, or preview data.
        select = name_column + (", " + dim_column if dim_column else "")
        rows = db.execute(
            f"SELECT {select} FROM tensors LIMIT ?", (MAX_TENSORS + 1,)
        ).fetchall()

    report["tensor_rows_scanned"] = len(rows)
    report["limit_exceeded"] = len(rows) > MAX_TENSORS
    if len(rows) > MAX_TENSORS:
        report["error"] = (
            f"More than {MAX_TENSORS} tensors; the current classifier returns Unknown."
        )
        return report

    names: list[str] = []
    size_values: Counter[int] = Counter()
    storage_types: Counter[str] = Counter()
    prefixes: Counter[str] = Counter()
    current_blocks: set[int] = set()
    broader_blocks: set[int] = set()
    samples: list[dict] = []
    interesting_samples: list[dict] = []
    marker_counts: Counter[str] = Counter()

    for row in rows:
        if not isinstance(row[0], str):
            continue
        name = row[0]
        lower = name.lower()
        names.append(lower)
        prefixes[name.split(".", 1)[0].split("/", 1)[0]] += 1
        for marker in MARKERS:
            if marker in lower:
                marker_counts[marker] += 1
        current_blocks.update(int(m.group(1)) for m in BLOCK.finditer(lower))
        broader_blocks.update(int(m.group(1)) for m in OTHER_BLOCK.finditer(lower))

        raw_shape = row[1] if dim_column else None
        parsed_shape = dimensions(raw_shape)
        storage_types[shape_storage(raw_shape)] += 1
        size_values.update(parsed_shape)
        example = {
            "tensor_name": name,
            "parsed_dimensions": list(parsed_shape),
            "dim_storage": shape_storage(raw_shape),
            "dim_prefix": sample_dimension(raw_shape),
        }
        if len(samples) < sample_count:
            samples.append(example)
        if (
            len(interesting_samples) < sample_count
            and (parsed_shape or any(marker in lower for marker in MARKERS))
            and example not in samples
        ):
            interesting_samples.append(example)

    checks = {
        "z_image": {
            "block_29": 29 in current_blocks,
            "dimension_3840": 3840 in size_values,
            "ada_ln_name": marker_counts["ada_ln"] > 0,
            "ffn_and_proj_same_name": any(
                "ffn_" in name and "proj" in name for name in names
            ),
        },
        "krea_2": {
            "block_27": 27 in current_blocks,
            "dimension_6144": 6144 in size_values,
            "text_fusion_name": any(
                marker in name
                for name in names
                for marker in ("txtfusion", "textfusion", "text_fusion")
            ),
            "attention_name": any(
                "attn" in name or "attention" in name for name in names
            ),
        },
    }
    hits = [family for family, gates in checks.items() if all(gates.values())]
    report.update(
        {
            "name_count": len(names),
            "name_prefixes": prefixes.most_common(15),
            "name_marker_counts": dict(marker_counts),
            "current_block_indices": sorted(current_blocks),
            "broader_block_indices": sorted(broader_blocks),
            "dimension_storage": dict(storage_types),
            "most_common_dimensions": size_values.most_common(20),
            "sample_tensors": samples,
            "additional_tensors": interesting_samples,
            "current_fingerprint_checks": checks,
            "current_detector_result": hits[0] if len(hits) == 1 else "unknown",
        }
    )
    if not names:
        report["error"] = "No readable tensor names; the current classifier returns Unknown."
    return report


def print_report(report: dict) -> None:
    """Print concise per-file evidence suitable for sharing without model bytes."""
    print(f"\n== {report['file']} ({report.get('size_bytes', 0):,} bytes)")
    print("Tables:", report.get("tables", []))
    print("Tensor columns:", report.get("tensor_columns", []))
    print("Columns used:", report.get("name_column"), "/", report.get("dimension_column"))
    print("Rows scanned:", report.get("tensor_rows_scanned", 0))
    if "error" in report:
        print("Limitation:", report["error"])
        return

    print("Name prefixes:", report["name_prefixes"])
    print("Marker counts:", report["name_marker_counts"])
    print("Current block indices:", report["current_block_indices"])
    print("Broader block indices:", report["broader_block_indices"])
    print("Dimension storage:", report["dimension_storage"])
    print("Frequent dimensions:", report["most_common_dimensions"])
    print("Current PR #371 fingerprint gates (False indicates missing evidence):")
    for family, gates in report["current_fingerprint_checks"].items():
        print(f"  {family}: " + ", ".join(f"{key}={value}" for key, value in gates.items()))
    print("Current fingerprint result:", report["current_detector_result"])
    for title, key in (
        ("First tensor examples", "sample_tensors"),
        ("Other informative tensor examples", "additional_tensors"),
    ):
        print(f"{title}:")
        for item in report[key]:
            print(
                f"  {item['tensor_name']} | dimensions={item['parsed_dimensions']} "
                f"| raw={item['dim_storage']}:{item['dim_prefix']}"
            )


def main() -> int:
    """Inspect every file locally; optionally save one consolidated JSON report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="Converted SQLite .ckpt files.")
    parser.add_argument(
        "--sample",
        type=int,
        default=12,
        help="Maximum first and additional tensor examples per file (default: 12).",
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="Write the report as JSON for sharing. Never includes weight bytes.",
    )
    args = parser.parse_args()
    if not 0 <= args.sample <= 100:
        parser.error("--sample must be between 0 and 100.")

    reports = []
    failed = False
    for path in args.files:
        try:
            report = inspect(path.expanduser(), args.sample)
            failed |= "error" in report
        except (OSError, sqlite3.Error, ValueError, struct.error) as exc:
            report = {"file": path.name, "error": f"Inspection failed: {exc.__class__.__name__}"}
            failed = True
        reports.append(report)
        print_report(report)

    if args.json:
        try:
            args.json.write_text(
                json.dumps(reports, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            print(f"Could not write JSON report: {exc.__class__.__name__}", file=sys.stderr)
            return 1
        print(f"\nSaved report: {args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Export LoRA file headers and conversion metadata as JSON, without reading weights.

Examples:
    python tools/drawthings/head_lora.py "C:\LoRAs\adapter.safetensors"
    python tools/drawthings/head_lora.py "C:\LoRAs\adapter_lora_f16.ckpt" --outdir headers
    python tools/drawthings/head_lora.py -d "C:\LoRAs" --json lora-headers.json

Supports original safetensors headers, converted Draw Things SQLite .ckpt
tensor names/shapes, and JSON conversion manifests. An adjacent .json file
is attached as *unverified* sidecar evidence; it is not CKPT metadata.
No model tensor data is read, decoded, hashed, or uploaded.

Standard library only. Directory scanning is non-recursive.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import struct
import sys

MAX_HEADER_BYTES = 64 * 1024 * 1024
MAX_TENSORS = 100_000
INPUT_EXTENSIONS = frozenset((".safetensors", ".ckpt", ".json"))


def safetensors_header(path: Path) -> dict:
    """Read the length-prefixed JSON header, never the following tensor bytes."""
    with path.open("rb") as handle:
        raw_length = handle.read(8)
        if len(raw_length) != 8:
            raise ValueError("Missing safetensors header length.")
        header_length = struct.unpack("<Q", raw_length)[0]
        if not 2 <= header_length <= MAX_HEADER_BYTES:
            raise ValueError("Invalid or excessively large safetensors header.")
        if header_length > path.stat().st_size - 8:
            raise ValueError("Safetensors header extends beyond the file.")
        header = json.loads(handle.read(header_length).decode("utf-8"))

    if not isinstance(header, dict):
        raise ValueError("Safetensors header must be a JSON object.")
    keys = [key for key in header if key != "__metadata__"]
    shapes = {
        key: {"shape": header[key].get("shape"), "dtype": header[key].get("dtype")}
        for key in keys
        if isinstance(header[key], dict)
    }
    metadata = header.get("__metadata__")
    return {
        "source_format": "safetensors",
        "__metadata__": metadata if isinstance(metadata, dict) else {},
        "__tensor_keys__": keys,
        "__tensor_shapes__": shapes,
        "tensor_count": len(keys),
    }


def _dimensions(value: object) -> list[int]:
    """Decode SQLite shape metadata without reading the data column."""
    if isinstance(value, bytes):
        if not value or len(value) > 64 or len(value) % 4:
            return []
        return [item for item in struct.unpack(f"<{len(value) // 4}i", value) if item > 0]
    if isinstance(value, str):
        try:
            parsed = json.loads(value.replace("(", "[").replace(")", "]"))
        except ValueError:
            return []
        if isinstance(parsed, list) and all(type(item) is int and item > 0 for item in parsed):
            return parsed
    return []


def ckpt_header(path: Path) -> dict:
    """Read Draw Things SQLite tensor names and shapes in read-only mode."""
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0) as db:
        db.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in db.execute("PRAGMA table_info(tensors)")}
        name_col = next((name for name in ("name", "key", "tensor_name") if name in columns), None)
        shape_col = next((name for name in ("dim", "shape", "dims", "dimensions") if name in columns), None)
        if name_col is None:
            raise ValueError("No tensors table with a supported name column.")
        # Both column names are fixed allowlisted constants; never select data.
        select = name_col + (", " + shape_col if shape_col else "")
        rows = db.execute(f"SELECT {select} FROM tensors LIMIT ?", (MAX_TENSORS + 1,)).fetchall()

    if len(rows) > MAX_TENSORS:
        raise ValueError(f"Checkpoint exceeds the {MAX_TENSORS} tensor metadata limit.")
    names = [row[0] for row in rows if isinstance(row[0], str)]
    shapes = {
        row[0]: _dimensions(row[1])
        for row in rows
        if shape_col and isinstance(row[0], str)
    }
    return {
        "source_format": "draw_things_ckpt",
        "__metadata__": {},
        "metadata_note": "The SQLite tensors table does not store the detected model version.",
        "__tensor_keys__": names,
        "__tensor_shapes__": shapes,
        "tensor_count": len(names),
    }


def json_header(path: Path) -> dict:
    """Expose an existing JSON manifest or registry without interpreting its claims."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"source_format": "json", "content": payload}


def extract(path: Path) -> dict:
    """Export metadata from one source and optionally attach its JSON sidecar."""
    kind = path.suffix.lower()
    if kind == ".safetensors":
        result = safetensors_header(path)
    elif kind == ".ckpt":
        result = ckpt_header(path)
    elif kind == ".json":
        result = json_header(path)
    else:
        raise ValueError("Supported formats: .safetensors, .ckpt, .json.")

    result["file"] = path.name
    result["size_bytes"] = path.stat().st_size
    if kind != ".json":
        sidecar = path.with_suffix(".json")
        if sidecar.is_file():
            manifest = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                expected_name = manifest.get("output")
                expected_size = manifest.get("size_bytes")
                result["sidecar"] = {
                    "file": sidecar.name,
                    "content": manifest,
                    "output_name_matches": expected_name == path.name if expected_name is not None else None,
                    "file_size_matches": expected_size == path.stat().st_size if expected_size is not None else None,
                    "sha256_verified": False,
                    "note": "Separate JSON evidence, not embedded metadata; no weight hash was computed.",
                }
    return result


def main() -> int:
    """Export headers from explicit paths or every supported file in a directory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="Files to inspect.")
    parser.add_argument("-d", "--directory", type=Path, help="Scan a directory, non-recursively.")
    parser.add_argument("--outdir", type=Path, help="Write one JSON header per input file.")
    parser.add_argument("--json", type=Path, help="Write a combined JSON report.")
    args = parser.parse_args()

    paths = [path.expanduser() for path in args.files]
    if args.directory is not None:
        folder = args.directory.expanduser()
        if not folder.is_dir():
            parser.error(f"Not a directory: {folder}")
        try:
            candidates = sorted(folder.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            parser.error(f"Could not list directory: {exc}")
        for item in candidates:
            if not item.is_file() or item.suffix.lower() not in INPUT_EXTENSIONS:
                continue
            # Matching JSON sidecars are included with the model file instead.
            if item.suffix.lower() == ".json" and any(
                item.with_suffix(extension).is_file()
                for extension in (".safetensors", ".ckpt")
            ):
                continue
            paths.append(item)
    if not paths:
        parser.error("Provide files or -d with supported files.")

    results: list[dict] = []
    seen: set[Path] = set()
    failed = False
    for path in paths:
        try:
            canonical = path.resolve()
            if canonical in seen:
                continue
            seen.add(canonical)
            result = extract(path)
        except (OSError, ValueError, UnicodeError, sqlite3.Error, struct.error) as exc:
            failed = True
            result = {"file": path.name, "error": f"{type(exc).__name__}: {exc}"}
        results.append(result)

    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)
        for result in results:
            target = args.outdir / (result["file"] + ".header.json")
            target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {len(results)} header reports to {args.outdir}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {len(results)} entries to {args.json}")
    if not args.outdir and not args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

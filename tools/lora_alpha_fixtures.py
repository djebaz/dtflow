"""Create a convertible malformed-alpha fixture from a real LoRA safetensors file.

Copy every tensor unchanged except one existing alpha tensor. The output keeps
the original model-family tensor names and weights, so it can exercise the full
converter. Never modify the input or overwrite an existing output.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
DTYPE_BYTES = {"F16": 2, "BF16": 2, "F32": 4, "F64": 8, "I8": 1, "U8": 1,
               "I16": 2, "U16": 2, "I32": 4, "U32": 4, "I64": 8, "U64": 8, "BOOL": 1}


def modify_alpha(source: Path, output: Path, mode: str, alpha_key: str | None) -> str:
    """Rewrite one alpha tensor while copying all other tensor bytes exactly."""
    if source.resolve() == output.resolve():
        raise ValueError("Output must differ from input")
    with source.open("rb") as reader:
        header_size_bytes = reader.read(8)
        if len(header_size_bytes) != 8:
            raise ValueError("Invalid safetensors header")
        header_size = struct.unpack("<Q", header_size_bytes)[0]
        if header_size > 100 * 1024 * 1024:
            raise ValueError("Safetensors header is too large")
        header = json.loads(reader.read(header_size))
        if not isinstance(header, dict):
            raise ValueError("Invalid safetensors header")
        original_data_start = 8 + header_size
        keys = [key for key in header if key.endswith((".alpha", "::alpha"))]
        if alpha_key is None:
            if not keys:
                raise ValueError("No alpha tensor found; supply a LoRA with alpha tensors")
            alpha_key = keys[0]
        elif alpha_key not in keys:
            raise ValueError(f"Alpha key not found: {alpha_key}")
        original = header[alpha_key]
        if original["dtype"] not in ("F16", "BF16", "F32"):
            raise ValueError("Selected alpha must be F16, BF16, or F32")
        if mode == "empty":
            replacement_shape, replacement = [0], b""
        elif mode == "multiple":
            replacement_shape, replacement = [2], struct.pack("<ff", 1.0, 2.0)
        elif mode == "scalar":
            replacement_shape, replacement = [], struct.pack("<f", 2.0)
        else:
            replacement_shape, replacement = [1], struct.pack("<f", 2.0)

        # Rebuild offsets in original data order, preserving every non-target tensor.
        entries = sorted(
            ((key, value) for key, value in header.items() if key != "__metadata__"),
            key=lambda pair: pair[1]["data_offsets"][0],
        )
        cursor = 0
        operations = []
        source_size = source.stat().st_size
        for key, entry in entries:
            start, end = entry["data_offsets"]
            dtype = entry["dtype"]
            shape = entry["shape"]
            if dtype not in DTYPE_BYTES or not isinstance(shape, list):
                raise ValueError(f"Unsupported tensor metadata: {key}")
            if start < 0 or end < start or original_data_start + end > source_size:
                raise ValueError(f"Invalid offsets for {key}")
            if (end - start) != math.prod(shape) * DTYPE_BYTES[dtype]:
                raise ValueError(f"Invalid tensor length for {key}")
            if key == alpha_key:
                entry["dtype"] = "F32"
                entry["shape"] = replacement_shape
                length = len(replacement)
                operations.append((None, replacement))
            else:
                length = end - start
                operations.append(((start, length), None))
            entry["data_offsets"] = [cursor, cursor + length]
            cursor += length

        encoded = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as writer:
            try:
                writer.write(struct.pack("<Q", len(encoded)))
                writer.write(encoded)
                for segment, data in operations:
                    if segment is None:
                        writer.write(data)
                    else:
                        start, remaining = segment
                        reader.seek(original_data_start + start)
                        while remaining:
                            block = reader.read(min(CHUNK_SIZE, remaining))
                            if not block:
                                raise ValueError("Unexpected end of source file")
                            writer.write(block)
                            remaining -= len(block)
            except BaseException:
                writer.close()
                output.unlink(missing_ok=True)
                raise
    return alpha_key


def main() -> None:
    """Write a modified full-size LoRA for converter acceptance or refusal checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Original .safetensors LoRA")
    parser.add_argument("--mode", choices=("empty", "multiple", "scalar", "singleton"), default="empty")
    parser.add_argument("--alpha-key", help="Exact alpha tensor name; defaults to first found")
    parser.add_argument("--output", type=Path, help="Destination; defaults beside input with mode suffix")
    args = parser.parse_args()
    output = args.output or args.input.with_name(f"{args.input.stem}_alpha_{args.mode}.safetensors")
    try:
        key = modify_alpha(args.input, output, args.mode, args.alpha_key)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Created {output} (modified tensor: {key}, mode: {args.mode})")


if __name__ == "__main__":
    main()

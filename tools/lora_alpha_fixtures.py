"""Generate tiny safetensors fixtures for malformed LoRA alpha values.

These fixtures exercise alpha parsing, not full model-family conversion.
No provider, model download, or test runner is involved.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def write_fixture(path: Path, alpha_shape: list[int], alpha_values: list[float]) -> None:
    """Write a valid safetensors container with a LoRA pair and one alpha tensor."""
    import array

    tensors = [
        ("example.lora_down.weight", [2, 2], [1.0, 0.0, 0.0, 1.0]),
        ("example.lora_up.weight", [2, 2], [1.0, 0.0, 0.0, 1.0]),
        ("example.alpha", alpha_shape, alpha_values),
    ]
    header: dict[str, dict] = {}
    payload = bytearray()
    for name, shape, values in tensors:
        raw = array.array("f", values)
        if raw.itemsize != 4:
            raise RuntimeError("Expected 32-bit floats")
        import sys

        if sys.byteorder != "little":
            raw.byteswap()
        start = len(payload)
        payload.extend(raw.tobytes())
        header[name] = {"dtype": "F32", "shape": shape, "data_offsets": [start, len(payload)]}
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + payload)


def main() -> None:
    """Create deterministic alpha fixtures in an explicitly selected directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("alpha-fixtures"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = {
        "alpha_empty.safetensors": ([0], []),
        "alpha_multiple.safetensors": ([2], [1.0, 2.0]),
        "alpha_scalar.safetensors": ([], [2.0]),
        "alpha_singleton.safetensors": ([1], [2.0]),
    }
    for filename, (shape, values) in cases.items():
        path = args.output / filename
        if path.exists():
            parser.error(f"Refusing to overwrite existing fixture: {path}")
        write_fixture(path, shape, values)
        print(path)


if __name__ == "__main__":
    main()

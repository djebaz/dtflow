"""Convert a safetensors LoRA to a Draw Things float16 LoRA ``.ckpt``, pure Python.

Thin wrapper around ``dtflow.services.lora_convert`` (the converter moved into the package).
Run it with the project environment (``uv run python tools/drawthings/lora_to_ckpt.py ...``);
the repository ``src`` folder is added to the path when ``dtflow`` is not installed.

Usage:
    python lora_to_ckpt.py LORA.safetensors OUT.ckpt --family krea2 [--scale 1.0]

Check the result against a Swift made file with ``inspect_ckpt.py A.ckpt B.ckpt``
(the goal is 0 differing tensor data hashes).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import dtflow  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dtflow.services.lora_convert import (  # noqa: E402
    ConversionError,
    ConversionProgress,
    ConversionRequest,
    convert_lora,
    supported_families,
)


def _print_message(event: ConversionProgress) -> None:
    if event.message:
        print(event.message)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("destination", type=Path)
    ap.add_argument("--family", required=True, choices=sorted(spec.key for spec in supported_families()))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument(
        "--ignore-unmatched",
        action="store_true",
        help="sd1 and sdxl only: skip LoRA tensors that have no place in the model, as Draw Things does",
    )
    ap.add_argument(
        "--experimental",
        action="store_true",
        help="allow families that are not verified against Draw Things yet (sdxl)",
    )
    args = ap.parse_args()
    request = ConversionRequest(
        args.source,
        args.destination,
        args.family,
        args.scale,
        allow_experimental=args.experimental,
        ignore_unmatched=args.ignore_unmatched,
        hash_source=False,
    )
    try:
        convert_lora(request, progress=_print_message)
    except ConversionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Command line entry point: ``uv run python -m dtflow.services.lora_convert SRC.safetensors OUT.ckpt --family krea2``.

The proof-of-concept script under ``tools/drawthings/`` is kept unchanged as history; this is the
CLI of the package and calls the same ``convert_lora`` the application uses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dtflow.services.lora_convert import (
    ConversionError,
    ConversionProgress,
    ConversionRequest,
    convert_lora,
    supported_families,
)


def _print_message(event: ConversionProgress) -> None:
    if event.message:
        print(event.message)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dtflow.services.lora_convert", description=__doc__.splitlines()[0])
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
    args = ap.parse_args(argv)
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

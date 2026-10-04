"""Compute the exact byte size and SHA-256 digest of a checkpoint file."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def sha256_and_size(path: Path) -> tuple[int, str]:
    """Return the exact byte size and SHA-256 digest for a file."""
    digest = hashlib.sha256()
    total_bytes = 0

    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            total_bytes += len(chunk)
            digest.update(chunk)

    return total_bytes, digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Compute the exact size and SHA-256 of a .ckpt file.")
    parser.add_argument(
        "checkpoint",
        type=Path,
        help="Path to the .ckpt file.",
    )
    return parser


def main() -> int:
    """Run the checkpoint probe."""
    args = build_parser().parse_args()
    path = args.checkpoint.expanduser()

    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    size_bytes, sha256 = sha256_and_size(path)

    print(f"File: {path}")
    print(f"Size (bytes): {size_bytes}")
    print(f"SHA-256: {sha256}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

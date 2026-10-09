"""Read-only, uncalibrated pixel-statistics PoC; never a generation success verdict."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

from dtflow.services.image_quality import ALGORITHM, ANALYSIS_SIDE, THRESHOLDS, assess_rgb

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_FILES = 10_000
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def analyze(path: Path) -> dict:
    record = {"path": str(path)}
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Encoded file exceeds 64 MiB analysis limit")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError("Image exceeds 32 million pixel analysis limit")
                record["original_size"] = list(source.size)
                record["frames"] = getattr(source, "n_frames", 1)
                oriented = ImageOps.exif_transpose(source)
                rgba = oriented.convert("RGBA")
                image = Image.new("RGBA", rgba.size, "white")
                image.alpha_composite(rgba)
                image = image.convert("RGB")
                record.update(assess_rgb(image))
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        record.update(label="unreadable", error=f"{type(exc).__name__}: {exc}")
    return record


def discover(sources: list[Path], recursive: bool) -> list[Path]:
    paths: set[Path] = set()
    for source in sources:
        source = source.resolve()
        if not source.exists():
            raise ValueError(f"Source does not exist: {source}")
        if source.is_file():
            candidates = (source,)
        elif source.is_dir():
            candidates = source.rglob("*") if recursive else source.iterdir()
        else:
            raise ValueError(f"Source is not a regular file or directory: {source}")
        for path in candidates:
            if path.is_file() and (source.is_file() or path.suffix.lower() in EXTENSIONS):
                paths.add(path.resolve())
                if len(paths) > MAX_FILES:
                    raise ValueError("More than 10000 input images; narrow the source selection")
    return sorted(paths, key=lambda path: str(path).casefold())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-s",
        "--source",
        type=Path,
        nargs="+",
        required=True,
        help="Image files or folders (folders are flat by default)",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan folder subdirectories")
    parser.add_argument("-o", "--out", type=Path, help="New JSON report; default is stdout; never overwrite")
    args = parser.parse_args(argv)
    try:
        paths = discover(args.source, args.recursive)
        if not paths:
            raise ValueError("No supported image files found")
        # Reserve exclusively before analysis, protecting reports and source images.
        destination = args.out.open("x", encoding="utf-8") if args.out else None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    try:
        records = [analyze(path) for path in paths]
        report = {
            "algorithm": ALGORITHM,
            "warning": "Uncalibrated advisory flags; not aesthetics, prompt alignment or generation status. Review manually.",
            "analysis": {
                "max_side": ANALYSIS_SIDE,
                "coarse_max_side": 32,
                "alpha_background": "white",
                "frame": "first",
                "thresholds": THRESHOLDS,
            },
            "summary": dict(Counter(record["label"] for record in records)),
            "images": records,
        }
        json.dump(report, destination or sys.stdout, ensure_ascii=False, indent=2)
        (destination or sys.stdout).write("\n")
    finally:
        if destination:
            destination.close()
    if args.out:
        print(f"Report: {args.out} ({len(records)} images)", file=sys.stderr)
    return 1 if any(record["label"] == "unreadable" for record in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())

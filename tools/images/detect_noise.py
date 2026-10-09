"""Read-only, uncalibrated pixel-statistics PoC; never a generation success verdict."""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageOps, ImageStat

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_FILES = 10_000
ANALYSIS_SIDE = 256
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
THRESHOLDS = {
    "saturated_fraction": 0.30,
    "extreme_channel_fraction": 0.20,
    "neighbor_rgb_difference": 0.12,
    "coarse_residual_rms": 0.10,
    "coarse_residual_to_rgb_stddev": 0.75,
    "noise_min_signals": 3,
    "noise_neighbor_gate": 0.08,
    "blank_luma_stddev": 0.01,
    "spatial_neighbor_min": 0.08,
    "spatial_residual_rms_min": 0.18,
    "spatial_residual_ratio_min": 0.65,
}


def pixel_metrics(image: Image.Image) -> dict[str, float]:
    """Amplitudes/fractions are normalized; entropy is in bits; residual ratio is unbounded."""
    gray = image.convert("L")
    luma = ImageStat.Stat(gray)
    histogram = gray.histogram()
    count = image.width * image.height
    entropy = -sum((n / count) * math.log2(n / count) for n in histogram if n)
    saturation = image.convert("HSV").getchannel("S").histogram()
    extreme_channels = sum(sum(h[:6]) + sum(h[250:]) for h in (c.histogram() for c in image.split()))
    total_difference = 0.0
    neighbor_pairs = 0
    for first, second in (
        ((0, 0, image.width - 1, image.height), (1, 0, image.width, image.height)),
        ((0, 0, image.width, image.height - 1), (0, 1, image.width, image.height)),
    ):
        if first[2] <= first[0] or first[3] <= first[1]:
            continue
        diff = ImageChops.difference(image.crop(first), image.crop(second))
        total_difference += sum(ImageStat.Stat(diff).sum)
        neighbor_pairs += diff.width * diff.height
    coarse = image.copy()
    coarse.thumbnail((32, 32), Image.Resampling.BOX)
    reconstructed = coarse.resize(image.size, Image.Resampling.BILINEAR)
    residual = ImageStat.Stat(ImageChops.difference(image, reconstructed))
    residual_rms = math.sqrt(sum(v * v for v in residual.rms) / 3) / 255
    rgb_stddev = math.sqrt(sum(v * v for v in ImageStat.Stat(image).stddev) / 3) / 255
    return {
        "luma_mean": luma.mean[0] / 255,
        "luma_stddev": luma.stddev[0] / 255,
        "luma_entropy_bits": entropy,
        "saturated_fraction": sum(saturation[200:]) / count,
        "extreme_channel_fraction": extreme_channels / (count * 3),
        "neighbor_rgb_difference": total_difference / (max(neighbor_pairs, 1) * 3 * 255),
        "coarse_residual_rms": residual_rms,
        "coarse_residual_to_rgb_stddev": residual_rms / max(rgb_stddev, 1 / 255),
    }


def classify(metrics: dict[str, float]) -> tuple[str, list[str], int]:
    """Count explainable heuristic signals, not probabilities or quality scores."""
    signals = []
    for name in ("saturated_fraction", "extreme_channel_fraction", "neighbor_rgb_difference"):
        if metrics[name] >= THRESHOLDS[name]:
            signals.append(name)
    if all(metrics[name] >= THRESHOLDS[name] for name in ("coarse_residual_rms", "coarse_residual_to_rgb_stddev")):
        signals.append("strong_coarse_scale_residual")
    if metrics["luma_stddev"] < THRESHOLDS["blank_luma_stddev"]:
        return "possible_blank", ["very_low_luminance_variation"], len(signals)
    if len(signals) >= THRESHOLDS["noise_min_signals"] and (
        metrics["neighbor_rgb_difference"] >= THRESHOLDS["noise_neighbor_gate"]
    ):
        return "possible_noise", signals, len(signals)
    if (
        metrics["neighbor_rgb_difference"] >= THRESHOLDS["spatial_neighbor_min"]
        and metrics["coarse_residual_rms"] >= THRESHOLDS["spatial_residual_rms_min"]
        and metrics["coarse_residual_to_rgb_stddev"] >= THRESHOLDS["spatial_residual_ratio_min"]
    ):
        # Preserve the original four-signal count; this is a separate decision route.
        return "possible_noise", [*signals, "joint_spatial_noise_rule"], len(signals)
    return "no_obvious_failure", signals, len(signals)


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
                image.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE), Image.Resampling.LANCZOS)
                record["analysis_size"] = list(image.size)
                metrics = pixel_metrics(image)
        label, reasons, signals = classify(metrics)
        record.update(
            label=label,
            reasons=reasons,
            noise_signal_count=signals,
            metrics={name: round(value, 6) for name, value in metrics.items()},
        )
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
            "algorithm": "dtflow-pixel-statistics-poc-v2",
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

"""Shared advisory pixel-statistics detector; no files, Qt, providers or job-state writes."""

import math

from PIL import Image, ImageChops, ImageStat

ALGORITHM = "dtflow-pixel-statistics-poc-v2"
ANALYSIS_SIDE = 256
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


def assess_rgb(image: Image.Image) -> dict:
    """Analyze a caller-normalized RGB image without modifying it or reading metadata."""
    thumbnail = image.copy()
    try:
        thumbnail.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE), Image.Resampling.LANCZOS)
        metrics = pixel_metrics(thumbnail)
        label, reasons, signals = classify(metrics)
        return {
            "algorithm": ALGORITHM,
            "label": label,
            "reasons": reasons,
            "noise_signal_count": signals,
            "analysis_size": list(thumbnail.size),
            "metrics": {name: round(value, 6) for name, value in metrics.items()},
        }
    finally:
        thumbnail.close()

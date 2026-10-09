"""Disposable source-only image computations; never destination/export-history records."""

import json
import math
import re
from pathlib import Path

from dtflow.services.artifacts import atomic_write

CACHE_VERSION = 1
MAX_ENTRIES = 10_000
MAX_BYTES = 16 * 1024 * 1024
FILENAME = "image_analysis.json"
METRICS = (
    "luma_mean",
    "luma_stddev",
    "luma_entropy_bits",
    "saturated_fraction",
    "extreme_channel_fraction",
    "neighbor_rgb_difference",
    "coarse_residual_rms",
    "coarse_residual_to_rgb_stddev",
)


def _assessment(value) -> dict | None:
    if not isinstance(value, dict) or value.get("label") not in (
        "possible_noise",
        "possible_blank",
        "no_obvious_failure",
    ):
        return None
    algorithm, reasons, size, metrics = (value.get(key) for key in ("algorithm", "reasons", "analysis_size", "metrics"))
    if not isinstance(algorithm, str) or len(algorithm) > 100:
        return None
    if (
        not isinstance(reasons, list)
        or len(reasons) > 8
        or any(not isinstance(x, str) or len(x) > 100 for x in reasons)
    ):
        return None
    if not isinstance(size, list) or len(size) != 2 or any(type(x) is not int or not 1 <= x <= 256 for x in size):
        return None
    if type(value.get("noise_signal_count")) is not int or not 0 <= value["noise_signal_count"] <= 4:
        return None
    if not isinstance(metrics, dict) or any(
        type(metrics.get(key)) not in (int, float) or not math.isfinite(metrics[key]) or metrics[key] < 0
        for key in METRICS
    ):
        return None
    return {
        "algorithm": algorithm,
        "label": value["label"],
        "reasons": reasons,
        "analysis_size": size,
        "noise_signal_count": value["noise_signal_count"],
        "metrics": {key: metrics[key] for key in METRICS},
    }


def load_image_analysis(state: Path) -> dict:
    try:
        with (state / "cache" / "derived" / FILENAME).open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return {}
        document = json.loads(data)
        if (
            not isinstance(document, dict)
            or type(document.get("cache_version")) is not int
            or document["cache_version"] != CACHE_VERSION
        ):
            return {}
        entries = document.get("images")
        if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
            return {}
        result = {}
        for identity, entry in entries.items():
            if not re.fullmatch(r"[a-f0-9]{32}:\d+", identity) or not isinstance(entry, dict):
                continue
            fingerprint, checksum = entry.get("fingerprint"), entry.get("source_png_sha256")
            if not isinstance(fingerprint, dict) or any(
                type(fingerprint.get(key)) is not int or fingerprint[key] < 0 for key in ("size", "mtime_ns")
            ):
                continue
            path_digest = fingerprint.get("path_sha256")
            if not isinstance(path_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", path_digest):
                continue
            if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
                continue
            result[identity] = {
                "fingerprint": {
                    "size": fingerprint["size"],
                    "mtime_ns": fingerprint["mtime_ns"],
                    "path_sha256": path_digest,
                },
                "source_png_sha256": checksum,
                "assessment": _assessment(entry.get("assessment")),
            }
        return result
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
        return {}


def save_image_analysis(state: Path, images: dict) -> None:
    while len(images) > MAX_ENTRIES:
        images.pop(next(iter(images)))
    encoded = (
        json.dumps(
            {"cache_version": CACHE_VERSION, "images": images},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError("Image analysis cache exceeds its size limit.")
    root = state / "cache" / "derived"
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / FILENAME, encoded)

#!/usr/bin/env python3
"""Generate review-only YOLO OBB drafts from penisV2-context SEG masks, using ONNX CPU."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw

from custom_parts_poc import (
    PALETTE,
    class_confidence,
    frame,
    load_model,
    paint_mask,
    predict,
    segment_context_regions,
)
from schp_atr18_smoke import IMAGE_SUFFIXES, load_rgb

CLASSES = {"balls": 0, "dick": 1, "dick-head": 2}
RESUME_SETTINGS = (
    "detect_confidence",
    "seg_confidence",
    "seg_class_conf",
    "context",
    "context_shape",
    "mask_threshold",
    "nms",
    "threads",
    "min_area",
    "dominance",
    "size",
    "classes",
    "max_det",
    "uncrop",
    "alpha",
)


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def completed_outputs(output):
    """Sidecars are completion markers; reserve all numbers, including partial writes."""
    completed, records, maximum = {}, [], 0
    for path in sorted(output.iterdir()):
        match = re.fullmatch(r"draft_(\d+)(?:_overlay)?\.(?:png|txt|jpg|json)", path.name)
        if not match:
            continue
        maximum = max(maximum, int(match[1]))
        if path.is_symlink() or path.is_junction() or not path.is_file():
            raise ValueError(f"Redirected/non-file draft output: {path}")
        if path.suffix != ".json":
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        prefix = path.stem
        expected = [f"{prefix}.png", f"{prefix}_overlay.jpg"]
        if report.get("label_file") is not None:
            if report["label_file"] != f"{prefix}.txt":
                raise ValueError(f"Unexpected label filename in {path}")
            expected.append(f"{prefix}.txt")
        if report.get("output_image") != f"{prefix}.png":
            raise ValueError(f"Unexpected source-image filename in {path}")
        if report.get("status") not in {"draft_needs_review", "no_labels_needs_review"}:
            raise ValueError(f"Unrecognized completion status in {path}")
        artifacts = [output / name for name in expected]
        if any(p.is_symlink() or p.is_junction() for p in artifacts):
            raise ValueError(f"Redirected draft artifacts for {prefix}")
        if any(not p.is_file() for p in artifacts):
            # Incomplete outputs remain untouched; a new number can be allocated.
            continue
        key = Path(report["source"]).resolve()
        if key in completed:
            raise ValueError(f"Multiple completed drafts for {key}; choose a new output folder")
        completed[key] = report
        records.append(
            {
                "source": report["source"],
                "prefix": prefix,
                "status": report["status"],
                "accepted": len(report["accepted"]),
                "rejected": len(report["rejected"]),
            }
        )
    return completed, records, maximum


def write_manifest(output, records):
    path = output / "manifest.json"
    temporary = output / "manifest.json.tmp"
    if path.is_symlink() or path.is_junction() or temporary.exists() or temporary.is_symlink():
        raise ValueError("Unsafe or leftover manifest path; inspect manifest.json.tmp before retrying")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"review_required": True, "class_ids": CLASSES, "images": records}, indent=2) + "\n")
    os.replace(temporary, path)


def fit_mask(item, source_size, minimum, dominance):
    mask = item["mask"].astype(np.uint8)
    count, components, stats, _ = cv.connectedComponentsWithStats(mask, connectivity=8)
    record = {key: value for key, value in item.items() if key != "mask"}
    total = int(mask.sum())
    record.update(mask_pixels=total, components=count - 1)
    if not total:
        return None, dict(record, rejected="empty mask")
    largest = 1 + int(np.argmax(stats[1:, cv.CC_STAT_AREA]))
    area = int(stats[largest, cv.CC_STAT_AREA])
    fraction = area / total
    record.update(component_pixels=area, dominant_fraction=fraction)
    if area < minimum:
        return None, dict(record, rejected="component below minimum area")
    if fraction < dominance:
        return None, dict(record, rejected="fragmented mask; no dominant component")
    clean = components == largest
    contours, _ = cv.findContours(clean.astype(np.uint8), cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    points = np.concatenate(contours).astype(np.float32)
    rectangle = cv.minAreaRect(points)
    if min(rectangle[1]) < 2:
        return None, dict(record, rejected="degenerate rectangle")
    polygon = cv.boxPoints(rectangle).astype(np.float64) + item["offset_xy"]
    width, height = source_size
    if np.any(polygon < 0) or np.any(polygon > [width, height]):
        return None, dict(record, rejected="rectangle outside image; clipping would destroy right angles")
    edges = np.roll(polygon, -1, axis=0) - polygon
    edge = edges[np.argmax(np.linalg.norm(edges, axis=1))]
    record.update(
        class_id=CLASSES[item["class"]],
        polygon_xy=polygon.tolist(),
        normalized_corners=(polygon / [width, height]).tolist(),
        long_axis_degrees=math.degrees(math.atan2(edge[1], edge[0])) % 180,
        rectangle_area=float(rectangle[1][0] * rectangle[1][1]),
    )
    return clean, record


def process(path, index, detector, detector_names, seg, seg_names, identities, args, source_digest):
    started = time.perf_counter()
    source = load_rgb(path)
    _, located = predict(detector, detector_names, source, "detect", args, filter_classes=False)
    targets = [d for d in located["detections"] if d["class"] == "penis"]
    masks = []
    _, segmented = segment_context_regions(seg, seg_names, source, args, targets, mask_sink=masks)
    pixels = np.asarray(source).copy()
    accepted, rejected = [], []
    for item in masks:
        clean, record = fit_mask(item, source.size, args.min_area, args.dominance)
        if clean is None:
            rejected.append(record)
            continue
        x, y = item["offset_xy"]
        height, width = clean.shape
        paint_mask(pixels[y : y + height, x : x + width], clean, PALETTE[item["class"]], args.alpha)
        polygon = np.asarray(record["polygon_xy"])
        cv.polylines(
            pixels, [np.rint(polygon * 256).astype(np.int32)], True, PALETTE[item["class"]], 2, cv.LINE_AA, shift=8
        )
        accepted.append(record)
    preview = Image.fromarray(pixels)
    draw = ImageDraw.Draw(preview)
    for record in accepted:
        polygon = np.asarray(record["polygon_xy"])
        x, y = polygon.min(axis=0)
        draw.text(
            (max(0, x), max(0, y - 15)),
            f"{record['class']} {record['score']:.2f}",
            fill="white",
            stroke_width=1,
            stroke_fill="black",
        )
    prefix = f"draft_{index:05d}"
    status = "draft_needs_review" if accepted else "no_labels_needs_review"
    # Normalize EXIF/transparency into exactly the raster coordinate frame used for labels.
    source.save(args.output / f"{prefix}.png")
    lines = [
        f"{r['class_id']} " + " ".join(f"{v:.10f}" for point in r["normalized_corners"] for v in point)
        for r in accepted
    ]
    # No empty label files: unreviewed misses must not silently become training negatives.
    if lines:
        (args.output / f"{prefix}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    preview.thumbnail((args.size, args.size), Image.Resampling.LANCZOS)
    frame(preview, f"DRAFT: {len(accepted)} boxes / {len(rejected)} rejected", {v: k for k, v in CLASSES.items()}).save(
        args.output / f"{prefix}_overlay.jpg", quality=93
    )
    digest = file_digest(path)
    if digest != source_digest:
        raise RuntimeError(f"Source changed during processing: {path}; partial outputs were retained")
    report = {
        "status": status,
        "source": str(path.resolve()),
        "source_sha256": digest,
        "source_size": list(source.size),
        "output_image": f"{prefix}.png",
        "label_file": f"{prefix}.txt" if lines else None,
        "class_ids": CLASSES,
        "models": identities,
        "settings": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "detector": located,
        "segmentation": segmented,
        "accepted": accepted,
        "rejected": rejected,
        "seconds": time.perf_counter() - started,
        "notes": [
            "Draft pseudo-labels only; review every accepted box and missing object before training",
            "Minimum-area rectangle of largest component; orientation is undirected, not anatomical base-to-tip",
            "Smaller components discarded only when dominant fraction passes configured threshold",
            "No mask smoothing, expansion or blur; source pixels are normalized to PNG",
            "Only three export classes; detector misses and SEG mistakes can propagate",
            "No train/validation/test split created or modified; no empty negative labels generated",
        ],
    }
    (args.output / f"{prefix}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"{index}: {path.name}: {len(accepted)} drafts, {len(rejected)} rejected; {report['seconds']:.2f}s", flush=True
    )
    return {
        "source": str(path.resolve()),
        "prefix": prefix,
        "status": status,
        "accepted": len(accepted),
        "rejected": len(rejected),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument(
        "-o", "--output", type=Path, required=True, help="Flat folder; existing directories require --resume"
    )
    parser.add_argument(
        "-rs",
        "--resume",
        action="store_true",
        help="Skip completed unchanged sources; append new drafts without overwriting",
    )
    parser.add_argument("-dm", "--detect-model", type=Path, default=Path("tmp/models/penisV2.onnx"))
    parser.add_argument("-sm", "--seg-model", type=Path, default=Path("tmp/models/best_seg_dick.onnx"))
    parser.add_argument("-ds", "--detect-confidence", type=float, default=0.15)
    parser.add_argument("-ss", "--seg-confidence", type=float, default=0.05)
    parser.add_argument(
        "-sc", "--seg-class-conf", nargs="+", default=["dick=0.1", "dick-head=0.15", "hand=0.60", "balls=0.1"]
    )
    parser.add_argument("-k", "--context", type=float, default=1.3)
    parser.add_argument("-ks", "--context-shape", choices=["box", "square"], default="box")
    parser.add_argument("-mt", "--mask-threshold", type=float, default=-1)
    parser.add_argument("-n", "--nms", type=float, default=0.1)
    parser.add_argument("-t", "--threads", type=int, default=2)
    parser.add_argument(
        "-ma", "--min-area", type=int, default=32, help="Minimum dominant-component area in source pixels"
    )
    parser.add_argument("-df", "--dominance", type=float, default=0.85, help="Minimum largest-component fraction")
    parser.add_argument(
        "-r", "--size", type=int, default=1600, help="Maximum preview side; export images stay full size"
    )
    parser.add_argument("-l", "--limit", type=int, default=0, help="First N sorted input photos; 0 means all")
    args = parser.parse_args()
    if not args.input_dir.is_dir():
        parser.error("Input must be a directory")
    if args.output.is_symlink() or args.output.is_junction():
        parser.error("Output must not be a redirected directory")
    if args.output.exists() and (not args.resume or not args.output.is_dir()):
        parser.error("Output already exists; use --resume or a new directory")
    args.input_dir = args.input_dir.resolve()
    args.output = args.output.resolve()
    if args.output == args.input_dir or args.output in args.input_dir.parents:
        parser.error("Output must not equal or contain the input folder")
    if any(
        not math.isfinite(v) or not 0 < v <= 1 for v in [args.detect_confidence, args.seg_confidence, args.dominance]
    ):
        parser.error("Confidence/dominance must be finite and in (0,1]")
    if (
        not math.isfinite(args.context)
        or not 1 <= args.context <= 6
        or not math.isfinite(args.nms)
        or not 0 <= args.nms <= 1
    ):
        parser.error("Context must be 1..6 and NMS 0..1")
    if not math.isfinite(args.mask_threshold) or not -8 <= args.mask_threshold <= 8:
        parser.error("Mask threshold must be finite in [-8,8]")
    if not 1 <= args.threads <= 64 or args.min_area < 3 or not 256 <= args.size <= 4096 or args.limit < 0:
        parser.error("Invalid threads, minimum area, preview size or limit")
    args.seg_class_conf = class_confidence(args.seg_class_conf, parser)
    args.classes = list(CLASSES)
    args.max_det, args.uncrop, args.alpha = 50, False, 0.3
    paths = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        parser.error("No supported images found (input folder is nonrecursive)")
    detector, detector_names, detect_id = load_model(args.detect_model, "detect", args.threads)
    seg, seg_names, seg_id = load_model(args.seg_model, "segment", args.threads)
    if "penis" not in detector_names.values() or not set(CLASSES) <= set(seg_names.values()):
        parser.error("Expected penis locator and SEG with balls/dick/dick-head classes")
    unknown = set(args.seg_class_conf) - set(seg_names.values())
    if unknown:
        parser.error(f"SEG has no classes: {sorted(unknown)}")
    cv.setNumThreads(args.threads)
    args.output.mkdir(parents=True, exist_ok=args.resume)
    completed, records, maximum = completed_outputs(args.output)
    identities = {"detector": detect_id, "seg": seg_id}
    pending, skipped = [], 0
    # Preflight all selected sources before appending anything; never mix changed recipes silently.
    for path in paths:
        digest = file_digest(path)
        previous = completed.get(path.resolve())
        if previous is not None:
            if previous["source_sha256"] != digest:
                parser.error(f"Completed source changed: {path}; use a new output folder")
            if any(previous["models"][key]["sha256"] != identities[key]["sha256"] for key in identities):
                parser.error(f"Models changed for {path}; use a new output folder")
            if any(previous["settings"].get(key) != getattr(args, key) for key in RESUME_SETTINGS):
                parser.error(f"Settings changed for {path}; repeat the original recipe or use a new output folder")
            skipped += 1
        else:
            pending.append((path, digest))
    print(f"Resume: {skipped} completed skipped; {len(pending)} to process; next draft {maximum + 1}", flush=True)
    failures = 0
    for index, (path, digest) in enumerate(pending, maximum + 1):
        try:
            records.append(process(path, index, detector, detector_names, seg, seg_names, identities, args, digest))
        except Exception as exc:
            failures += 1
            records.append({"source": str(path), "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            print(f"{index}: {path.name}: FAILED: {exc}", flush=True)
        write_manifest(args.output, records)
    if not pending:
        write_manifest(args.output, records)
    print(f"Saved drafts to {args.output}; {failures} failures. Review before importing into training.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())

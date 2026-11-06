#!/usr/bin/env python3
"""Inspect OBB rotation behavior and/or augmented YOLO training polygons, on CPU."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace

import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw

from custom_parts_poc import PALETTE, class_confidence, frame, load_model, predict
from schp_atr18_smoke import IMAGE_SUFFIXES, load_rgb

NAMES = {0: "balls", 1: "dick", 2: "dick-head"}


def transform(points, matrix):
    return np.asarray(points, dtype=np.float64) @ matrix[:, :2].T + matrix[:, 2]


def rotate(image, angle):
    width, height = image.size
    matrix = cv.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    bounds = transform([[0, 0], [width, 0], [width, height], [0, height]], matrix)
    low, high = bounds.min(axis=0), bounds.max(axis=0)
    size = tuple(max(1, math.ceil(float(v))) for v in high - low)
    matrix[:, 2] -= low
    pixels = cv.warpAffine(np.asarray(image), matrix, size, borderValue=(114, 114, 114))
    return Image.fromarray(pixels), matrix


def orientation(polygon):
    edges = np.roll(polygon, -1, axis=0) - polygon
    edge = edges[np.argmax(np.linalg.norm(edges, axis=1))]
    angle = math.degrees(math.atan2(edge[1], edge[0])) % 180
    tilt = min(angle % 90, 90 - angle % 90)
    return angle, tilt


def draw_polygons(image, records):
    pixels = np.asarray(image).copy()
    for record in records:
        polygon = np.asarray(record["polygon_xy"])
        color = PALETTE.get(record["class"], (255, 230, 40))
        cv.polylines(pixels, [np.rint(polygon * 256).astype(np.int32)], True, color, 2, cv.LINE_AA, shift=8)
    return Image.fromarray(pixels)


def panel(image, title, names, side):
    image = image.copy()
    image.thumbnail((side, side), Image.Resampling.LANCZOS)
    return frame(image, title, names)


def grid(rows, path):
    width = max(sum(p.width for p in row) for row in rows)
    heights = [max(p.height for p in row) for row in rows]
    result = Image.new("RGB", (width, sum(heights)), (22, 22, 26))
    y = 0
    for row, height in zip(rows, heights, strict=True):
        x = 0
        for image in row:
            result.paste(image, (x, y))
            x += image.width
        y += height
    result.save(path, quality=93)


def probe(args):
    path = args.input
    if path.is_dir():
        paths = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        if not paths:
            raise ValueError(f"No supported images in {path}")
        path = paths[0]
    source = load_rgb(path)
    session, names, identity = load_model(args.model, "obb", args.threads)
    settings = SimpleNamespace(
        obb_confidence=args.confidence,
        obb_class_conf=args.class_conf,
        classes=None,
        nms=args.nms,
        max_det=50,
    )
    rows, reports = [], []
    for angle in args.angles:
        rotated, matrix = rotate(source, angle)
        overlay, report = predict(session, names, rotated, "obb", settings)
        inverse = cv.invertAffineTransform(matrix)
        mapped = []
        for detection in report["detections"]:
            polygon = transform(detection["polygon_xy"], inverse)
            axis, tilt = orientation(polygon)
            mapped.append(
                {
                    "class": detection["class"],
                    "score": detection["score"],
                    "polygon_xy": polygon.tolist(),
                    "long_axis_degrees": axis,
                    "tilt_from_axis_degrees": tilt,
                }
            )
        rows.append(
            [
                panel(overlay, f"Input rotated {angle:g} deg", names, args.size),
                panel(draw_polygons(source, mapped), f"{angle:g} deg predictions mapped back", names, args.size),
            ]
        )
        reports.append({"rotation_degrees": angle, "matrix": matrix.tolist(), "prediction": report, "mapped": mapped})
        print(f"{angle:g} deg: {len(mapped)} boxes; CPU {report['inference_seconds']:.3f}s", flush=True)
    grid(rows, args.output / "rotation_compare.jpg")
    return {"source": str(path), "model": identity, "runs": reports}


def preview_labels(args):
    # Read raster coordinates as stored: EXIF transposition would misalign YOLO labels.
    root = args.dataset
    images = root / "train" / "images"
    labels = root / "train" / "labels"
    paths = sorted(p for p in images.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    paths = [p for p in paths if p.stem.startswith(("augrot_", "balls_extra_"))]
    if not paths:
        raise ValueError(f"No augrot_/balls_extra_ training images in {images}")
    # Spread previews across both augmentation families, rather than just the first files.
    indices = np.linspace(0, len(paths) - 1, min(args.limit, len(paths)), dtype=int)
    reports = []
    for index in indices:
        path = paths[index]
        label = labels / path.relative_to(images).with_suffix(".txt")
        with Image.open(path) as opened:
            if opened.width * opened.height > 16_000_000:
                raise ValueError(f"Image too large: {path}")
            image = opened.convert("RGB")
        records = []
        for line in label.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 9 or int(fields[0]) not in NAMES:
                raise ValueError(f"Invalid three-class OBB annotation: {label}")
            polygon = np.asarray(fields[1:], dtype=np.float64).reshape(4, 2)
            if not np.isfinite(polygon).all() or np.any((polygon < 0) | (polygon > 1)):
                raise ValueError(f"Invalid normalized corners: {label}")
            polygon *= image.size
            edges = np.roll(polygon, -1, axis=0) - polygon
            lengths = np.linalg.norm(edges, axis=1)
            if np.any(lengths < 1e-6):
                raise ValueError(f"Degenerate polygon: {label}")
            adjacent = np.roll(edges, -1, axis=0)
            cosine = np.sum(edges * adjacent, axis=1) / (lengths * np.roll(lengths, -1))
            angles = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
            axis, tilt = orientation(polygon)
            records.append(
                {
                    "class": NAMES[int(fields[0])],
                    "polygon_xy": polygon.tolist(),
                    "long_axis_degrees": axis,
                    "tilt_from_axis_degrees": tilt,
                    "corner_angles_degrees": angles.tolist(),
                }
            )
        title = f"Labels: {path.name}"
        preview = panel(draw_polygons(image, records), title, NAMES, args.size)
        ImageDraw.Draw(preview).text((10, 31), "Pixel-space corners and angles recorded in JSON", fill="white")
        preview.save(args.output / f"labels_{len(reports) + 1:03d}.jpg", quality=93)
        reports.append({"source": str(path), "labels": str(label), "boxes": records})
    print(f"Saved {len(reports)} label previews; no inference for this mode", flush=True)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", type=Path, help="One photo or a folder (first sorted photo only)")
    parser.add_argument(
        "-ds", "--dataset", type=Path, help="Optional three-class augmented dataset root; preview labels"
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="New flat output directory; never overwritten")
    parser.add_argument("-m", "--model", type=Path, default=Path("tmp/models/yolo26n_obb_duck_rotate.onnx"))
    parser.add_argument("-a", "--angles", type=float, nargs="+", default=[0, 30, 60])
    parser.add_argument("-s", "--confidence", type=float, default=0.5)
    parser.add_argument("-sc", "--class-conf", nargs="+", default=["dick=0.4", "balls=0.1", "dick-head=0.1"])
    parser.add_argument("-n", "--nms", type=float, default=0.5)
    parser.add_argument("-t", "--threads", type=int, default=2)
    parser.add_argument("-r", "--size", type=int, default=768, help="Saved panel maximum side")
    parser.add_argument("-l", "--limit", type=int, default=8, help="Maximum dataset label previews")
    args = parser.parse_args()
    if not args.input and not args.dataset:
        parser.error("Supply --input and/or --dataset")
    if not 0 <= args.confidence <= 1 or not 0 <= args.nms <= 1:
        parser.error("Confidence/NMS must be in [0,1]")
    if not 1 <= args.threads <= 64 or not 256 <= args.size <= 2048 or not 1 <= args.limit <= 100:
        parser.error("Require threads 1..64, size 256..2048, limit 1..100")
    if len(args.angles) > 12 or any(not math.isfinite(a) or abs(a) > 180 for a in args.angles):
        parser.error("Supply at most 12 finite angles in [-180,180]")
    args.class_conf = class_confidence(args.class_conf, parser)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {}
    if args.input:
        report["rotation_probe"] = probe(args)
    if args.dataset:
        report["label_previews"] = preview_labels(args)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Outputs: {args.output}")


if __name__ == "__main__":
    main()

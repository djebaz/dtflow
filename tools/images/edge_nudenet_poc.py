#!/usr/bin/env python3
"""DexiNed/OpenCV CPU edges and NudeNet-seeded zones; no Pascal or depth model.

Automatically downloads missing pinned ONNX checkpoints. Writes flat edge views,
a zone preview with legend, an RGBA person cutout and a JSON report. Only the
largest person is processed. Zone names are detector suggestions, not anatomy.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pascal_nudenet_poc import UNKNOWN, save_preview, verified_checkpoint
from schp_atr18_smoke import CHECKPOINTS, IMAGE_SUFFIXES, detect_body_parts, load_rgb, person_crop_box, select_people

REVISION = "01a752eb4006688376e576dc24941c54608f6dd0"
EDGE_SPEC = (
    f"https://huggingface.co/opencv/edge_detection_dexined/resolve/{REVISION}/edge_detection_dexined_2024sep.onnx",
    47_235_563,
    "sha256",
    "a50d01dc8481549c7dedb9eb3e0123b810a016520df75e4669a504609982cdd0",
)


def detect_edges(cv, net, crop):
    """Follow the pinned OpenCV Zoo artifact's BGR preprocessing and fused output."""
    bgr = np.asarray(crop, dtype=np.uint8)[:, :, ::-1].copy()
    blob = cv.dnn.blobFromImage(bgr, 1.0, (512, 512), (103.5, 116.2, 123.6), swapRB=False, crop=False)
    net.setInput(blob)
    output = np.asarray(net.forward())
    if output.ndim not in (3, 4, 5) or not np.isfinite(output).all():
        raise RuntimeError(f"Invalid DexiNed output: {output.shape}")
    # Zoo iterates the first axis and selects its final (fused) prediction.
    fused = output[-1].squeeze()
    if fused.ndim != 2:
        raise RuntimeError(f"Expected fused 2D edge logits, got {fused.shape}")
    edges = 1.0 / (1.0 + np.exp(-np.clip(fused.astype(np.float32), -80, 80)))
    # Preserve absolute strengths; per-image min/max normalization distorts growth costs.
    return cv.resize(edges, crop.size, interpolation=cv.INTER_LINEAR)


def grow_zones(cv, edges, inside, detections, resolution, edge_weight, growth):
    """Compete from small detector seeds using edge-weighted path lengths only.

    No semantic classes or rectangular growth barriers. A per-seed path budget,
    proportional to detection size, prevents unlimited flooding across weak edges.
    """
    height, width = inside.shape
    scale = min(1.0, resolution / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    field = cv.resize(edges, size, interpolation=cv.INTER_AREA)
    valid = cv.resize(inside.astype(np.uint8), size, interpolation=cv.INTER_NEAREST) > 0
    h, w = valid.shape
    sx, sy = w / width, h / height
    zones = np.zeros((h, w), dtype=np.uint16)
    distance = np.full((h, w), np.inf, dtype=np.float64)
    fixed = np.zeros_like(valid)
    budgets, reports, queue = {}, [], []
    yy, xx = np.ogrid[:h, :w]
    for index, detection in enumerate(detections, 7):
        x0, y0, x1, y1 = detection["box_xyxy"]
        cx, cy = (x0 + x1) * sx / 2, (y0 + y1) * sy / 2
        rx, ry = max(1.0, (x1 - x0) * sx * 0.2), max(1.0, (y1 - y0) * sy * 0.2)
        center_distance = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        core = valid & ~fixed & (center_distance <= 1)
        record = dict(detection, zone_id=index, status="rejected_no_person_core")
        reports.append(record)
        if not core.any():
            continue
        # Prefer a near-center, low-edge point over seeding every pixel of a box.
        ranking = np.where(core, center_distance + 2 * field, np.inf)
        y, x = np.unravel_index(int(np.argmin(ranking)), ranking.shape)
        zones[y, x], distance[y, x], fixed[y, x] = index, 0.0, True
        budgets[index] = max(4.0, growth * math.hypot((x1 - x0) * sx, (y1 - y0) * sy) / 2)
        record.update(status="seeded", seed_xy=[round(float(x / sx), 2), round(float(y / sy), 2)])
        heapq.heappush(queue, (0.0, index, int(y), int(x)))
    while queue:
        cost, zone, y, x = heapq.heappop(queue)
        if cost != distance[y, x] or zones[y, x] != zone:
            continue
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if not (0 <= ny < h and 0 <= nx < w) or not valid[ny, nx] or fixed[ny, nx]:
                continue
            strength = max(float(field[y, x]), float(field[ny, nx]))
            candidate = cost + 1.0 + edge_weight * strength * strength
            if candidate <= budgets[zone] and candidate < distance[ny, nx]:
                distance[ny, nx], zones[ny, nx] = candidate, zone
                heapq.heappush(queue, (candidate, zone, ny, nx))
    result = cv.resize(zones, (width, height), interpolation=cv.INTER_NEAREST)
    result[~inside] = 0
    result[inside & (result == 0)] = UNKNOWN
    for record in reports:
        record["pixels"] = int(np.count_nonzero(result == record["zone_id"]))
        if record["status"] == "seeded":
            record["status"] = "assigned" if record["pixels"] else "empty_after_restoration"
    return result, reports


def save_outputs(source, box, inside, edges, zones, reports, output, prefix):
    x0, y0, x1, y1 = box
    names = {0: "background", UNKNOWN: "unknown"}
    names.update({r["zone_id"]: r["label"].lower() for r in reports})
    save_preview(
        source,
        box,
        inside,
        zones,
        names,
        output / f"{prefix}_zones_overlay.jpg",
        "Edge-guided zones",
        ("NudeNet names are suggestions", "Boundaries follow edge costs", "Gray: unreached / unknown"),
    )
    alpha = np.zeros((source.height, source.width), dtype=np.uint8)
    alpha[y0:y1, x0:x1] = inside.astype(np.uint8) * 255
    cutout = source.convert("RGBA")
    cutout.putalpha(Image.fromarray(alpha))
    cutout.save(output / f"{prefix}_person_cutout.png")
    full_edges = np.zeros((source.height, source.width), dtype=np.float32)
    full_edges[y0:y1, x0:x1] = np.where(inside, edges, 0)
    Image.fromarray(np.rint(full_edges * 255).astype(np.uint8)).save(output / f"{prefix}_edges.png")
    rgb = np.asarray(source, dtype=np.float32).copy()
    opacity = (full_edges * 0.85)[..., None]
    rgb = (rgb * (1 - opacity) + np.array([255, 64, 32]) * opacity).astype(np.uint8)
    legend = ("DexiNed edges", "Red: stronger edge response", "Not an anatomical prediction")
    font = ImageFont.load_default(size=18)
    panel_width = max(330, max(font.getbbox(text)[2] for text in legend) + 40)
    preview = Image.new("RGB", (source.width + panel_width, max(source.height, 140)), (24, 27, 34))
    preview.paste(Image.fromarray(rgb), (0, 0))
    draw = ImageDraw.Draw(preview)
    for index, text in enumerate(legend):
        draw.text((source.width + 20, 20 + index * 34), text, fill="white", font=font)
    preview.save(output / f"{prefix}_edges_overlay.jpg", quality=95, subsampling=0)
    return {str(key): name for key, name in names.items()}


def output_directory(args, files, parser):
    """Preflight nonrecursive cleanup, including recognized previous Pascal outputs."""
    if args.output_dir.is_symlink() or args.output_dir.is_junction():
        parser.error("output-dir must not be a link/junction")
    output = args.output_dir.resolve()
    if args.input_dir.resolve().is_relative_to(output):
        parser.error("output-dir must not contain input-dir")
    output.mkdir(parents=True, exist_ok=True)
    previous = list(output.iterdir())
    if previous and not args.overwrite:
        parser.error("output-dir is not empty; use -w / --overwrite to remove previous POC outputs")
    generated = re.compile(
        r"\d{4,}_.+_(?:edges\.png|edges_overlay\.jpg|pascal_overlay\.jpg|zones_overlay\.jpg|"
        r"person_cutout\.png|report\.json|person\.png|confidence\.png|"
        r"(?:pascal|zones)_labels\.png|(?:pascal|zones)_\d+_[a-z_]+\.png)"
    )
    protected = {p.resolve() for p in (*files, args.model, args.person_model, args.nudenet_model)}
    for entry in previous:
        if (
            entry.is_symlink()
            or entry.is_junction()
            or not entry.is_file()
            or entry.resolve().parent != output
            or entry.resolve() in protected
            or not generated.fullmatch(entry.name)
        ):
            parser.error(f"Refusing cleanup of unrelated/redirected entry: {entry.name}")
    return output, previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("tmp/edge-nudenet-poc"))
    parser.add_argument("-m", "--model", type=Path, default=Path("tmp/models/edge_detection_dexined_2024sep.onnx"))
    parser.add_argument(
        "-p", "--person-model", type=Path, default=Path("tmp/models/yolo26n-seg-ultralytics-v8.4.0.onnx")
    )
    parser.add_argument("-n", "--nudenet-model", type=Path, default=Path("tmp/models/nudenet-320n.onnx"))
    parser.add_argument("-t", "--threads", type=int, default=4, help="OpenCV threads; ORT detectors use at most 4")
    parser.add_argument("-r", "--resolution", type=int, default=384, help="Region-growth dimension; DexiNed uses 512")
    parser.add_argument("-s", "--parts-confidence", type=float, default=0.25)
    parser.add_argument("-e", "--edge-weight", type=float, default=12, help="Higher values resist crossing edges more")
    parser.add_argument(
        "-g", "--growth", type=float, default=2.5, help="Path budget relative to detection half-diagonal"
    )
    parser.add_argument("-d", "--no-download", action="store_true")
    parser.add_argument("-w", "--overwrite", action="store_true", help="Delete recognized previous POC outputs only")
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error("input-dir must be an existing directory")
    if args.threads < 1 or not 64 <= args.resolution <= 1024 or not 0 < args.parts_confidence <= 1:
        parser.error("threads >= 1, resolution in [64,1024], parts-confidence in (0,1] required")
    if (
        not math.isfinite(args.edge_weight)
        or args.edge_weight < 0
        or not math.isfinite(args.growth)
        or args.growth <= 0
    ):
        parser.error("edge-weight must be finite and >= 0; growth must be finite and > 0")
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        parser.error("No supported images found")
    import cv2 as cv
    from dtflow.services import person_selection as selection

    output, previous = output_directory(args, files, parser)
    for path, spec in (
        (args.model, EDGE_SPEC),
        (args.person_model, CHECKPOINTS["person"]),
        (args.nudenet_model, CHECKPOINTS["faces"]),
    ):
        verified_checkpoint(path, spec, not args.no_download)
    cv.setNumThreads(args.threads)
    net = cv.dnn.readNetFromONNX(str(args.model))
    net.setPreferableBackend(cv.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv.dnn.DNN_TARGET_CPU)
    people_session = selection._verified_session(
        selection.describe_model(args.person_model), selection._segmentation_session
    )
    parts_session = selection._verified_session(selection.describe_model(args.nudenet_model), selection._faces_session)
    if any(s.get_providers() != ["CPUExecutionProvider"] for s in (people_session, parts_session)):
        raise RuntimeError("Detectors must use ONNX Runtime CPU only")
    for entry in previous:
        if entry.is_symlink() or entry.is_junction() or not entry.is_file() or entry.resolve().parent != output:
            raise RuntimeError(f"Output entry changed before cleanup: {entry.name}")
        entry.unlink()
    print(f"CPU models loaded; removed {len(previous)} prior outputs; {len(files)} images", flush=True)
    failures = 0
    for index, path in enumerate(files, 1):
        prefix = f"{index:04d}_{path.stem}"
        try:
            started = time.perf_counter()
            source = load_rgb(path)
            people = select_people(selection._instances(people_session, source, 0.25), source.size, 0, 1, False)
            report = {
                "source": path.name,
                "algorithm": "dexined-nudenet-edge-growth-v1",
                "people": len(people),
                "edge_backend": "OpenCV DNN CPU",
                "detector_backend": "ONNX Runtime CPU",
                "edge_revision": REVISION,
                "edge_sha256": EDGE_SPEC[3],
                "settings": {
                    "edge_weight": args.edge_weight,
                    "growth": args.growth,
                    "resolution": args.resolution,
                    "parts_confidence": args.parts_confidence,
                },
            }
            if people:
                _, mask, person_box = people[0]
                box = tuple(int(v) for v in person_crop_box(person_box, source.size))
                x0, y0, x1, y1 = box
                crop, inside = source.crop(box), mask[y0:y1, x0:x1] > 0
                tick = time.perf_counter()
                edges = detect_edges(cv, net, crop)
                report["edge_seconds"] = time.perf_counter() - tick
                _, detections, _ = detect_body_parts(parts_session, crop, args.parts_confidence)
                detections = [d for d in detections if not d["label"].startswith("ARMPITS_")]
                tick = time.perf_counter()
                zones, details = grow_zones(
                    cv, edges, inside, detections, args.resolution, args.edge_weight, args.growth
                )
                report["growth_seconds"] = time.perf_counter() - tick
                names = save_outputs(source, box, inside, edges, zones, details, output, prefix)
                report.update(
                    crop_box=list(box),
                    label_names=names,
                    detections=details,
                    coordinates="crop-relative, EXIF-oriented pixels",
                    unknown_pixels=int(np.count_nonzero(zones == UNKNOWN)),
                    limitation="Edges include texture/shadows; zones are heuristic, not anatomical segmentation.",
                )
                print(
                    f"{path.name}: 1 person, {sum(d['pixels'] > 0 for d in details)} zones; "
                    f"edges {report['edge_seconds']:.2f}s, growth {report['growth_seconds']:.2f}s",
                    flush=True,
                )
            else:
                report["status"] = "no_person"
                print(f"{path.name}: no person", flush=True)
            report["total_seconds"] = time.perf_counter() - started
            (output / f"{prefix}_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            failures += 1
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

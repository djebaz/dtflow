#!/usr/bin/env python3
"""Fast depth-volume segmentation POC using NudeNet boxes as region selectors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from schp_atr18_smoke import (
    CHECKPOINTS,
    IMAGE_SUFFIXES,
    NUDENET_CONFIDENCE,
    depth_session,
    detect_body_parts,
    ensure_checkpoint,
    load_rgb,
    select_people,
)


def estimate_depth(source, session):
    """Run the existing Depth Anything V2 ONNX model and preserve float precision."""
    side = 518
    rgb = np.asarray(source.resize((side, side), Image.Resampling.BICUBIC), dtype=np.float32) / 255
    rgb = (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = np.transpose(rgb, (2, 0, 1))[None].astype(np.float32)
    output = np.asarray(session.run(None, {session.get_inputs()[0].name: tensor})[0]).squeeze()
    if output.ndim != 2 or not np.isfinite(output).all():
        raise RuntimeError(f"Invalid depth output: {output.shape}")
    return cv2.resize(output.astype(np.float32), source.size, interpolation=cv2.INTER_CUBIC)


def volume_regions(depth, silhouette, *, resolution=384, edge_threshold=0.30):
    """Extract connected surface patches separated by strong depth/normal transitions."""
    height, width = depth.shape
    scale = min(1.0, resolution / max(height, width))
    size = (max(32, round(width * scale)), max(32, round(height * scale)))
    z = cv2.resize(depth, size, interpolation=cv2.INTER_AREA)
    inside = cv2.resize(silhouette.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) > 0
    if not np.any(inside):
        return np.zeros((height, width), dtype=np.int32), np.zeros((height, width), dtype=np.uint8)
    low, high = np.percentile(z[inside], [2, 98])
    # Percentile scalars can promote normalization to float64; Sobel needs float32 input.
    z = ((z - low) / max(float(high - low), 1e-6)).astype(np.float32)
    z = cv2.GaussianBlur(z, (0, 0), 1.0)
    gx = cv2.Sobel(z, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(z, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.hypot(gx, gy)
    gradient_scale = max(float(np.percentile(magnitude[inside], 95)), 1e-6)
    nx, ny = -gx / gradient_scale, -gy / gradient_scale
    norm = np.sqrt(nx * nx + ny * ny + 1.0)
    normals = np.stack([nx / norm, ny / norm, 1.0 / norm], axis=-1)
    change_x = np.linalg.norm(np.diff(normals, axis=1), axis=-1)
    change_y = np.linalg.norm(np.diff(normals, axis=0), axis=-1)
    edges = np.zeros_like(z)
    edges[:, 1:] = np.maximum(edges[:, 1:], change_x)
    edges[1:, :] = np.maximum(edges[1:, :], change_y)
    contrast = max(float(np.percentile(edges[inside], 98)), 1e-6)
    edges = np.clip(edges / contrast, 0, 1)
    barrier = (edges >= edge_threshold).astype(np.uint8)
    barrier = cv2.morphologyEx(barrier, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    free = ((barrier == 0) & inside).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=8)
    # Each free-space component is a surface region; barrier pixels stay unlabeled.
    components[~inside] = 0
    components = cv2.resize(components.astype(np.float32), (width, height), interpolation=cv2.INTER_NEAREST).astype(
        np.int32
    )
    components[~silhouette] = 0
    edge_image = cv2.resize((edges * 255).astype(np.uint8), (width, height), interpolation=cv2.INTER_LINEAR)
    edge_image[~silhouette] = 0
    return components, edge_image


def choose_regions(labels, box, person, *, max_regions=3):
    """Rank large coherent regions by box coverage, rejecting dominant spill outside the box."""
    h, w = labels.shape
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return np.zeros_like(person, dtype=np.uint8), []
    inside_box = np.zeros((h, w), dtype=bool)
    inside_box[y0:y1, x0:x1] = True
    ids, hits = np.unique(labels[inside_box & person], return_counts=True)
    ranked = []
    box_area = max(1, int(np.count_nonzero(inside_box & person)))
    for region_id, overlap in zip(ids, hits, strict=True):
        if region_id == 0:
            continue
        region_area = int(np.count_nonzero(labels == region_id))
        if region_area < max(12, box_area * 0.01):
            continue
        purity = float(overlap) / region_area
        coverage = float(overlap) / box_area
        score = coverage * np.sqrt(purity)
        if purity >= 0.20:
            ranked.append((score, int(region_id), coverage, purity))
    ranked.sort(reverse=True)
    selected = ranked[:max_regions]
    mask = np.isin(labels, [item[1] for item in selected]) & person
    return (mask.astype(np.uint8) * 255), [
        {"region": rid, "coverage": round(cov, 3), "purity": round(purity, 3), "score": round(score, 3)}
        for score, rid, cov, purity in selected
    ]


def main(argv=None):
    """Process images with one person model, NudeNet and depth inference per image."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/depth-volume-poc"))
    parser.add_argument("--person-model", type=Path, default=Path("tmp/models/yolo26m-seg.onnx"))
    parser.add_argument("--faces-model", type=Path, default=Path("tmp/models/nudenet-320n.onnx"))
    parser.add_argument("--depth-model", type=Path, default=Path("tmp/models/depth-anything-v2-small.onnx"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--resolution", type=int, default=384)
    parser.add_argument("--edge-threshold", type=float, default=0.30)
    parser.add_argument("--max-regions", type=int, default=3)
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args(argv)
    if args.resolution < 64 or not 0 < args.edge_threshold < 1 or args.max_regions < 1:
        parser.error("resolution must be >= 64, edge threshold in (0, 1), max regions >= 1")
    from dtflow.services import person_selection as selection

    for role, path in (("person", args.person_model), ("faces", args.faces_model)):
        ensure_checkpoint(path, CHECKPOINTS[role], allow_download=not args.no_download)
    person_ref = selection.describe_model(args.person_model)
    faces_ref = selection.describe_model(args.faces_model)
    person_session = selection._verified_session(person_ref, selection._segmentation_session)
    faces_session = selection._verified_session(faces_ref, selection._faces_session)
    depth = depth_session(args.depth_model, allow_download=not args.no_download, threads=args.threads)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in args.input_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file())
    for index, path in enumerate(files, 1):
        source = load_rgb(path)
        people = select_people(selection._instances(person_session, source, 0.25), source.size, 0, 1, False)
        if not people:
            print(f"{path.name}: no people", flush=True)
            continue
        _, detections, _ = detect_body_parts(faces_session, source, NUDENET_CONFIDENCE)
        z = estimate_depth(source, depth)
        rgb = np.asarray(source, dtype=np.uint8)
        prefix = f"{index:04d}_{path.stem}"
        records = []
        for person_index, (_, silhouette, _) in enumerate(people, 1):
            person = silhouette > 0
            regions, edges = volume_regions(z, person, resolution=args.resolution, edge_threshold=args.edge_threshold)
            seed = (regions.astype(np.uint32) * 2654435761) & 0xFFFFFFFF
            palette = np.stack(((seed >> 16) & 255, (seed >> 8) & 255, seed & 255), axis=-1).astype(np.uint8)
            palette[regions == 0] = 0
            Image.fromarray(palette).save(args.output_dir / f"{prefix}_regions.png")
            Image.fromarray(edges).save(args.output_dir / f"{prefix}_edges.png")
            overlay = rgb.copy()
            for detection_index, detection in enumerate(detections, 1):
                box = detection["box_xyxy"]
                x0, y0, x1, y1 = [int(v) for v in box]
                if (
                    np.count_nonzero(person[max(0, y0) : min(source.height, y1), max(0, x0) : min(source.width, x1)])
                    == 0
                ):
                    continue
                mask, selected = choose_regions(regions, box, person, max_regions=args.max_regions)
                if not selected:
                    continue
                name = f"{prefix}_d{detection_index:03d}"
                Image.fromarray(mask).save(args.output_dir / f"{name}_mask.png")
                cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 255, 255), 2)
                records.append(
                    {
                        "person": person_index,
                        "label": detection["label"],
                        "score": float(detection["score"]),
                        "box": [int(v) for v in box],
                        "mask": f"{name}_mask.png",
                        "regions": selected,
                    }
                )
            Image.fromarray(overlay).save(args.output_dir / f"{prefix}_boxes.png")
        (args.output_dir / f"{prefix}_regions.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"{path.name}: 1 person, {len(records)} masks -> {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

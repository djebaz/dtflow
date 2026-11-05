#!/usr/bin/env python3
"""CPU edge or SCHP LIP-20 score boundaries with NudeNet-seeded zones.

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
PIDINET_REVISION = "6b3f899da74e697a7d2c2e7cba10cfce057d6e55"
PIDINET_FILES = {
    "pidinet": (
        "pidinet_table5.onnx",
        144_170,
        "bca69bf456d073689303e2a56974baa2a6a1753b77425d8cac4986352093ce32",
        2_881_392,
        "e7c11c8f06033295d34c0e4e09f672728ff63544ee5a2103e211623a779c5c11",
    ),
    "pidinet-tiny": (
        "table5_pidinet_tiny.onnx",
        86_530,
        "05b1f8df5627f0a2f5a69c5adfddb353c3ad6fcc4feece089f8125f99b90244e",
        307_120,
        "fd11bdd6fa5358ba15212a3c8887ce9aed48bdbeb5cc2032f5612e7447062c9a",
    ),
}
LIP_REVISION = "7c0028fe34dcd8bd534ffa291a9086cdc856e4a9"
LIP_SPEC = (
    f"https://huggingface.co/pirocheto/schp-lip-20/resolve/{LIP_REVISION}/onnx/schp-lip-20-int8-static.onnx",
    68_314_352,
    "sha256",
    "871c9e209dc67d6be61c73dcc79cb49e638fcf5bbc35a19b4c7429eba0bfc046",
)


def edge_checkpoints(detector, model):
    """External weights retain their pinned basename, as referenced by the graph."""
    if detector == "dexined":
        return [(model, EDGE_SPEC)]
    if detector == "lip-20":
        return [(model, LIP_SPEC)]
    filename, size, digest, data_size, data_digest = PIDINET_FILES[detector]
    base = f"https://huggingface.co/bdck/PiDiNet_ONNX/resolve/{PIDINET_REVISION}"
    return [
        (model, (f"{base}/{filename}", size, "sha256", digest)),
        (model.parent / f"{filename}.data", (f"{base}/{filename}.data", data_size, "sha256", data_digest)),
    ]


def load_pidinet(path, threads, detector="pidinet"):
    """CPU-only ORT session; no PyTorch, export or GPU dependency at runtime."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise RuntimeError(f"{detector} must use ONNX Runtime CPU only")
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].type != "tensor(float)" or len(inputs[0].shape) != 4:
        raise RuntimeError(f"{detector} requires one float NCHW input")
    batch, channels, height, width = inputs[0].shape
    if (isinstance(batch, int) and batch != 1) or (isinstance(channels, int) and channels != 3):
        raise RuntimeError(f"Unsupported {detector} input: {inputs[0].shape}")
    default_size = 473 if detector == "lip-20" else 512
    size = (width if isinstance(width, int) else default_size, height if isinstance(height, int) else default_size)
    if min(size) < 1 or max(size) > 2048:
        raise RuntimeError(f"Unsupported {detector} spatial dimensions: {size}")
    if detector == "lip-20" and size != (473, 473):
        raise RuntimeError(f"Expected LIP-20 473-square input, got {size}")
    outputs = session.get_outputs()
    expected_output = "logits" if detector == "lip-20" else "fused"
    if not any(item.name == expected_output for item in outputs):
        raise RuntimeError(f"{detector} does not expose its expected {expected_output} output")
    return session, inputs[0].name, size


def pidinet_edges(cv, model, crop):
    """Publisher's RGB/ImageNet normalization; fused output already has sigmoid."""
    session, input_name, size = model
    rgb = np.asarray(crop.resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    rgb = (rgb - np.asarray([0.485, 0.456, 0.406], dtype=np.float32)) / np.asarray(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)
    output = np.asarray(session.run(["fused"], {input_name: tensor})[0])
    if output.ndim != 4 or output.shape[:2] != (1, 1) or not np.isfinite(output).all():
        raise RuntimeError(f"Invalid PiDiNet fused output: {output.shape}")
    if output.min() < -1e-5 or output.max() > 1.00001:
        raise RuntimeError("Expected PiDiNet probabilities, not logits")
    return cv.resize(np.clip(output[0, 0], 0, 1), crop.size, interpolation=cv.INTER_LINEAR)


def lip_score_boundaries(cv, model, crop, inside, resolution, sensitivity):
    """Use spatial changes in winning softmax scores, never class IDs as barriers.

    Restore logits before softmax on the bounded growth grid. Neighbor differences
    use only pairs inside the person, excluding artificial silhouette boundaries.
    """
    session, input_name, size = model
    rgb = np.asarray(crop.resize(size, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    rgb = (rgb - np.asarray([0.406, 0.456, 0.485], dtype=np.float32)) / np.asarray(
        [0.225, 0.224, 0.229], dtype=np.float32
    )
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)
    logits = np.asarray(session.run(["logits"], {input_name: tensor})[0])
    if logits.shape != (1, 20, 473, 473) or not np.isfinite(logits).all():
        raise RuntimeError(f"Invalid LIP-20 logits: {logits.shape}")
    scale = min(1.0, resolution / max(crop.size))
    work_size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    maximum = np.full((work_size[1], work_size[0]), -np.inf, dtype=np.float32)
    denominator = np.zeros_like(maximum)
    for plane in logits[0]:
        score = cv.resize(plane, work_size, interpolation=cv.INTER_LINEAR)
        updated = np.maximum(maximum, score)
        denominator = denominator * np.exp(maximum - updated) + np.exp(score - updated)
        maximum = updated
    confidence = 1.0 / denominator
    valid = cv.resize(inside.astype(np.uint8), work_size, interpolation=cv.INTER_NEAREST) > 0
    strength = np.zeros_like(confidence)
    for a, b in ((np.s_[:, :-1], np.s_[:, 1:]), (np.s_[:-1, :], np.s_[1:, :])):
        change = np.abs(confidence[a] - confidence[b]) * (valid[a] & valid[b])
        np.maximum(strength[a], change, out=strength[a])
        np.maximum(strength[b], change, out=strength[b])
    # Normalize the derived cost, not the confidence preview; record the exact scale.
    normalizer = max(float(np.percentile(strength[valid], 98)), 1e-3) if valid.any() else 1.0
    barriers = np.clip(sensitivity * strength / normalizer, 0, 1)
    barriers[~valid] = 0
    return (
        cv.resize(barriers, crop.size, interpolation=cv.INTER_LINEAR),
        cv.resize(confidence, crop.size, interpolation=cv.INTER_LINEAR),
        {
            "signal": "absolute neighboring winning-softmax-score differences",
            "normalizer_p98": normalizer,
            "working_size": list(work_size),
            "scores_calibrated": False,
            "semantic_class_constraints": False,
        },
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


def save_score_preview(cv, source, box, inside, scores, destination):
    """Show unnormalized model scores with a fixed 0..1 scale, not accuracy claims."""
    x0, y0, x1, y1 = box
    color = cv.applyColorMap(np.rint(np.clip(scores, 0, 1) * 255).astype(np.uint8), cv.COLORMAP_TURBO)
    color = color[:, :, ::-1]
    rgb = np.asarray(source).copy()
    region = rgb[y0:y1, x0:x1]
    region[inside] = (region[inside] * 0.35 + color[inside] * 0.65).astype(np.uint8)
    preview = Image.new("RGB", (source.width + 380, max(source.height, 320)), (24, 27, 34))
    preview.paste(Image.fromarray(rgb), (0, 0))
    draw, font = ImageDraw.Draw(preview), ImageFont.load_default(size=18)
    draw.text((source.width + 20, 20), "LIP-20 winning class score", fill="white", font=font)
    draw.text((source.width + 20, 54), "Uncalibrated; not an accuracy map", fill="white", font=font)
    for index, value in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
        bgr = cv.applyColorMap(np.array([[round(value * 255)]], dtype=np.uint8), cv.COLORMAP_TURBO)[0, 0]
        y = 100 + index * 36
        draw.rectangle((source.width + 20, y, source.width + 44, y + 24), fill=tuple(int(v) for v in bgr[::-1]))
        draw.text((source.width + 60, y), f"{value:.2f}", fill="white", font=font)
    preview.save(destination, quality=95, subsampling=0)


def save_outputs(source, box, inside, edges, zones, reports, output, prefix, detector):
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
    legend = (
        "LIP-20 score changes" if detector == "lip-20" else f"{detector} edges",
        "Red: stronger score variation" if detector == "lip-20" else "Red: stronger edge response",
        "Not an anatomical prediction",
    )
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
        r"\d{4,}_.+_(?:scores_overlay\.jpg|edges\.png|edges_overlay\.jpg|pascal_overlay\.jpg|zones_overlay\.jpg|"
        r"person_cutout\.png|report\.json|person\.png|confidence\.png|"
        r"(?:pascal|zones)_labels\.png|(?:pascal|zones)_\d+_[a-z_]+\.png)"
    )
    protected = {p.resolve() for p in (*files, args.model, args.person_model, args.nudenet_model)}
    protected.update(path.resolve() for path, _ in edge_checkpoints(args.detector, args.model))
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
    parser.add_argument("-b", "--detector", choices=("dexined", "pidinet", "pidinet-tiny", "lip-20"), default="dexined")
    parser.add_argument("-m", "--model", type=Path, help="Checkpoint path; default follows the selected detector")
    parser.add_argument(
        "-p", "--person-model", type=Path, default=Path("tmp/models/yolo26n-seg-ultralytics-v8.4.0.onnx")
    )
    parser.add_argument("-n", "--nudenet-model", type=Path, default=Path("tmp/models/nudenet-320n.onnx"))
    parser.add_argument(
        "-t", "--threads", type=int, default=4, help="Edge inference threads; person/parts use at most 4"
    )
    parser.add_argument(
        "-r", "--resolution", type=int, default=384, help="Region-growth dimension, not edge-model input"
    )
    parser.add_argument("-s", "--parts-confidence", type=float, default=0.25)
    parser.add_argument(
        "-v",
        "--sensitivity",
        type=float,
        default=1.0,
        help="LIP-20 score-boundary gain: >1 strengthens faint changes, <1 weakens them (default 1)",
    )
    parser.add_argument("-e", "--edge-weight", type=float, default=12, help="Higher values resist crossing edges more")
    parser.add_argument(
        "-g", "--growth", type=float, default=2.5, help="Path budget relative to detection half-diagonal"
    )
    parser.add_argument("-d", "--no-download", action="store_true")
    parser.add_argument("-w", "--overwrite", action="store_true", help="Delete recognized previous POC outputs only")
    args = parser.parse_args(argv)
    if args.model is None:
        if args.detector == "dexined":
            filename = "edge_detection_dexined_2024sep.onnx"
        elif args.detector == "lip-20":
            filename = "schp-lip-20-int8-static.onnx"
        else:
            filename = PIDINET_FILES[args.detector][0]
        args.model = Path("tmp/models") / filename
    if not args.input_dir.is_dir():
        parser.error("input-dir must be an existing directory")
    if not math.isfinite(args.sensitivity) or args.sensitivity <= 0:
        parser.error("sensitivity must be finite and > 0")
    if args.detector != "lip-20" and args.sensitivity != 1.0:
        parser.error("--sensitivity applies only to --detector lip-20")
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
    edge_files = edge_checkpoints(args.detector, args.model)
    for path, spec in (
        *edge_files,
        (args.person_model, CHECKPOINTS["person"]),
        (args.nudenet_model, CHECKPOINTS["faces"]),
    ):
        verified_checkpoint(path, spec, not args.no_download)
    cv.setNumThreads(args.threads)
    if args.detector == "dexined":
        net = cv.dnn.readNetFromONNX(str(args.model))
        net.setPreferableBackend(cv.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv.dnn.DNN_TARGET_CPU)
    else:
        net = load_pidinet(args.model, args.threads, args.detector)
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
                "algorithm": f"{args.detector}-nudenet-edge-growth-v1",
                "people": len(people),
                "edge_backend": "OpenCV DNN CPU" if args.detector == "dexined" else "ONNX Runtime CPU",
                "detector_backend": "ONNX Runtime CPU",
                "edge_detector": args.detector,
                "edge_revision": (
                    REVISION
                    if args.detector == "dexined"
                    else LIP_REVISION
                    if args.detector == "lip-20"
                    else PIDINET_REVISION
                ),
                "edge_sha256": edge_files[0][1][3],
                "edge_checkpoints": {path.name: spec[3] for path, spec in edge_files},
                "settings": {
                    "edge_weight": args.edge_weight,
                    "growth": args.growth,
                    "resolution": args.resolution,
                    "parts_confidence": args.parts_confidence,
                    "sensitivity": args.sensitivity,
                },
            }
            if people:
                _, mask, person_box = people[0]
                box = tuple(int(v) for v in person_crop_box(person_box, source.size))
                x0, y0, x1, y1 = box
                crop, inside = source.crop(box), mask[y0:y1, x0:x1] > 0
                tick = time.perf_counter()
                scores = None
                if args.detector == "dexined":
                    edges = detect_edges(cv, net, crop)
                elif args.detector == "lip-20":
                    edges, scores, score_details = lip_score_boundaries(
                        cv, net, crop, inside, args.resolution, args.sensitivity
                    )
                    report["score_boundaries"] = score_details
                else:
                    edges = pidinet_edges(cv, net, crop)
                report["edge_seconds"] = time.perf_counter() - tick
                _, detections, _ = detect_body_parts(parts_session, crop, args.parts_confidence)
                detections = [d for d in detections if not d["label"].startswith("ARMPITS_")]
                tick = time.perf_counter()
                zones, details = grow_zones(
                    cv, edges, inside, detections, args.resolution, args.edge_weight, args.growth
                )
                report["growth_seconds"] = time.perf_counter() - tick
                names = save_outputs(source, box, inside, edges, zones, details, output, prefix, args.detector)
                if scores is not None:
                    save_score_preview(cv, source, box, inside, scores, output / f"{prefix}_scores_overlay.jpg")
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

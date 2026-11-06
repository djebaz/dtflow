#!/usr/bin/env python3
"""Compare custom YOLOv8 segmentation and oriented-box ONNX models on CPU.

Full-image inference, flat labeled overlays, no NudeNet/SAM/person-mask clipping.
Export the trusted .pt checkpoints separately; this script never unpickles them.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import cv2 as cv
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from schp_atr18_smoke import IMAGE_SUFFIXES, load_rgb

PALETTE = {
    "objects": (150, 150, 150),
    "balls": (255, 165, 30),
    "dick": (255, 65, 170),
    "dick-head": (0, 210, 255),
    "hand": (80, 220, 85),
}


def load_model(path, task, threads):
    import onnxruntime as ort

    if path.suffix.lower() != ".onnx" or not path.is_file():
        raise FileNotFoundError(
            f"Missing ONNX {task} model: {path}. Export the .pt checkpoint first; PyTorch is not used by this POC."
        )
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise RuntimeError("Custom models must use ONNX Runtime CPU only")
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or inputs[0].type != "tensor(float)" or len(inputs[0].shape) != 4:
        raise RuntimeError(f"{task}: export with half=False, dynamic=False, batch=1")
    shape = inputs[0].shape
    if shape[:2] != [1, 3] or any(not isinstance(v, int) or not 64 <= v <= 2048 for v in shape[2:]):
        raise RuntimeError(f"{task}: expected static [1,3,H,W] input, got {shape}")
    meta = session.get_modelmeta().custom_metadata_map
    try:
        names = ast.literal_eval(meta.get("names", "{}"))
    except (ValueError, SyntaxError) as exc:
        raise RuntimeError(f"{task}: invalid class metadata") from exc
    if meta.get("task") != task or not isinstance(names, dict) or sorted(names) != list(range(len(names))) or not names:
        raise RuntimeError(f"{task}: expected Ultralytics {task} class metadata")
    names = {int(k): str(v) for k, v in names.items()}
    if any(name not in PALETTE for name in names.values()):
        raise RuntimeError(f"Unexpected custom classes: {names}")
    if len(outputs) != (2 if task == "segment" else 1) or any(item.type != "tensor(float)" for item in outputs):
        raise RuntimeError(f"{task}: expected raw YOLOv8 outputs; export with nms=False")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return session, names, {"filename": path.name, "sha256": digest, "input_shape": shape, "classes": names}


def preprocess(image, shape):
    height, width = shape[2:]
    gain = min(width / image.width, height / image.height)
    rw, rh = max(1, round(image.width * gain)), max(1, round(image.height * gain))
    left, top = round((width - rw) / 2 - 0.1), round((height - rh) / 2 - 0.1)
    resized = cv.resize(np.asarray(image), (rw, rh), interpolation=cv.INTER_LINEAR)
    canvas = np.full((height, width, 3), 114, dtype=np.uint8)
    canvas[top : top + rh, left : left + rw] = resized
    tensor = np.ascontiguousarray(canvas.astype(np.float32).transpose(2, 0, 1)[None] / np.float32(255))
    return tensor, {"gain": gain, "padding_xy": [left, top], "resized": [rw, rh]}


def corners(row):
    cx, cy, width, height, angle = (float(v) for v in row)
    return cv.boxPoints(((cx, cy), (width, height), math.degrees(angle)))


def suppress(rows, scores, classes, task, threshold, maximum):
    """Class-aware greedy NMS; OBB uses exact rotated-polygon IoU, not ProbIoU."""
    order = np.argsort(-scores, kind="stable")
    polygons = [corners(np.concatenate((row[:4], row[-1:]))) for row in rows] if task == "obb" else None
    kept = []
    while len(order) and len(kept) < maximum:
        index = int(order[0])
        kept.append(index)
        others = order[1:]
        if not len(others):
            break
        ious = np.zeros(len(others), dtype=np.float32)
        same = np.flatnonzero(classes[others] == classes[index])
        if task == "obb":
            area = rows[index, 2] * rows[index, 3]
            for position in same:
                other = int(others[position])
                overlap, _ = cv.intersectConvexConvex(polygons[index], polygons[other])
                ious[position] = overlap / max(float(area + rows[other, 2] * rows[other, 3] - overlap), 1e-6)
        elif len(same):
            a, b = rows[index, :4], rows[others[same], :4]
            amin, amax = a[:2] - a[2:] / 2, a[:2] + a[2:] / 2
            bmin, bmax = b[:, :2] - b[:, 2:] / 2, b[:, :2] + b[:, 2:] / 2
            intersection = np.maximum(np.minimum(amax, bmax) - np.maximum(amin, bmin), 0).prod(axis=1)
            ious[same] = intersection / np.maximum(a[2:].prod() + b[:, 2:].prod(axis=1) - intersection, 1e-6)
        order = others[ious <= threshold]
    return kept


def predict(session, names, source, task, args):
    tensor, fit = preprocess(source, session.get_inputs()[0].shape)
    tick = time.perf_counter()
    output = session.run(None, {session.get_inputs()[0].name: tensor})
    seconds = time.perf_counter() - tick
    if any(not np.isfinite(item).all() for item in output):
        raise RuntimeError(f"{task}: nonfinite model output")
    raw = output[0]
    if raw.ndim != 3 or raw.shape[0] != 1:
        raise RuntimeError(f"{task}: unexpected raw output {raw.shape}")
    proto = output[1][0] if task == "segment" and output[1].ndim == 4 and output[1].shape[0] == 1 else None
    if task == "segment" and proto is None:
        raise RuntimeError("Expected segmentation mask prototypes [1,C,H,W]")
    channels = 4 + len(names) + (proto.shape[0] if task == "segment" else 1)
    if raw.shape[1] != channels:
        raise RuntimeError(f"{task}: expected raw [1,{channels},N], got {raw.shape}; use YOLOv8 nms=False export")
    rows = raw[0].T
    classes = rows[:, 4 : 4 + len(names)].argmax(axis=1)
    scores = rows[np.arange(len(rows)), 4 + classes]
    accepted = (scores >= args.confidence) & (rows[:, 2] > 0) & (rows[:, 3] > 0)
    if args.classes:
        accepted &= np.isin(classes, [key for key, name in names.items() if name in args.classes])
    rows, scores, classes = rows[accepted], scores[accepted], classes[accepted]
    candidates = len(rows)
    order = np.argsort(-scores, kind="stable")[:3000]
    rows, scores, classes = rows[order], scores[order], classes[order]
    kept = suppress(rows, scores, classes, task, args.nms, args.max_det)
    rgb = np.asarray(source).copy()
    records = []
    gain, (left, top) = fit["gain"], fit["padding_xy"]
    height, width = tensor.shape[2:]
    rw, rh = fit["resized"]
    for index in kept:
        row, class_id = rows[index], int(classes[index])
        record = {"class_id": class_id, "class": names[class_id], "score": float(scores[index])}
        color = PALETTE[names[class_id]]
        if task == "obb":
            polygon = corners(np.concatenate((row[:4], row[-1:])))
            polygon = (polygon - np.array([left, top], dtype=np.float32)) / gain
            cv.polylines(rgb, [np.rint(polygon * 256).astype(np.int32)], True, color, 2, cv.LINE_AA, shift=8)
            # Undirected long-axis line: orientation, not base-to-tip anatomical direction.
            cx, cy = (row[0] - left) / gain, (row[1] - top) / gain
            angle = float(row[-1]) + (math.pi / 2 if row[3] > row[2] else 0)
            length = max(float(row[2]), float(row[3])) / gain / 2
            delta = np.array([math.cos(angle), math.sin(angle)]) * length
            ends = np.rint((np.array([cx, cy]) + np.stack((-delta, delta))) * 256).astype(np.int32)
            cv.line(rgb, tuple(ends[0]), tuple(ends[1]), color, 1, cv.LINE_AA, shift=8)
            record.update(polygon_xy=polygon.tolist(), long_axis_degrees=math.degrees(angle) % 180)
            record["label_xy"] = [float(polygon[:, 0].min()), float(polygon[:, 1].min())]
        else:
            box = np.concatenate((row[:2] - row[2:4] / 2, row[:2] + row[2:4] / 2)).astype(np.float32)
            box[[0, 2]] = ((box[[0, 2]] - left) / gain).clip(0, source.width)
            box[[1, 3]] = ((box[[1, 3]] - top) / gain).clip(0, source.height)
            logits = (row[4 + len(names) :] @ proto.reshape(proto.shape[0], -1)).reshape(proto.shape[1:])
            enlarged = cv.resize(logits, (width, height), interpolation=cv.INTER_LINEAR)
            restored = cv.resize(enlarged[top : top + rh, left : left + rw], source.size, interpolation=cv.INTER_LINEAR)
            yy, xx = np.ogrid[: source.height, : source.width]
            # Native-style mask restoration: crop to the learned detection box after upsampling.
            mask = (restored > 0) & (xx >= box[0]) & (xx < box[2]) & (yy >= box[1]) & (yy < box[3])
            rgb[mask] = ((1 - args.alpha) * rgb[mask] + args.alpha * np.array(color)).astype(np.uint8)
            contours, _ = cv.findContours(mask.astype(np.uint8), cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
            cv.drawContours(rgb, contours, -1, color, 2, cv.LINE_AA)
            record.update(box_xyxy=box.tolist(), pixels=int(mask.sum()), label_xy=box[:2].tolist())
        records.append(record)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=16)
    for record in records:
        x, y = record.pop("label_xy")
        x, y = max(0, min(source.width - 120, int(x))), max(0, min(source.height - 20, int(y)))
        draw.text(
            (x, y),
            f"{record['class']} {record['score']:.2f}",
            font=font,
            fill=PALETTE[record["class"]],
            stroke_width=2,
            stroke_fill="black",
        )
    info = {
        "inference_seconds": seconds,
        "preprocessing": fit,
        "candidates": candidates,
        "pre_nms_limit": 3000,
        "pre_nms_truncated": candidates > 3000,
        "detections": records,
    }
    return image, info


def frame(image, title, names):
    result = Image.new("RGB", (image.width, image.height + 98), (22, 22, 26))
    result.paste(image, (0, 54))
    draw = ImageDraw.Draw(result)
    draw.text((10, 8), title, fill="white", font=ImageFont.load_default(size=18))
    # A compact legend is repeated per panel, with colors fixed across both models.
    font = ImageFont.load_default(size=14)
    x, y = 10, image.height + 61
    for name in names.values():
        item_width = int(draw.textlength(name, font=font)) + 30
        if x + item_width > image.width and x > 10:
            x, y = 10, y + 19
        draw.rectangle((x, y + 2, x + 10, y + 12), fill=PALETTE[name])
        draw.text((x + 15, y), name, font=font, fill="white")
        x += item_width
    return result


def save_image(image, path, size):
    if size:
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
    image.save(path, quality=93)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("tmp/custom-parts-poc"))
    parser.add_argument("-sm", "--seg-model", type=Path, default=Path("tmp/models/best_seg_dick.onnx"))
    parser.add_argument("-om", "--obb-model", type=Path, default=Path("tmp/models/best_obb_dick_lastmile.onnx"))
    parser.add_argument("-s", "--confidence", type=float, default=0.25)
    parser.add_argument("-n", "--nms", type=float, default=0.5)
    parser.add_argument("-m", "--max-det", type=int, default=50)
    parser.add_argument("-t", "--threads", type=int, default=2)
    parser.add_argument("-a", "--alpha", type=float, default=0.35)
    parser.add_argument(
        "-c", "--classes", nargs="+", choices=tuple(PALETTE), help="Optional class-name filter; default all"
    )
    parser.add_argument(
        "-r", "--size", type=int, default=2560, help="Saved image maximum long side, 0 keeps native resolution"
    )
    parser.add_argument(
        "-cp", "--compare", action="store_true", help="One side-by-side image instead of two separate overlays"
    )
    parser.add_argument("-f", "--images-only", action="store_true")
    parser.add_argument(
        "-w", "--overwrite", action="store_true", help="Replace only recognized custom_parts_poc flat outputs"
    )
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error("input-dir must exist")
    if (
        not 0 < args.confidence <= 1
        or not 0 < args.nms < 1
        or not math.isfinite(args.alpha)
        or not 0 <= args.alpha <= 1
    ):
        parser.error("confidence (0,1], nms (0,1), alpha [0,1] required")
    if args.threads < 1 or not 1 <= args.max_det <= 200 or (args.size != 0 and args.size < 256):
        parser.error("threads >= 1, max-det 1..200, size 0 or >= 256 required")
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        parser.error("No supported still images")
    output, source_dir = args.output_dir.resolve(), args.input_dir.resolve()
    if (
        output == source_dir
        or output in source_dir.parents
        or args.output_dir.is_symlink()
        or args.output_dir.is_junction()
    ):
        parser.error("output-dir must not redirect or contain input-dir")
    previous = list(output.iterdir()) if output.exists() else []
    pattern = re.compile(r"cpp_\d{4,}_.+_(?:seg|obb|compare|report)\.(?:jpg|json)$")
    if previous and (
        not args.overwrite
        or any(p.is_symlink() or p.is_junction() or not p.is_file() or not pattern.fullmatch(p.name) for p in previous)
    ):
        parser.error("Output is not empty; -w replaces recognized custom_parts_poc outputs only")
    seg, seg_names, seg_model = load_model(args.seg_model, "segment", args.threads)
    obb, obb_names, obb_model = load_model(args.obb_model, "obb", args.threads)
    cv.setNumThreads(args.threads)
    output.mkdir(parents=True, exist_ok=True)
    for entry in previous:
        if entry.is_symlink() or entry.is_junction() or not entry.is_file() or entry.resolve().parent != output:
            raise RuntimeError(f"Output changed before cleanup: {entry.name}")
        entry.unlink()
    print(
        f"ONNX Runtime CPU: {len(files)} images; seg {seg_model['input_shape'][2:]}; OBB {obb_model['input_shape'][2:]}",
        flush=True,
    )
    failures = 0
    for index, path in enumerate(files, 1):
        prefix = f"cpp_{index:04d}_{path.stem[:70]}"
        try:
            started = time.perf_counter()
            source = load_rgb(path)
            seg_overlay, seg_report = predict(seg, seg_names, source, "segment", args)
            obb_overlay, obb_report = predict(obb, obb_names, source, "obb", args)
            seg_panel = frame(seg_overlay, f"SEG masks / {seg_report['inference_seconds']:.2f}s", seg_names)
            obb_panel = frame(obb_overlay, f"OBB + long axis / {obb_report['inference_seconds']:.2f}s", obb_names)
            if args.compare:
                combined = Image.new(
                    "RGB", (seg_panel.width + obb_panel.width, max(seg_panel.height, obb_panel.height)), (22, 22, 26)
                )
                combined.paste(seg_panel, (0, 0))
                combined.paste(obb_panel, (seg_panel.width, 0))
                save_image(combined, output / f"{prefix}_compare.jpg", args.size)
            else:
                save_image(seg_panel, output / f"{prefix}_seg.jpg", args.size)
                save_image(obb_panel, output / f"{prefix}_obb.jpg", args.size)
            seconds = time.perf_counter() - started
            if not args.images_only:
                report = {
                    "source": path.name,
                    "source_size": source.size,
                    "settings": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                    "models": {"seg": seg_model, "obb": obb_model},
                    "seg": seg_report,
                    "obb": obb_report,
                    "seconds": seconds,
                    "notes": [
                        "Full-image RGB inference; aspect-preserving centered letterbox with fill 114",
                        "Masks use native-style logits restoration, threshold 0 and predicted-box crop",
                        "OBB NMS uses exact polygon IoU, not Ultralytics ProbIoU",
                        "OBB long axis is undirected, not an anatomical base-to-tip prediction",
                    ],
                }
                (output / f"{prefix}_report.json").write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
                )
            print(
                f"{path.name}: {len(seg_report['detections'])} masks, {len(obb_report['detections'])} rotated boxes; {seconds:.2f}s",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

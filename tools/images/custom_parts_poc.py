#!/usr/bin/env python3
"""Compare custom YOLO segmentation/detection and YOLOv8/YOLO26 OBB ONNX models on CPU.

Flat labeled overlays, optional NudeNet-context segmentation, no SAM/person-mask clipping.
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
    "penis": (255, 65, 170),
}
MASK_VARIANTS = tuple(
    (f"threshold_{threshold:+d}_clip_{int(clip)}", threshold, clip)
    for clip in (True, False)
    for threshold in (-1, 0, 1)
)


def paint_mask(rgb, mask, color, alpha):
    rgb[mask] = ((1 - alpha) * rgb[mask] + alpha * np.array(color)).astype(np.uint8)
    contours, _ = cv.findContours(mask.astype(np.uint8), cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
    cv.drawContours(rgb, contours, -1, color, 2, cv.LINE_AA)


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
    end2end = str(meta.get("end2end", "false")).lower() == "true"
    if end2end and task != "obb":
        raise RuntimeError("This POC currently supports end-to-end outputs only for OBB")
    if end2end and (len(outputs[0].shape) != 3 or outputs[0].shape[0] != 1 or outputs[0].shape[-1] != 7):
        raise RuntimeError("End-to-end OBB must output [1,N,7]: cx,cy,w,h,score,class,angle")
    return (
        session,
        names,
        {
            "filename": path.name,
            "sha256": digest,
            "input_shape": shape,
            "classes": names,
            "head": meta.get("head"),
            "end2end": end2end,
            "output_layout": "obb_xywh_score_class_angle" if end2end else "raw_channels_first",
        },
    )


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


def predict(session, names, source, task, args, *, filter_classes=True, variant_overlays=None, mask_sink=None):
    """Optionally fill a six-variant overlay sink, reusing restored logits from this one call."""
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
    end2end = str(session.get_modelmeta().custom_metadata_map.get("end2end", "false")).lower() == "true"
    default = (
        args.seg_confidence if task == "segment" else args.obb_confidence if task == "obb" else args.detect_confidence
    )
    overrides = args.seg_class_conf if task == "segment" else args.obb_class_conf if task == "obb" else {}
    thresholds = np.array([overrides.get(names[key], default) for key in range(len(names))], dtype=np.float32)
    if end2end:
        # OBB uses cx,cy,w,h even in end-to-end mode; ordinary Detect uses xyxy instead.
        if task != "obb" or raw.shape[2] != 7:
            raise RuntimeError(f"Unsupported end-to-end {task} output: {raw.shape}")
        values = raw[0]
        rounded = np.rint(values[:, 5])
        if np.any(rounded != values[:, 5]) or np.any(rounded < 0) or np.any(rounded >= len(names)):
            raise RuntimeError("Invalid end-to-end OBB class indices")
        classes = rounded.astype(np.int32)
        scores = values[:, 4]
        rows = np.concatenate((values[:, :4], values[:, 6:7]), axis=1)
        accepted = (scores >= thresholds[classes]) & (rows[:, 2] > 0) & (rows[:, 3] > 0)
        if args.classes and filter_classes:
            accepted &= np.isin(classes, [key for key, name in names.items() if name in args.classes])
    else:
        channels = 4 + len(names) + (proto.shape[0] if task == "segment" else 1 if task == "obb" else 0)
        if raw.shape[1] != channels:
            raise RuntimeError(
                f"{task}: expected raw [1,{channels},N], got {raw.shape}; export a raw head or a supported end-to-end OBB head"
            )
        rows = raw[0].T
        class_scores = rows[:, 4 : 4 + len(names)]
        eligible = class_scores >= thresholds[None]
        if args.classes and filter_classes:
            eligible &= np.array([names[key] in args.classes for key in range(len(names))])[None]
        # Threshold/filter before argmax: an ineligible class cannot hide an eligible alternative.
        classes = np.where(eligible, class_scores, -1).argmax(axis=1)
        scores = class_scores[np.arange(len(rows)), classes]
        accepted = eligible.any(axis=1) & (rows[:, 2] > 0) & (rows[:, 3] > 0)
    rows, scores, classes = rows[accepted], scores[accepted], classes[accepted]
    candidates = len(rows)
    order = np.argsort(-scores, kind="stable")[:3000]
    rows, scores, classes = rows[order], scores[order], classes[order]
    kept = (
        list(range(min(len(rows), args.max_det)))
        if end2end
        else suppress(rows, scores, classes, task, args.nms, args.max_det)
    )
    rgb = np.asarray(source).copy()
    variants = MASK_VARIANTS if variant_overlays is not None and task == "segment" else ()
    variant_pixels = {key: rgb.copy() for key, _, _ in variants}
    variant_reports = {
        key: {"mask_threshold": threshold, "crop_to_seg_box": clip, "detections": []}
        for key, threshold, clip in variants
    }
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
        elif task == "detect":
            box = np.concatenate((row[:2] - row[2:4] / 2, row[:2] + row[2:4] / 2)).astype(np.float32)
            box[[0, 2]] = ((box[[0, 2]] - left) / gain).clip(0, source.width)
            box[[1, 3]] = ((box[[1, 3]] - top) / gain).clip(0, source.height)
            cv.rectangle(
                rgb, tuple(np.rint(box[:2]).astype(int)), tuple(np.rint(box[2:]).astype(int)), color, 2, cv.LINE_AA
            )
            record.update(box_xyxy=box.tolist(), label_xy=box[:2].tolist())
        else:
            box = np.concatenate((row[:2] - row[2:4] / 2, row[:2] + row[2:4] / 2)).astype(np.float32)
            box[[0, 2]] = ((box[[0, 2]] - left) / gain).clip(0, source.width)
            box[[1, 3]] = ((box[[1, 3]] - top) / gain).clip(0, source.height)
            logits = (row[4 + len(names) :] @ proto.reshape(proto.shape[0], -1)).reshape(proto.shape[1:])
            enlarged = cv.resize(logits, (width, height), interpolation=cv.INTER_LINEAR)
            restored = cv.resize(enlarged[top : top + rh, left : left + rw], source.size, interpolation=cv.INTER_LINEAR)
            yy, xx = np.ogrid[: source.height, : source.width]
            box_mask = (xx >= box[0]) & (xx < box[2]) & (yy >= box[1]) & (yy < box[3])
            mask = restored > args.mask_threshold
            if not args.uncrop:
                mask &= box_mask
            paint_mask(rgb, mask, color, args.alpha)
            for key, threshold, clip in variants:
                candidate = restored > threshold
                if clip:
                    candidate &= box_mask
                paint_mask(variant_pixels[key], candidate, color, args.alpha)
                variant_reports[key]["detections"].append(
                    dict(record, box_xyxy=box.tolist(), pixels=int(candidate.sum()))
                )
            record.update(box_xyxy=box.tolist(), pixels=int(mask.sum()), label_xy=box[:2].tolist())
            if mask_sink is not None:
                mask_sink.append({"class": names[class_id], "score": float(scores[index]), "mask": mask})
        records.append(record)

    def labeled(pixels):
        image = Image.fromarray(pixels)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=16)
        for record in records:
            x, y = record["label_xy"]
            x, y = max(0, min(source.width - 120, int(x))), max(0, min(source.height - 20, int(y)))
            draw.text(
                (x, y),
                f"{record['class']} {record['score']:.2f}",
                font=font,
                fill=PALETTE[record["class"]],
                stroke_width=2,
                stroke_fill="black",
            )
        return image

    image = labeled(rgb)
    if variant_overlays is not None:
        variant_overlays.update({key: labeled(pixels) for key, pixels in variant_pixels.items()})
    for record in records:
        record.pop("label_xy")
    info = {
        "inference_seconds": seconds,
        "preprocessing": fit,
        "candidates": candidates,
        "pre_nms_limit": 3000,
        "pre_nms_truncated": candidates > 3000,
        "class_thresholds": {name: float(thresholds[key]) for key, name in names.items()},
        "detections": records,
        "output_layout": "obb_xywh_score_class_angle" if end2end else "raw_channels_first",
        "nms_applied": not end2end,
        "mask_threshold": args.mask_threshold if task == "segment" else None,
        "crop_to_seg_box": not args.uncrop if task == "segment" else None,
    }
    if variants:
        info["mask_variants"] = variant_reports
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


def context_segmentation(session, names, nudenet, source, args):
    """Infer on merged, non-overlapping context crops and restore source coordinates."""
    from schp_atr18_smoke import detect_body_parts

    tick = time.perf_counter()
    _, found, _ = detect_body_parts(
        nudenet, source, args.nn_confidence, input_side=640 if args.nudenet_size == "640m" else 320
    )
    targets = [d for d in found if d["label"] == "MALE_GENITALIA_EXPOSED"]
    nn_seconds = time.perf_counter() - tick
    overlay, info = segment_context_regions(session, names, source, args, targets)
    info.update(nudenet_seconds=nn_seconds, nudenet_targets=targets, context_provider="nudenet")
    return overlay, info


def segment_context_regions(session, names, source, args, targets, *, variant_overlays=None, mask_sink=None):
    """Shared crop segmentation; detector boxes are context anchors, not mask clips."""
    crops = []
    for detection in targets:
        x0, y0, x1, y1 = detection["box_xyxy"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = args.context * (x1 - x0) / 2, args.context * (y1 - y0) / 2
        if args.context_shape == "square":
            rx = ry = max(rx, ry)
        box = (
            max(0, math.floor(cx - rx)),
            max(0, math.floor(cy - ry)),
            min(source.width, math.ceil(cx + rx)),
            min(source.height, math.ceil(cy + ry)),
        )
        if box[2] > box[0] and box[3] > box[1]:
            crops.append(box)
    # Merging prevents duplicate predictions and one opaque crop paste erasing another.
    changed = True
    while changed:
        changed = False
        for i in range(len(crops)):
            for j in range(i + 1, len(crops)):
                a, b = crops[i], crops[j]
                if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                    crops[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    crops.pop(j)
                    changed = True
                    break
            if changed:
                break
    result = source.copy()
    variant_reports = {}
    if variant_overlays is not None:
        variant_overlays.update({key: source.copy() for key, _, _ in MASK_VARIANTS})
        variant_reports = {
            key: {"mask_threshold": threshold, "crop_to_seg_box": clip, "detections": []}
            for key, threshold, clip in MASK_VARIANTS
        }
    records, regions = [], []
    seconds = 0.0
    class_thresholds = {name: args.seg_class_conf.get(name, args.seg_confidence) for name in names.values()}
    for roi_index, box in enumerate(crops, 1):
        local_variants = {} if variant_overlays is not None else None
        local_masks = [] if mask_sink is not None else None
        overlay, info = predict(
            session, names, source.crop(box), "segment", args, variant_overlays=local_variants, mask_sink=local_masks
        )
        if local_masks is not None:
            mask_sink.extend(dict(item, offset_xy=list(box[:2]), roi=roi_index) for item in local_masks)
        result.paste(overlay, box[:2])
        if local_variants is not None:
            for key, image in local_variants.items():
                variant_overlays[key].paste(image, box[:2])
                for detection in info["mask_variants"][key]["detections"]:
                    x0, y0, x1, y1 = detection["box_xyxy"]
                    variant_reports[key]["detections"].append(
                        dict(detection, box_xyxy=[x0 + box[0], y0 + box[1], x1 + box[0], y1 + box[1]], roi=roi_index)
                    )
        seconds += info["inference_seconds"]
        for detection in info["detections"]:
            x0, y0, x1, y1 = detection["box_xyxy"]
            records.append(
                dict(detection, box_xyxy=[x0 + box[0], y0 + box[1], x1 + box[0], y1 + box[1]], roi=roi_index)
            )
        regions.append(
            {
                "source_box_xyxy": box,
                "inference_seconds": info["inference_seconds"],
                "preprocessing": info["preprocessing"],
                "candidates": info["candidates"],
                "pre_nms_truncated": info["pre_nms_truncated"],
            }
        )
    for image in (result, *(variant_overlays.values() if variant_overlays is not None else ())):
        draw = ImageDraw.Draw(image)
        for box in crops:
            draw.rectangle(box, outline=(255, 220, 30), width=2)
        for detection in targets:
            draw.rectangle(tuple(detection["box_xyxy"]), outline="white", width=2)
    return result, {
        "status": "segmented" if crops else "no_target_seg_skipped",
        "inference_seconds": seconds,
        "context_targets": targets,
        "regions": regions,
        "detections": records,
        "class_thresholds": class_thresholds,
        "detection_coordinates": "source image",
        "roi_merge": "overlapping context boxes merged before inference",
        "mask_variants": variant_reports,
    }


def class_confidence(values, parser):
    result = {}
    for value in values:
        try:
            name, raw = value.split("=", 1)
            threshold = float(raw)
        except ValueError:
            parser.error(f"Class threshold must be name=value, got {value!r}")
        if name not in PALETTE or not 0 < threshold <= 1:
            parser.error(f"Class threshold needs a known class and value in (0,1], got {value!r}")
        result[name] = threshold
    return result


def save_image(image, path, size):
    if size:
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
    image.save(path, quality=93)


def measure_obb_coverage(detections, masks, source_size, minimum):
    """Greedy one-to-one same-class mask/OBB matching by raster IoU, in source pixels."""
    measurements, candidates = [], []
    width, height = source_size
    for obb_index, detection in enumerate(detections):
        polygon = np.asarray(detection["polygon_xy"], dtype=np.float64)
        low = np.maximum(np.floor(polygon.min(axis=0)).astype(int), 0)
        high = np.minimum(np.ceil(polygon.max(axis=0)).astype(int) + 1, [width, height])
        x0, y0 = low
        x1, y1 = high
        area = 0
        if x1 > x0 and y1 > y0:
            raster = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
            cv.fillPoly(raster, [np.rint((polygon - low) * 256).astype(np.int32)], 1, shift=8)
            area = int(raster.sum())
            for mask_index, item in enumerate(masks):
                if item["class"] != detection["class"]:
                    continue
                mask = item["mask"]
                mx, my = item["offset_xy"]
                mh, mw = mask.shape
                ax, ay = max(x0, mx), max(y0, my)
                bx, by = min(x1, mx + mw), min(y1, my + mh)
                if bx <= ax or by <= ay:
                    continue
                intersection = int(
                    np.count_nonzero(
                        mask[ay - my : by - my, ax - mx : bx - mx] & (raster[ay - y0 : by - y0, ax - x0 : bx - x0] != 0)
                    )
                )
                if intersection:
                    mask_area = int(np.count_nonzero(mask))
                    iou = intersection / (area + mask_area - intersection)
                    candidates.append((iou, obb_index, mask_index, intersection, mask_area))
        measurements.append(
            {
                "obb_index": obb_index,
                "class": detection["class"],
                "obb_score": detection["score"],
                "visible_obb_pixels": area,
                "seg_index": None,
                "seg_score": None,
                "intersection_pixels": 0,
                "coverage": 0.0,
                "mask_iou": 0.0,
                "issue": minimum > 0 or not area,
                "reason": "no_matched_same_class_mask" if area else "obb_outside_image",
            }
        )
    used_obb, used_masks = set(), set()
    for iou, obb_index, mask_index, intersection, mask_area in sorted(candidates, reverse=True):
        if obb_index in used_obb or mask_index in used_masks:
            continue
        used_obb.add(obb_index)
        used_masks.add(mask_index)
        record = measurements[obb_index]
        coverage = intersection / record["visible_obb_pixels"]
        record.update(
            seg_index=mask_index,
            seg_score=masks[mask_index]["score"],
            intersection_pixels=intersection,
            coverage=coverage,
            mask_iou=iou,
            seg_pixels=mask_area,
            spill_fraction=(mask_area - intersection) / mask_area,
            issue=coverage < minimum,
            reason="low_coverage" if coverage < minimum else "ok",
        )
    return {
        "minimum_coverage": minimum,
        "definition": "same-class matched SEG pixels inside visible rasterized OBB / visible rasterized OBB pixels",
        "matching": "greedy one-to-one by mask/OBB raster IoU; indices reference OBB and context SEG detections",
        "issues": sum(record["issue"] for record in measurements),
        "measurements": measurements,
        "unmatched_seg_indices": [i for i in range(len(masks)) if i not in used_masks],
    }


def draw_coverage_issues(image, detections, coverage):
    pixels = np.asarray(image).copy()
    for record in coverage["measurements"]:
        if record["issue"]:
            polygon = np.asarray(detections[record["obb_index"]]["polygon_xy"])
            cv.polylines(pixels, [np.rint(polygon * 256).astype(np.int32)], True, (255, 70, 70), 3, cv.LINE_AA, shift=8)
    result = Image.fromarray(pixels)
    draw = ImageDraw.Draw(result)
    for record in coverage["measurements"]:
        if record["issue"]:
            polygon = np.asarray(detections[record["obb_index"]]["polygon_xy"])
            x, y = polygon.max(axis=0)
            text = f"LOW SEG {record['class']}: {record['coverage']:.1%} < {coverage['minimum_coverage']:.0%}"
            x = max(0, min(float(x), result.width - draw.textlength(text) - 2))
            y = max(0, min(float(y) + 3, result.height - 14))
            draw.text((x, y), text, fill=(255, 90, 90), stroke_width=2, stroke_fill="black")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input-dir", type=Path, required=True)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("tmp/custom-parts-poc"))
    parser.add_argument("-sm", "--seg-model", type=Path, default=Path("tmp/models/best_seg_dick.onnx"))
    parser.add_argument("-om", "--obb-model", type=Path, help="OBB checkpoint; -bc defaults to the reviewed nano model")
    parser.add_argument(
        "-bc",
        "--obb-context",
        action="store_true",
        help="Reviewed OBB -> contextual SEG only; replaces penisV2/NudeNet; crop axes stay aligned with the image",
    )
    parser.add_argument(
        "-so",
        "--superimpose",
        action="store_true",
        help="With -bc: save one overlay per image with contextual SEG masks and OBB outlines together",
    )
    parser.add_argument(
        "-mm",
        "--mismatches",
        action="store_true",
        help="With -bc: flag same-class SEG coverage below -mc, without altering masks",
    )
    parser.add_argument(
        "-mc",
        "--min-coverage",
        type=float,
        default=0.4,
        help="Minimum matched SEG coverage of visible OBB, fraction 0..1 (default 0.4)",
    )
    parser.add_argument(
        "-dm",
        "--detect-model",
        type=Path,
        help="Optional extra YOLOv8 detector, e.g. tmp/models/penisV2.onnx; adds overlay and CPU timing",
    )
    parser.add_argument("-ds", "--detect-confidence", type=float, help="Optional detector threshold; defaults to -s")
    parser.add_argument(
        "-vc",
        "--v2-context",
        action="store_true",
        help="Fast penisV2 -> contextual SEG only; defaults -dm to tmp/models/penisV2.onnx; skips NudeNet, OBB and full-image SEG",
    )
    parser.add_argument(
        "-s", "--confidence", type=float, default=0.25, help="Fallback threshold for both custom models"
    )
    parser.add_argument("-ss", "--seg-confidence", type=float, help="Segmentation threshold; overrides -s")
    parser.add_argument(
        "-mt",
        "--mask-threshold",
        type=float,
        default=0,
        help="SEG mask-logit threshold, -8..8; lower generally expands masks (default 0)",
    )
    parser.add_argument(
        "-uc",
        "--uncrop",
        action="store_true",
        help="Do not clip SEG masks to their predicted boxes; context crop still bounds them",
    )
    parser.add_argument(
        "-sw",
        "--mask-sweep",
        action="store_true",
        help="With -vc/-bc: six-panel -1/0/+1 mask-threshold x box-clip on/off comparison; reuses inference; -cp unnecessary",
    )
    parser.add_argument("-os", "--obb-confidence", type=float, help="OBB threshold; overrides -s")
    parser.add_argument(
        "-sc",
        "--seg-class-conf",
        nargs="+",
        action="extend",
        default=[],
        metavar="CLASS=VALUE",
        help="Per-class SEG overrides, e.g. dick=0.1 hand=0.5",
    )
    parser.add_argument(
        "-oc",
        "--obb-class-conf",
        nargs="+",
        action="extend",
        default=[],
        metavar="CLASS=VALUE",
        help="Per-class OBB overrides, e.g. dick-head=0.15 balls=0.3",
    )
    parser.add_argument(
        "-n", "--nms", type=float, default=0.5, help="Raw-head suppression IoU; not applied to end-to-end OBB outputs"
    )
    parser.add_argument("-m", "--max-det", type=int, default=50)
    parser.add_argument("-t", "--threads", type=int, default=2)
    parser.add_argument(
        "-nn",
        "--nudenet",
        action="store_true",
        help="Add segmentation inside NudeNet context crops; retain full-image SEG/OBB references",
    )
    parser.add_argument("-ns", "--nudenet-size", choices=("320n", "640m"), default="640m")
    parser.add_argument(
        "-co",
        "--context-only",
        action="store_true",
        help="Run NN-context SEG + full-image OBB only; implies -nn and skips baseline SEG inference",
    )
    parser.add_argument(
        "-nc",
        "--nn-confidence",
        type=float,
        default=0.25,
        help="NudeNet target threshold, independent of custom models",
    )
    parser.add_argument(
        "-k",
        "--context",
        type=float,
        default=2.5,
        help="Context width/height multiplier, 1..6; square mode uses the box long side",
    )
    parser.add_argument(
        "-ks",
        "--context-shape",
        choices=("box", "square"),
        default="box",
        help="box (default): preserve NN box proportions; square: previous long-side-based context",
    )
    parser.add_argument(
        "-d", "--no-download", action="store_true", help="Forbid downloading the optional NudeNet model"
    )
    parser.add_argument("-a", "--alpha", type=float, default=0.35)
    parser.add_argument(
        "-c", "--classes", nargs="+", choices=tuple(PALETTE), help="Optional class-name filter; default all"
    )
    parser.add_argument(
        "-r", "--size", type=int, default=2560, help="Saved image maximum long side, 0 keeps native resolution"
    )
    parser.add_argument(
        "-cp", "--compare", action="store_true", help="Side-by-side comparison; -nn adds a middle context-SEG panel"
    )
    parser.add_argument("-f", "--images-only", action="store_true")
    parser.add_argument(
        "-w", "--overwrite", action="store_true", help="Replace only recognized custom_parts_poc flat outputs"
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.min_coverage) or not 0 <= args.min_coverage <= 1:
        parser.error("min-coverage must be finite in [0,1]")
    if args.mismatches and (not args.obb_context or args.mask_sweep):
        parser.error("-mm requires -bc and is not available with -sw")
    if not math.isfinite(args.mask_threshold) or not -8 <= args.mask_threshold <= 8:
        parser.error("mask-threshold must be finite in [-8,8]")
    if args.obb_context and (args.v2_context or args.nudenet or args.detect_model is not None):
        parser.error("-bc replaces penisV2/NudeNet; omit -vc, -nn and -dm")
    if args.superimpose and (not args.obb_context or args.mask_sweep or args.compare):
        parser.error("-so requires -bc and replaces -cp/-sw output modes")
    if args.mask_sweep and not (args.v2_context or args.obb_context):
        parser.error("-sw requires -vc/-bc so unclipped masks stay bounded by context crops")
    if args.mask_sweep and (args.mask_threshold != 0 or args.uncrop):
        parser.error("-sw uses fixed thresholds/clipping; omit -mt and -uc")
    if args.obb_context:
        args.context_only = True
    elif args.v2_context:
        if args.nudenet:
            parser.error("-vc replaces NudeNet; omit -nn")
        args.context_only = True
        if args.detect_model is None:
            args.detect_model = Path("tmp/models/penisV2.onnx")
    elif args.context_only:
        args.nudenet = True
    if args.obb_model is None:
        args.obb_model = Path(
            "tmp/models/yolo26n_obb_duck_reviewed.onnx"
            if args.obb_context
            else "tmp/models/best_obb_dick_lastmile.onnx"
        )
    args.seg_confidence = args.confidence if args.seg_confidence is None else args.seg_confidence
    args.obb_confidence = args.confidence if args.obb_confidence is None else args.obb_confidence
    args.detect_confidence = args.confidence if args.detect_confidence is None else args.detect_confidence
    if any(
        not 0 < value <= 1
        for value in (args.seg_confidence, args.obb_confidence, args.nn_confidence, args.detect_confidence)
    ):
        parser.error("SEG, OBB, detector and NudeNet thresholds must be in (0,1]")
    args.seg_class_conf = class_confidence(args.seg_class_conf, parser)
    args.obb_class_conf = class_confidence(args.obb_class_conf, parser)
    if not math.isfinite(args.context) or not 1 <= args.context <= 6:
        parser.error("context must be finite in [1,6]")
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
    pattern = re.compile(
        r"cpp_\d{4,}_.+_(?:seg|seg_nn|seg_v2|seg_obb|overlay|obb|detect|compare|report)\.(?:jpg|json)$"
    )
    if previous and (
        not args.overwrite
        or any(p.is_symlink() or p.is_junction() or not p.is_file() or not pattern.fullmatch(p.name) for p in previous)
    ):
        parser.error("Output is not empty; -w replaces recognized custom_parts_poc outputs only")
    seg, seg_names, seg_model = load_model(args.seg_model, "segment", args.threads)
    obb, obb_names, obb_model = None, {}, None
    if not args.v2_context:
        obb, obb_names, obb_model = load_model(args.obb_model, "obb", args.threads)
    detector, detector_names, detector_model = None, None, None
    if args.detect_model is not None:
        detector, detector_names, detector_model = load_model(args.detect_model, "detect", args.threads)
    if args.v2_context and "penis" not in detector_names.values():
        parser.error("-vc requires a detector with class 'penis'")
    for label, overrides, names in (("SEG", args.seg_class_conf, seg_names), ("OBB", args.obb_class_conf, obb_names)):
        unknown = set(overrides) - set(names.values()) if names else set()
        if unknown:
            parser.error(f"{label} model has no classes: {', '.join(sorted(unknown))}")
    nn_session, nn_model = None, None
    if args.nudenet:
        from dtflow.services import person_selection as selection
        from edge_nudenet_poc import NUDENET_SPECS, nudenet_640_session
        from pascal_nudenet_poc import verified_checkpoint

        nn_path = Path("tmp/models") / f"nudenet-{args.nudenet_size}.onnx"
        spec = NUDENET_SPECS[args.nudenet_size]
        verified_checkpoint(nn_path, spec, not args.no_download)
        nn_session = selection._verified_session(
            selection.describe_model(nn_path),
            nudenet_640_session if args.nudenet_size == "640m" else selection._faces_session,
        )
        nn_model = {"filename": nn_path.name, "digest_algorithm": spec[2], "digest": spec[3]}
    cv.setNumThreads(args.threads)
    output.mkdir(parents=True, exist_ok=True)
    for entry in previous:
        if entry.is_symlink() or entry.is_junction() or not entry.is_file() or entry.resolve().parent != output:
            raise RuntimeError(f"Output changed before cleanup: {entry.name}")
        entry.unlink()
    print(
        f"ONNX Runtime CPU: {len(files)} images; seg {seg_model['input_shape'][2:]}"
        + (f"; OBB {obb_model['input_shape'][2:]}" if obb_model else "; fast V2-context mode, OBB/NudeNet skipped"),
        flush=True,
    )
    if obb_model and obb_model["end2end"]:
        print("OBB: end-to-end head; confidence/class filtering only, -n inactive for OBB", flush=True)
    if args.mask_sweep:
        print(
            "Mask sweep: columns -1 / 0 / +1; rows SEG-box clip ON / OFF; one SEG inference per context crop",
            flush=True,
        )
    failures = 0
    for index, path in enumerate(files, 1):
        prefix = f"cpp_{index:04d}_{path.stem[:70]}"
        try:
            started = time.perf_counter()
            source = load_rgb(path)
            seg_report = None
            panels = []
            if not args.context_only:
                seg_overlay, seg_report = predict(seg, seg_names, source, "segment", args)
                panels.append(
                    ("seg", frame(seg_overlay, f"SEG masks / {seg_report['inference_seconds']:.2f}s", seg_names))
                )
            obb_report, obb_panel = None, None
            if obb is not None:
                obb_overlay, obb_report = predict(
                    obb, obb_names, source, "obb", args, filter_classes=not args.obb_context
                )
                obb_panel = frame(obb_overlay, f"OBB + long axis / {obb_report['inference_seconds']:.2f}s", obb_names)
            nn_report = None
            if nn_session is not None:
                nn_overlay, nn_report = context_segmentation(seg, seg_names, nn_session, source, args)
                title = f"SEG in NN context / {nn_report['inference_seconds']:.2f}s / {len(nn_report['regions'])} crops\nYellow: context crop / white: NudeNet box"
                panels.append(("seg_nn", frame(nn_overlay, title, seg_names)))
            if obb_panel is not None:
                panels.append(("obb", obb_panel))
            obb_context_report = None
            if args.obb_context:
                targets = []
                for detection in obb_report["detections"]:
                    if detection["class"] not in {"balls", "dick", "dick-head"}:
                        continue
                    polygon = np.asarray(detection["polygon_xy"], dtype=np.float64)
                    targets.append(
                        dict(detection, box_xyxy=[*polygon.min(axis=0).tolist(), *polygon.max(axis=0).tolist()])
                    )
                variant_overlays = {} if args.mask_sweep else None
                obb_masks = [] if args.superimpose or args.mismatches else None
                context_overlay, obb_context_report = segment_context_regions(
                    seg, seg_names, source, args, targets, variant_overlays=variant_overlays, mask_sink=obb_masks
                )
                obb_context_report["context_provider"] = "reviewed_obb"
                obb_context_report["crop_geometry"] = (
                    "Expanded axis-aligned polygon bounds; overlapping crops merged; no deskew"
                )
                coverage = None
                if args.mismatches:
                    coverage = measure_obb_coverage(obb_report["detections"], obb_masks, source.size, args.min_coverage)
                    obb_context_report["mismatches"] = coverage
                    context_overlay = draw_coverage_issues(context_overlay, obb_report["detections"], coverage)
                if args.superimpose:
                    # Render directly from masks, not by blending two opaque image overlays.
                    pixels = np.asarray(source).copy()
                    for item in obb_masks:
                        x, y = item["offset_xy"]
                        height, width = item["mask"].shape
                        paint_mask(
                            pixels[y : y + height, x : x + width], item["mask"], PALETTE[item["class"]], args.alpha
                        )
                    for detection in obb_report["detections"]:
                        polygon = np.asarray(detection["polygon_xy"])
                        color = PALETTE[detection["class"]]
                        points = np.rint(polygon * 256).astype(np.int32)
                        cv.polylines(pixels, [points], True, (0, 0, 0), 5, cv.LINE_AA, shift=8)
                        cv.polylines(pixels, [points], True, color, 2, cv.LINE_AA, shift=8)
                    overlay = Image.fromarray(pixels)
                    draw = ImageDraw.Draw(overlay)
                    for detection in obb_report["detections"]:
                        polygon = np.asarray(detection["polygon_xy"])
                        x, y = polygon.min(axis=0)
                        draw.text(
                            (max(0, x), max(0, y)),
                            f"OBB {detection['class']} {detection['score']:.2f}",
                            fill=PALETTE[detection["class"]],
                            stroke_width=2,
                            stroke_fill="black",
                        )
                    if coverage is not None:
                        overlay = draw_coverage_issues(overlay, obb_report["detections"], coverage)
                    panels = [
                        (
                            "overlay",
                            frame(
                                overlay,
                                "SEG fill + contours / OBB outlined boxes + scores"
                                + (
                                    f"\nRed: SEG coverage < {args.min_coverage:.0%}; {coverage['issues']} issues"
                                    if coverage is not None
                                    else ""
                                ),
                                seg_names,
                            ),
                        )
                    ]
                else:
                    title = f"SEG in OBB context / {obb_context_report['inference_seconds']:.2f}s / {len(obb_context_report['regions'])} crops\nYellow: context / white: OBB enclosing bounds (no deskew)"
                    panels.append(("seg_obb", frame(context_overlay, title, seg_names)))
                if args.mask_sweep:
                    panels = []
                    for number, (key, threshold, clip) in enumerate(MASK_VARIANTS, 1):
                        title = f"{number}: logit {threshold:+d} / SEG-box clip {'ON' if clip else 'OFF'}\nOBB context / same inference"
                        panels.append((key, frame(variant_overlays[key], title, seg_names)))
            detector_report = None
            v2_report = None
            if detector is not None:
                detector_overlay, detector_report = predict(
                    detector, detector_names, source, "detect", args, filter_classes=not args.v2_context
                )
                title = f"DETECT {args.detect_model.stem} / {detector_report['inference_seconds']:.2f}s"
                panels.append(("detect", frame(detector_overlay, title, detector_names)))
                if args.v2_context:
                    targets = [d for d in detector_report["detections"] if d["class"] == "penis"]
                    variant_overlays = {} if args.mask_sweep else None
                    v2_overlay, v2_report = segment_context_regions(
                        seg, seg_names, source, args, targets, variant_overlays=variant_overlays
                    )
                    v2_report["context_provider"] = "penisV2"
                    title = f"SEG in V2 context / {v2_report['inference_seconds']:.2f}s / {len(v2_report['regions'])} crops\nYellow: context crop / white: detector box"
                    panels.append(("seg_v2", frame(v2_overlay, title, seg_names)))
                    if args.mask_sweep:
                        panels = []
                        for number, (key, threshold, clip) in enumerate(MASK_VARIANTS, 1):
                            title = f"{number}: logit {threshold:+d} / SEG-box clip {'ON' if clip else 'OFF'}\nSame inference / yellow: context / white: V2 box"
                            if not v2_report["regions"]:
                                title += " / no target"
                            panels.append((key, frame(variant_overlays[key], title, seg_names)))
            if args.mask_sweep:
                panel_width, panel_height = panels[0][1].size
                combined = Image.new("RGB", (panel_width * 3, panel_height * 2), (22, 22, 26))
                for index, (_, panel) in enumerate(panels):
                    combined.paste(panel, ((index % 3) * panel_width, (index // 3) * panel_height))
                save_image(combined, output / f"{prefix}_compare.jpg", args.size)
            elif args.compare:
                combined = Image.new(
                    "RGB",
                    (sum(panel.width for _, panel in panels), max(panel.height for _, panel in panels)),
                    (22, 22, 26),
                )
                offset = 0
                for _, panel in panels:
                    combined.paste(panel, (offset, 0))
                    offset += panel.width
                save_image(combined, output / f"{prefix}_compare.jpg", args.size)
            else:
                for name, panel in panels:
                    save_image(panel, output / f"{prefix}_{name}.jpg", args.size)
            seconds = time.perf_counter() - started
            if not args.images_only:
                report = {
                    "source": path.name,
                    "source_size": source.size,
                    "settings": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                    "models": {"seg": seg_model, "obb": obb_model, "nudenet": nn_model, "detect": detector_model},
                    "seg": seg_report,
                    "obb": obb_report,
                    "seg_nn": nn_report,
                    "seg_v2": v2_report,
                    "seg_obb": obb_context_report,
                    "detect": detector_report,
                    "seconds": seconds,
                    "saved_panels": [name for name, _ in panels],
                    "notes": [
                        "Full-image RGB inference; aspect-preserving centered letterbox with fill 114",
                        "Mask-logit threshold and SEG predicted-box cropping are recorded in settings; sweep variants reuse restored logits and identical detections",
                        "Raw-head OBB NMS uses exact polygon IoU, not Ultralytics ProbIoU; end-to-end OBB skips NMS",
                        "OBB long axis is undirected, not an anatomical base-to-tip prediction",
                        "Optional NN context is target-centered, with shape recorded in settings; image-edge clipping and overlap merging can alter proportions; SEG masks are not clipped to the NN box",
                        "V2 context mode uses class penis as crop anchors; -c filters SEG classes, not the V2 locator; OBB/NudeNet are not loaded and there is no full-image SEG pass",
                        "OBB context uses balls/dick/dick-head polygon enclosing bounds, expanded and merged without deskew; -c filters SEG only; -so combines SEG masks and OBB outlines in one overlay",
                    ],
                }
                (output / f"{prefix}_report.json").write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
                )
            print(
                f"{path.name}: "
                + (
                    f"{len(seg_report['detections'])} full-image masks, "
                    if seg_report is not None
                    else "baseline SEG skipped, "
                )
                + (f"{len(obb_report['detections'])} rotated boxes" if obb_report is not None else "OBB skipped")
                + (f", {len(nn_report['detections'])} NN-context masks" if nn_report is not None else "")
                + (
                    f", {len(v2_report['detections'])} V2-context masks, SEG {v2_report['inference_seconds']:.3f}s"
                    if v2_report is not None
                    else ""
                )
                + (
                    f", {len(obb_context_report['detections'])} OBB-context masks, SEG {obb_context_report['inference_seconds']:.3f}s"
                    if obb_context_report is not None
                    else ""
                )
                + (
                    f", {obb_context_report['mismatches']['issues']} coverage issues"
                    if obb_context_report is not None and args.mismatches
                    else ""
                )
                + (
                    f", extra detector {detector_report['inference_seconds']:.3f}s"
                    if detector_report is not None
                    else ""
                )
                + f"; {seconds:.2f}s",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

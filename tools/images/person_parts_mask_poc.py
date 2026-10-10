"""CPU-only PoC: cut the YOLO26 person-segmentation mask into labeled parts using NudeNet detections.

Non-generative. The YOLO26 end2end decoder is reused from yolo_person_zone_mask_poc; the parts
detector is a NudeNet 320n export (Ultralytics YOLOv8 detect, raw [1,4+classes,N] output). Each part
is a detector box intersected with the person mask, so parts are rectangular cuts of the person
silhouette, not part segmentations.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import math
import platform
import re
import time
from pathlib import Path

from person_zone_mask_poc import load_image, output_path, thread_count
from yolo_person_zone_mask_poc import confidence, decode, letterbox, session_for

LOG = logging.getLogger("person_parts_mask_poc")
PARTS_SIDE = 320
MAX_PARTS = 100  # label ids 2..101 fit in uint8; 0 = outside person, 1 = person without a part box
MAX_BYTES = 64 * 1024 * 1024
PALETTE = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128), (220, 190, 255)]


def read_verified(path: Path, expected: str, what: str) -> tuple[bytes, str]:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        raise ValueError(f"--{what}-sha256 must be a 64-digit SHA-256")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{what} model exceeds 64 MiB")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected.lower():
        raise ValueError(f"{what} model SHA-256 mismatch")
    return data, digest


def io_summary(session) -> dict:
    def shape(item):
        return [dim if isinstance(dim, int) else "dynamic" for dim in item.shape]

    return {"inputs": [{"name": i.name, "shape": shape(i), "type": i.type} for i in session.get_inputs()],
            "outputs": [{"name": o.name, "shape": shape(o), "type": o.type} for o in session.get_outputs()]}


def parts_session(data: bytes, threads: int):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(data, sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise ValueError("CPU-only provider contract failed")
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or len(inputs[0].shape) != 4 or inputs[0].shape[1] != 3 or inputs[0].type != "tensor(float)":
        raise ValueError("Parts model requires one float32 NCHW RGB input")
    meta = session.get_modelmeta().custom_metadata_map
    if meta.get("task") != "detect":
        raise ValueError("Parts model must be an Ultralytics detect export (raw [1,4+classes,N] output)")
    names = ast.literal_eval(meta.get("names", "{}"))
    if not isinstance(names, dict) or not names or sorted(names) != list(range(len(names))):
        raise ValueError("Parts model needs contiguous class names in metadata")
    if (len(outputs) != 1 or outputs[0].type != "tensor(float)" or len(outputs[0].shape) != 3
            or outputs[0].shape[1] != 4 + len(names)):
        raise ValueError(f"Parts model requires one float32 output [1,{4 + len(names)},N]")
    return session, meta, {int(k): str(v) for k, v in names.items()}


def preprocess_parts(image):
    """NudeNet-style: pad bottom/right with black to a square, resize to 320, /255, RGB NCHW."""
    import numpy as np
    from PIL import Image

    width, height = image.size
    side = max(width, height)
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(image, (0, 0))
    small = canvas.resize((PARTS_SIDE, PARTS_SIDE), Image.Resampling.BILINEAR)
    tensor = np.ascontiguousarray(np.asarray(small, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0)
    return tensor, {"square_side": side, "scale": side / PARTS_SIDE, "padding": "bottom/right black to square"}


def nms(boxes, scores, iou_threshold: float) -> list[int]:
    """Greedy class-agnostic NMS; returns kept indices in descending score order."""
    import numpy as np

    order = np.argsort(-scores)
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    kept = []
    while order.size:
        i = order[0]
        kept.append(int(i))
        rest = order[1:]
        if not rest.size:
            break
        inter_w = np.clip(np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]), 0, None)
        inter_h = np.clip(np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]), 0, None)
        inter = inter_w * inter_h
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return kept


def decode_parts(output, geometry, size, threshold, iou, names):
    import numpy as np

    if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] != 4 + len(names):
        raise ValueError("Unexpected parts output shape")
    if not np.isfinite(output).all():
        raise ValueError("Non-finite parts output")
    rows = output[0].T  # [N, 4 + classes]; box is center-x, center-y, w, h in 320 px model space
    class_scores = rows[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(rows)), class_ids]
    keep = scores >= threshold
    candidates = int(keep.sum())
    rows, class_ids, scores = rows[keep], class_ids[keep], scores[keep]
    scale = geometry["scale"]
    width, height = size
    boxes = np.stack([(rows[:, 0] - rows[:, 2] / 2) * scale, (rows[:, 1] - rows[:, 3] / 2) * scale,
                      (rows[:, 0] + rows[:, 2] / 2) * scale, (rows[:, 1] + rows[:, 3] / 2) * scale], axis=1)
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    boxes, class_ids, scores = boxes[valid], class_ids[valid], scores[valid]
    kept = nms(boxes, scores, iou)
    dropped = max(0, len(kept) - MAX_PARTS)
    kept = kept[:MAX_PARTS]
    detections = [{"index": n, "class_id": int(class_ids[i]), "label": names[int(class_ids[i])],
                   "score": float(scores[i]), "box_xyxy": [float(v) for v in boxes[i]]}
                  for n, i in enumerate(kept)]
    return detections, {"candidates_above_confidence": candidates,
                        "detections_after_nms": len(kept) + dropped, "detections_dropped_at_cap": dropped}


def split_person_mask(person, detections):
    """Partition the person mask: smaller boxes win overlaps, ties go to the higher score.

    Returns (labels, int_boxes, box_person_pixels). labels: 0 outside person, 1 person without a part
    box, detection index + 2 otherwise.
    """
    import numpy as np

    height, width = person.shape
    labels = (person > 0).astype(np.uint8)
    boxes = [(max(0, math.floor(d["box_xyxy"][0])), max(0, math.floor(d["box_xyxy"][1])),
              min(width, math.ceil(d["box_xyxy"][2])), min(height, math.ceil(d["box_xyxy"][3])))
             for d in detections]
    inside = [int(np.count_nonzero(person[y0:y1, x0:x1])) for x0, y0, x1, y1 in boxes]
    areas = [(x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes]
    # Paint large boxes first so smaller ones overwrite; equal area: lower score first, higher score wins.
    for i in sorted(range(len(detections)), key=lambda k: (-areas[k], -k)):
        x0, y0, x1, y1 = boxes[i]
        view = labels[y0:y1, x0:x1]
        view[person[y0:y1, x0:x1] > 0] = i + 2
    return labels, boxes, inside


def expand_to_mask(core, boxes, detections):
    """Grow parts to the mask limits: each person pixel without a part box joins the nearest part.

    Distance is Euclidean from the pixel to the part's rectangle (0 inside). Ties go to the smaller box,
    then the higher score. Not anatomy-aware: limbs and hair take the label of the nearest box.
    """
    import numpy as np

    labels = core.copy()
    ys, xs = np.nonzero(core == 1)
    if not len(xs) or not boxes:
        return labels
    best = np.full(len(xs), np.iinfo(np.int64).max, dtype=np.int64)
    owner = np.zeros(len(xs), dtype=np.int64)
    areas = [(x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes]
    for i in sorted(range(len(detections)), key=lambda k: (areas[k], k)):
        x0, y0, x1, y1 = boxes[i]
        dx = np.maximum(np.maximum(x0 - xs, xs - (x1 - 1)), 0)  # box covers x0..x1-1
        dy = np.maximum(np.maximum(y0 - ys, ys - (y1 - 1)), 0)
        distance = dx * dx + dy * dy
        closer = distance < best  # strict: earlier (smaller/higher-score) box keeps ties
        best[closer] = distance[closer]
        owner[closer] = i
    labels[ys, xs] = (owner + 2).astype(np.uint8)
    return labels


def render_overlay(image, labels, core, detections, boxes):
    import numpy as np
    from PIL import Image, ImageDraw

    pixels = np.asarray(image, dtype=np.float32).copy()
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(detections))]
    for i, color in enumerate(colors):
        core_mask = core == i + 2
        expanded = (labels == i + 2) & ~core_mask  # lighter tint: grown to the mask limits
        if core_mask.any():
            pixels[core_mask] = pixels[core_mask] * 0.45 + np.array(color, dtype=np.float32) * 0.55
        if expanded.any():
            pixels[expanded] = pixels[expanded] * 0.75 + np.array(color, dtype=np.float32) * 0.25
    result = Image.fromarray(pixels.clip(0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(result)
    for i, (det, box) in enumerate(zip(detections, boxes)):
        draw.rectangle(box, outline=colors[i], width=2)
        draw.text((box[0] + 3, box[1] + 3), f"{det['index']}:{det['label']}", fill=colors[i])
    return result


def save_outputs(destination: Path, person, labels, core, detections, overlay, report, timings, began) -> None:
    import numpy as np
    from PIL import Image

    def save_png(path: Path, array) -> None:
        with path.open("xb") as handle:
            Image.fromarray(array).save(handle, format="PNG")

    # New directory only; exclusive files ensure even partial runs are never overwritten.
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "parts").mkdir()
    start = time.perf_counter()
    save_png(destination / "person_mask.png", person)
    save_png(destination / "parts_label_map.png", labels)
    save_png(destination / "parts_core_label_map.png", core)
    for det in detections:
        mask = labels == det["index"] + 2
        if mask.any():
            name = f"{det['index']:02d}_{re.sub(r'[^A-Za-z0-9_]', '_', det['label'])}.png"
            save_png(destination / "parts" / name, mask.astype(np.uint8) * 255)
            det["mask_file"] = f"parts/{name}"
        else:
            det["mask_file"] = None
    with (destination / "overlay.jpg").open("xb") as handle:
        overlay.save(handle, format="JPEG", quality=90)
    timings["image_output_write"] = (time.perf_counter() - start) * 1000
    timings["total_before_report_write"] = (time.perf_counter() - began) * 1000
    with (destination / "report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")


def run(args):
    import numpy as np
    import onnxruntime as ort

    began = time.perf_counter()
    timings = {}
    out = output_path(args.out)
    LOG.info("START YOLO26 + parts CPU threads=%d yolo_conf=%.3f parts_conf=%.3f iou=%.2f",
             args.threads, args.confidence, args.parts_confidence, args.parts_iou)
    start = time.perf_counter()
    yolo_data, yolo_digest = read_verified(args.yolo_model, args.yolo_sha256, "yolo")
    parts_data, parts_digest = read_verified(args.parts_model, args.parts_sha256, "parts")
    timings["models_read_checksum"] = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    image, raw_size = load_image(args.source)
    timings["image_decode"] = (time.perf_counter() - start) * 1000

    # Stage 1: person mask (YOLO26 end2end), then free the session before loading the parts model.
    start = time.perf_counter()
    yolo, yolo_meta, yolo_names = session_for(yolo_data, args.threads)
    del yolo_data
    timings["yolo_session_load"] = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    tensor, yolo_geometry = letterbox(image)
    timings["yolo_preprocessing"] = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    detections_raw, prototypes = yolo.run(None, {yolo.get_inputs()[0].name: tensor})
    timings["yolo_inference_cold_single_run"] = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    person, yolo_diag = decode(detections_raw, prototypes, yolo_geometry, image.size, args.confidence, yolo_names)
    timings["yolo_decode"] = (time.perf_counter() - start) * 1000
    yolo_io, yolo_providers = io_summary(yolo), yolo.get_providers()
    del detections_raw, prototypes, tensor, yolo
    LOG.info("PERSON pixels=%d instances=%d", np.count_nonzero(person), len(yolo_diag["person_instances"]))

    # Stage 2: part boxes (NudeNet-style detector).
    start = time.perf_counter()
    parts, parts_meta, parts_names = parts_session(parts_data, args.threads)
    del parts_data
    timings["parts_session_load"] = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    parts_tensor, parts_geometry = preprocess_parts(image)
    timings["parts_preprocessing"] = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    raw = parts.run([parts.get_outputs()[0].name], {parts.get_inputs()[0].name: parts_tensor})[0]
    timings["parts_inference_cold_single_run"] = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    detections, parts_diag = decode_parts(raw, parts_geometry, image.size, args.parts_confidence,
                                          args.parts_iou, parts_names)
    timings["parts_decode"] = (time.perf_counter() - start) * 1000
    parts_io, parts_providers = io_summary(parts), parts.get_providers()
    del raw, parts_tensor, parts

    # Stage 3: cut.
    start = time.perf_counter()
    core, boxes, inside = split_person_mask(person, detections)
    labels = expand_to_mask(core, boxes, detections) if args.expand == "mask" else core
    core_counts = np.bincount(core.ravel(), minlength=MAX_PARTS + 2)
    counts = np.bincount(labels.ravel(), minlength=MAX_PARTS + 2)
    label_pixels: dict[str, int] = {}
    for det, box, box_person in zip(detections, boxes, inside):
        k = det["index"] + 2
        det.update(box_xyxy_int=list(box), box_person_pixels=box_person, core_pixels=int(core_counts[k]),
                   part_pixels=int(counts[k]), expanded_pixels=int(counts[k] - core_counts[k]))
        label_pixels[det["label"]] = label_pixels.get(det["label"], 0) + det["part_pixels"]
    overlay = render_overlay(image, labels, core, detections, boxes)
    timings["split_and_overlay"] = (time.perf_counter() - start) * 1000

    person_px = int(np.count_nonzero(person))
    warnings = []
    if not person_px:
        warnings.append("Empty person mask; nothing to cut into parts")
    if not detections:
        warnings.append("No part detections above confidence")
    elif person_px and not any(d["part_pixels"] for d in detections):
        warnings.append("Part boxes found but none overlap the person mask")
    outside = sum(1 for d in detections if d["box_person_pixels"] == 0)
    if outside and person_px:
        warnings.append(f"{outside} detection(s) do not overlap the person mask")
    if parts_diag["detections_dropped_at_cap"]:
        warnings.append(f"Parts capped at {MAX_PARTS} highest-score detections")
    if yolo_diag["person_instances_dropped_at_cap"]:
        warnings.append("Person masks capped at 20 highest-score instances")
    for warning in warnings:
        LOG.warning(warning)
    LOG.info("PARTS %s", [(d["index"], d["label"], round(d["score"], 3), d["part_pixels"]) for d in detections])

    report = {
        "schema_version": 1, "algorithm": "yolo26-person-seg-nudenet-parts-v1",
        "models": {
            "person_segmentation": {"path": str(args.yolo_model.resolve()), "sha256": yolo_digest,
                                    "metadata": yolo_meta, "io": yolo_io},
            "parts_detector": {"path": str(args.parts_model.resolve()), "sha256": parts_digest,
                               "metadata": parts_meta, "io": parts_io, "class_names": parts_names},
        },
        "source": {"path": str(args.source.resolve()), "raw_size_wh": list(raw_size),
                   "oriented_size_wh": list(image.size)},
        "runtime": {"providers": {"person_segmentation": yolo_providers, "parts_detector": parts_providers},
                    "onnxruntime": ort.__version__, "numpy": np.__version__, "python": platform.python_version(),
                    "platform": platform.platform(), "processor": platform.processor(),
                    "intra_op_threads": args.threads, "inter_op_threads": 1, "sequential": True,
                    "spinning": False, "numpy_blas_threads": 1},
        "preprocessing": {
            "person_segmentation": {"resize": "Pillow bilinear letterbox 640 square, pad114",
                                    "scale": "/255, no mean/std", **yolo_geometry},
            "parts_detector": {"resize": "Pillow bilinear after black bottom/right pad to square, 320",
                               "normalization": "/255, RGB, no mean/std", **parts_geometry},
        },
        "decoding": {
            "person_segmentation": {"person_class": 0, "confidence": args.confidence,
                                    "mask_threshold": "logits >0"},
            "parts_detector": {"layout": "cx,cy,w,h + per-class scores; best class per anchor",
                               "confidence": args.parts_confidence, "nms": "greedy class-agnostic",
                               "nms_iou": args.parts_iou, "max_parts": MAX_PARTS},
            "split": "person mask partitioned by part boxes; smaller box wins overlaps, ties to higher score",
            "expand": args.expand,
            "expand_rule": "mask: person pixels without a box join the nearest part (Euclidean distance to its "
                           "rectangle; ties to smaller box, then higher score); none: core cuts only",
        },
        "labels": {"outside_person": 0, "person_without_part_box": 1, "part": "detection index + 2"},
        "masks": {"person_pixels": person_px, "unassigned_person_pixels": int(counts[1]),
                  "assigned_fraction_of_person": (person_px - int(counts[1])) / person_px if person_px else 0.0,
                  "core_unassigned_person_pixels": int(core_counts[1]),
                  "core_assigned_fraction_of_person":
                      (person_px - int(core_counts[1])) / person_px if person_px else 0.0,
                  "label_pixels": dict(sorted(label_pixels.items(), key=lambda kv: -kv[1])),
                  "polarity": "255 selected; 0 unselected"},
        "diagnostics": {"person_segmentation": yolo_diag, "parts_detector": parts_diag},
        "parts": detections, "warnings": warnings, "timings_ms": timings,
        "limitations": [
            "Parts are detector boxes intersected with the person mask: rectangular cuts, not part segmentation",
            "Expansion is nearest-box, not anatomy-aware: limbs and hair take the nearest part's label, "
            "and with several people it can cross instances",
            "Detections are model output, not ground truth; expect false positives and misses",
            "Class-agnostic NMS can suppress overlapping parts (for example covered/exposed variants)",
            "Person mask is a union of up to 20 instances; parts are not attributed to instances",
            "Pillow resize and black-padding preprocessing not verified identical to the NudeNet reference",
            "Both models are AGPL-3.0 Ultralytics exports; check licensing before shipping in an app",
            "One cold run per model, not a benchmark",
            "Report paths and overlay may be sensitive",
        ],
    }
    save_outputs(out, person, labels, core, detections, overlay, report, timings, began)
    LOG.info("DONE %s timings_ms=%s", out, timings)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-s", "--source", type=Path, required=True, help="One local image (read only)")
    parser.add_argument("-m", "--yolo-model", type=Path, required=True, help="YOLO26 end2end seg ONNX")
    parser.add_argument("-c", "--yolo-sha256", required=True, help="Expected SHA-256 of the YOLO model")
    parser.add_argument("--parts-model", type=Path, required=True, help="NudeNet-style 320 detect ONNX")
    parser.add_argument("--parts-sha256", required=True, help="Expected SHA-256 of the parts model")
    parser.add_argument("-f", "--confidence", type=confidence, default=.25, help="YOLO person confidence")
    parser.add_argument("--parts-confidence", type=confidence, default=.25, help="Parts detector confidence")
    parser.add_argument("--parts-iou", type=confidence, default=.45, help="Parts NMS IoU threshold")
    parser.add_argument("--expand", choices=("mask", "none"), default="mask",
                        help="mask (default): grow parts to fill the person mask; none: box cuts only")
    parser.add_argument("-t", "--threads", type=thread_count, default=1)
    parser.add_argument("-o", "--out", type=Path, required=True, help="New directory below repository tmp/")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        destination = run(args)
    except Exception:
        LOG.exception("PoC failed; no retry/fallback; any partial outputs are preserved")
        if args.out.exists():
            LOG.error("Partial output left at %s; remove it before retrying", args.out)
        return 1
    print(f"Saved person/parts masks and report to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
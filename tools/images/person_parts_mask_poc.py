"""CPU-only PoC: cut the YOLO26 person-segmentation mask into labeled parts using NudeNet detections.

Non-generative. The YOLO26 end2end decoder is reused from yolo_person_zone_mask_poc; the parts
detector is a NudeNet 320n export (Ultralytics YOLOv8 detect, raw [1,4+classes,N] output). Each part
is a detector box intersected with the person mask, so parts are rectangular cuts of the person
silhouette, not part segmentations.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import logging
import math
import platform
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from person_zone_mask_poc import ROOT, load_image, output_path, thread_count
from yolo_person_zone_mask_poc import SIDE, confidence, letterbox, session_for

LOG = logging.getLogger("person_parts_mask_poc")
PARTS_SIDE = 320
MAX_PARTS = 100  # label ids 2..101 fit in uint8; 0 = outside person, 1 = person without a part box
MAX_BYTES = 256 * 1024 * 1024
PALETTE = [
    (230, 25, 75),
    (60, 180, 75),
    (255, 225, 25),
    (0, 130, 200),
    (245, 130, 48),
    (145, 30, 180),
    (70, 240, 240),
    (240, 50, 230),
    (210, 245, 60),
    (250, 190, 212),
    (0, 128, 128),
    (220, 190, 255),
]


EXPAND_RULES = {
    "rows": "grown boxes cut the mask, then each part extends left/right along its own rows through unassigned "
    "person pixels until the mask border or another part; a gap between two parts is split at its midpoint; "
    "pixels outside every part's row band stay unassigned",
    "nearest": "person pixels without a box join the nearest part (Euclidean distance to its rectangle; ties to "
    "smaller box, then higher score), optionally capped by expand_max_px",
    "geodesic": "each part floods outward through the person mask from its cut region, one pixel ring at a time "
    "(alternating 8- and 4-neighbour steps, roughly octagonal distance), until the mask border or another "
    "part; follows limbs and curves, never jumps gaps; simultaneous arrivals go to the smaller box, then "
    "higher score; pixels not connected to any part stay unassigned; optionally capped by expand_max_px "
    "rings",
    "none": "grown-box cuts only",
}


# Local copy of yolo_person_zone_mask_poc.decode plus box_margin, so this script runs against either
# version of that module (the parts script no longer depends on its decode signature).
def decode_person(detections, prototypes, geometry, size, threshold, names, box_margin=8.0):
    import numpy as np
    from PIL import Image

    if detections.shape != (1, 300, 38) or prototypes.shape != (1, 32, 160, 160):
        raise ValueError("Unexpected output tensor shapes")
    if not np.isfinite(detections).all() or not np.isfinite(prototypes).all():
        raise ValueError("Non-finite model outputs")
    rows = detections[0]
    accepted = rows[rows[:, 4] >= threshold]
    if np.any((accepted[:, 4] < 0) | (accepted[:, 4] > 1)):
        raise ValueError("Scores outside [0,1]")
    if np.any(accepted[:, 5] != np.round(accepted[:, 5])):
        raise ValueError("Expected end2end integer class IDs")
    classes, counts = np.unique(accepted[:, 5].astype(int), return_counts=True)
    histogram = [
        {"class_id": int(c), "name": names.get(int(c), "unknown"), "detections": int(n)}
        for c, n in zip(classes, counts)
    ]
    people = accepted[accepted[:, 5] == 0]
    people = people[np.argsort(-people[:, 4])]
    # Decode sequentially and cap to 20 instances to bound mask work/memory.
    dropped = max(0, len(people) - 20)
    people = people[:20]
    union = np.zeros((SIDE, SIDE), dtype=bool)
    yy, xx = np.ogrid[:SIDE, :SIDE]
    records = []
    left, top = geometry["padding_left_top"]
    gain_x = gain_y = geometry["gain"]
    width, height = size
    proto = prototypes[0].reshape(32, -1)
    for row in people:
        x0, y0, x1, y1 = np.clip(row[:4], 0, SIDE)
        if x1 <= x0 or y1 <= y0:
            continue
        logits = (row[6:] @ proto).reshape(160, 160)
        if not np.isfinite(logits).all():
            raise ValueError("Non-finite reconstructed mask")
        # Float logits resized first, threshold at zero (=sigmoid >.5), crop to detection box.
        full = np.asarray(Image.fromarray(logits).resize((SIDE, SIDE), Image.Resampling.BILINEAR)) > 0
        # The detection box is regressed and often a few px inside the person; box_margin (model-input px)
        # widens the crop so the mask can reach the person's real border (and the image border).
        mx0, my0 = max(0.0, x0 - box_margin), max(0.0, y0 - box_margin)
        mx1, my1 = min(float(SIDE), x1 + box_margin), min(float(SIDE), y1 + box_margin)
        full &= (xx >= mx0) & (xx < mx1) & (yy >= my0) & (yy < my1)
        union |= full
        box = [
            float(np.clip((x0 - left) / gain_x, 0, width)),
            float(np.clip((y0 - top) / gain_y, 0, height)),
            float(np.clip((x1 - left) / gain_x, 0, width)),
            float(np.clip((y1 - top) / gain_y, 0, height)),
        ]
        records.append(
            {
                "score": float(row[4]),
                "box_input_xyxy": [float(v) for v in row[:4]],
                "box_source_xyxy": box,
                "mask_pixels_input": int(full.sum()),
            }
        )
    rw, rh = geometry["resized_wh"]
    cropped = union[top : top + rh, left : left + rw].astype(np.uint8) * 255
    person = np.asarray(Image.fromarray(cropped).resize(size, Image.Resampling.NEAREST)).copy()
    person_rows = rows[rows[:, 5] == 0]
    top_scores = [float(v) for v in np.sort(person_rows[:, 4])[::-1][:3]]
    return person, {
        "class_histogram_above_confidence": histogram,
        "person_instances": records,
        "person_instances_dropped_at_cap": dropped,
        "max_person_score": top_scores[0] if top_scores else None,
        "top3_person_scores_any_confidence": top_scores,
        "prototype_shape": list(prototypes.shape),
        "mask_coefficients": int(rows.shape[1] - 6),
    }


def grow_factor(value: str) -> float:
    number = float(value)
    if not 0 <= number <= 5:
        raise argparse.ArgumentTypeError("Grow must be between 0 and 5")
    return number


def pixel_count(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Pixel distance must be >= 0")
    return number


def read_verified(path: Path, expected: str, what: str) -> tuple[bytes, str]:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        raise ValueError(f"--{what}-sha256 must be a 64-digit SHA-256")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{what} model exceeds {MAX_BYTES // (1024 * 1024)} MiB")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected.lower():
        raise ValueError(f"{what} model SHA-256 mismatch")
    return data, digest


def io_summary(session) -> dict:
    def shape(item):
        return [dim if isinstance(dim, int) else "dynamic" for dim in item.shape]

    return {
        "inputs": [{"name": i.name, "shape": shape(i), "type": i.type} for i in session.get_inputs()],
        "outputs": [{"name": o.name, "shape": shape(o), "type": o.type} for o in session.get_outputs()],
    }


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
    if (
        len(inputs) != 1
        or len(inputs[0].shape) != 4
        or inputs[0].shape[1] not in (3, "num_channels")
        and isinstance(inputs[0].shape[1], int)
        or inputs[0].type != "tensor(float)"
    ):
        raise ValueError("Parts model requires one float32 NCHW RGB input")
    meta = session.get_modelmeta().custom_metadata_map
    if meta.get("task") != "detect":
        raise ValueError("Parts model must be an Ultralytics detect export (raw [1,4+classes,N] output)")
    names = ast.literal_eval(meta.get("names", "{}"))
    if not isinstance(names, dict) or not names or sorted(names) != list(range(len(names))):
        raise ValueError("Parts model needs contiguous class names in metadata")
    if (
        len(outputs) != 1
        or outputs[0].type != "tensor(float)"
        or len(outputs[0].shape) != 3
        or outputs[0].shape[1] != 4 + len(names)
    ):
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


def decode_parts(output, geometry, size, threshold, iou, names, exclude_ids=()):
    import numpy as np

    if output.ndim != 3 or output.shape[0] != 1 or output.shape[1] != 4 + len(names):
        raise ValueError("Unexpected parts output shape")
    if not np.isfinite(output).all():
        raise ValueError("Non-finite parts output")
    rows = output[0].T  # [N, 4 + classes]; box is center-x, center-y, w, h in 320 px model space
    class_scores = rows[:, 4:].copy()
    if len(exclude_ids):
        # Excluded before argmax and NMS, so they can neither win an anchor nor suppress other parts.
        class_scores[:, list(exclude_ids)] = -1.0
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(rows)), class_ids]
    keep = scores >= threshold
    candidates = int(keep.sum())
    rows, class_ids, scores = rows[keep], class_ids[keep], scores[keep]
    scale = geometry["scale"]
    width, height = size
    boxes = np.stack(
        [
            (rows[:, 0] - rows[:, 2] / 2) * scale,
            (rows[:, 1] - rows[:, 3] / 2) * scale,
            (rows[:, 0] + rows[:, 2] / 2) * scale,
            (rows[:, 1] + rows[:, 3] / 2) * scale,
        ],
        axis=1,
    )
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
    valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
    boxes, class_ids, scores = boxes[valid], class_ids[valid], scores[valid]
    kept = nms(boxes, scores, iou)
    dropped = max(0, len(kept) - MAX_PARTS)
    kept = kept[:MAX_PARTS]
    detections = [
        {
            "index": n,
            "class_id": int(class_ids[i]),
            "label": names[int(class_ids[i])],
            "score": float(scores[i]),
            "box_xyxy": [float(v) for v in boxes[i]],
        }
        for n, i in enumerate(kept)
    ]
    return detections, {
        "candidates_above_confidence": candidates,
        "detections_after_nms": len(kept) + dropped,
        "detections_dropped_at_cap": dropped,
    }


POSE_KEYPOINTS = 17
# (label, from keypoint, to keypoint, kind). COCO order: 5/6 shoulders, 7/8 elbows, 9/10 wrists (subject's left/right).
POSE_LIMBS = (
    ("LEFT_UPPER_ARM", 5, 7, "segment"),
    ("LEFT_FOREARM", 7, 9, "segment"),
    ("LEFT_HAND", 7, 9, "extension"),
    ("RIGHT_UPPER_ARM", 6, 8, "segment"),
    ("RIGHT_FOREARM", 8, 10, "segment"),
    ("RIGHT_HAND", 8, 10, "extension"),
    # legs: 11/12 hips, 13/14 knees, 15/16 ankles (subject's left/right); the foot continues past the ankle
    ("LEFT_THIGH", 11, 13, "segment"),
    ("LEFT_SHIN", 13, 15, "segment"),
    ("LEFT_FOOT", 13, 15, "extension"),
    ("RIGHT_THIGH", 12, 14, "segment"),
    ("RIGHT_SHIN", 14, 16, "segment"),
    ("RIGHT_FOOT", 14, 16, "extension"),
)
LEG_LABELS = ("THIGH", "SHIN", "FOOT")
POSE_MAX_PEOPLE = 10


def pose_session(data: bytes, threads: int):
    """CPU session for an Ultralytics pose export: float32 [1,3,640,640] in, end2end [1,300,57] or raw [1,56,N] out."""
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
    if len(inputs) != 1 or inputs[0].shape != [1, 3, SIDE, SIDE] or inputs[0].type != "tensor(float)":
        raise ValueError(f"Pose model requires float32 input [1,3,{SIDE},{SIDE}]")
    meta = session.get_modelmeta().custom_metadata_map
    if meta.get("task") != "pose":
        raise ValueError("Pose model must be an Ultralytics pose export (task=pose)")
    values = 6 + POSE_KEYPOINTS * 3
    shape = outputs[0].shape if len(outputs) == 1 else None
    if (
        shape is None
        or outputs[0].type != "tensor(float)"
        or len(shape) != 3
        or shape[0] != 1
        or not ((shape[2] == values) or shape[1] == values - 1)
    ):
        raise ValueError(
            f"Pose model requires one float32 output [1,N,{values}] (end2end) or [1,{values - 1},N] (raw), "
            f"17 COCO keypoints"
        )
    return session, meta, "end2end" if shape[2] == values else "raw"


def decode_pose(output, layout, geometry, threshold, kpt_threshold, iou):
    """Decode pose output into people with source-pixel boxes and keypoints (x, y, confidence)."""
    import numpy as np

    if not np.isfinite(output).all():
        raise ValueError("Non-finite pose output")
    rows = output[0]
    if layout == "raw":
        rows = rows.T  # [N, 56]: cx, cy, w, h, score, 17 x (x, y, conf)
        boxes = np.stack(
            [
                rows[:, 0] - rows[:, 2] / 2,
                rows[:, 1] - rows[:, 3] / 2,
                rows[:, 0] + rows[:, 2] / 2,
                rows[:, 1] + rows[:, 3] / 2,
            ],
            axis=1,
        )
        scores, kpts = rows[:, 4], rows[:, 5 : 5 + POSE_KEYPOINTS * 3]
        keep = np.nonzero(scores >= threshold)[0]
        kept = [int(keep[i]) for i in nms(boxes[keep], scores[keep], iou)] if len(keep) else []
    else:  # end2end rows: x0, y0, x1, y1, score, class, 17 x (x, y, conf)
        boxes, scores, kpts = rows[:, :4], rows[:, 4], rows[:, 6 : 6 + POSE_KEYPOINTS * 3]
        keep = np.nonzero(scores >= threshold)[0]
        # the end2end head can still emit near-identical duplicates of one person, so run NMS here too
        kept = [int(keep[i]) for i in nms(boxes[keep], scores[keep], iou)] if len(keep) else []
        kept.sort(key=lambda i: -scores[i])
    left, top = geometry["padding_left_top"]
    gain = geometry["gain"]
    people = []
    for i in kept[:POSE_MAX_PEOPLE]:
        points = kpts[i].reshape(POSE_KEYPOINTS, 3)
        people.append(
            {
                "score": float(scores[i]),
                "box_source_xyxy": [
                    float((boxes[i][0] - left) / gain),
                    float((boxes[i][1] - top) / gain),
                    float((boxes[i][2] - left) / gain),
                    float((boxes[i][3] - top) / gain),
                ],
                "keypoints": [[float((x - left) / gain), float((y - top) / gain), float(c)] for x, y, c in points],
            }
        )
    return people, {
        "candidates_above_confidence": int((scores >= threshold).sum()),
        "people": len(people),
        "people_dropped_at_cap": max(0, len(kept) - POSE_MAX_PEOPLE),
        "layout": layout,
        "nms_iou": iou,
        "keypoint_confidence_threshold": kpt_threshold,
    }


def limb_zone(person, start, end, kind, width, extent=0.0, min_length=6.0):
    """Zone of the person mask along the vector start->end, bounded by the mask itself.

    The zone is the slab between the planes through start and end perpendicular to the vector, at most
    `width` x length from the axis, restricted to the person mask and to the connected piece the axis runs
    through. kind="extension" continues past `end` along the same vector (the hand beyond the wrist): up to
    `extent` x length, or until the mask ends when extent is 0.
    """
    import numpy as np

    height, w = person.shape
    ax, ay = start
    bx, by = end
    length = math.hypot(bx - ax, by - ay)
    zone = np.zeros(person.shape, dtype=bool)
    if length < min_length:
        return zone
    ux, uy = (bx - ax) / length, (by - ay) / length
    half = max(width * length, 8.0)
    t0, t1 = (0.0, 1.0) if kind == "segment" else (1.0, 1.0 + extent if extent else math.inf)
    far = math.hypot(height, w) / length  # reach the image border at most
    t1c = min(t1, 1.0 + far)
    corners = [
        (ax + ux * length * t - uy * half * side, ay + uy * length * t + ux * half * side)
        for t in (t0, t1c)
        for side in (-1, 1)
    ]
    x0 = max(0, int(math.floor(min(c[0] for c in corners))))
    x1 = min(w, int(math.ceil(max(c[0] for c in corners))) + 1)
    y0 = max(0, int(math.floor(min(c[1] for c in corners))))
    y1 = min(height, int(math.ceil(max(c[1] for c in corners))) + 1)
    if x1 <= x0 or y1 <= y0:
        return zone
    yy, xx = np.mgrid[y0:y1, x0:x1]
    rx, ry = xx - ax, yy - ay
    along = (rx * ux + ry * uy) / length
    across = np.abs(rx * uy - ry * ux)
    crop = (person[y0:y1, x0:x1] > 0) & (along >= t0) & (along <= t1) & (across <= half)
    if not crop.any():
        return zone
    seed_t = t0 + 0.2 * (min(t1c, t0 + 1.0) - t0)
    sx, sy = ax + ux * length * seed_t - x0, ay + uy * length * seed_t - y0
    crop = keep_component_from_center(crop, sx, sy, max(8, int(0.2 * length)), 12, False)
    zone[y0:y1, x0:x1] = crop
    return zone


def pose_zones(people, person, kpt_threshold, width, extent, leg_width=None, foot_extent=None, legs=True):
    """Build limb zones for every detected person: list of dicts with label, score, person index, zone.

    Arms use ``width``/``extent`` (hand); legs use ``leg_width``/``foot_extent`` (defaulting to the arm values)
    and are skipped when ``legs`` is False.
    """
    found = []
    for k, who in enumerate(people):
        points = who["keypoints"]
        for label, a, b, kind in POSE_LIMBS:
            is_leg = label.split("_", 1)[1] in LEG_LABELS
            if is_leg and not legs:
                continue
            (ax, ay, ac), (bx, by, bc) = points[a], points[b]
            if min(ac, bc) < kpt_threshold:
                continue
            limb_w = (leg_width if leg_width is not None else width) if is_leg else width
            limb_e = (foot_extent if foot_extent is not None else extent) if is_leg else extent
            zone = limb_zone(person, (ax, ay), (bx, by), kind, limb_w, limb_e)
            if zone.any():
                # axis used to arbitrate overlaps between zones: the segment, or the ray past the end for hands/feet
                axis = (
                    ((bx, by), (bx + (bx - ax) * 20, by + (by - ay) * 20))
                    if kind == "extension"
                    else ((ax, ay), (bx, by))
                )
                found.append(
                    {"label": label, "score": float(min(ac, bc)), "person_index": k, "zone": zone, "axis": axis}
                )
    return found


# Label set of the ATR-style SegFormer clothes models (mattmdjaga/segformer_b*_clothes); --clothes-config overrides it.
CLOTHES_LABELS = {
    0: "Background",
    1: "Hat",
    2: "Hair",
    3: "Sunglasses",
    4: "Upper-clothes",
    5: "Skirt",
    6: "Pants",
    7: "Dress",
    8: "Belt",
    9: "Left-shoe",
    10: "Right-shoe",
    11: "Face",
    12: "Left-leg",
    13: "Right-leg",
    14: "Left-arm",
    15: "Right-arm",
    16: "Bag",
    17: "Scarf",
}
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def clothes_side(value: str) -> int:
    number = int(value)
    if not 128 <= number <= 1024 or number % 32:
        raise argparse.ArgumentTypeError("Clothes input size must be a multiple of 32 in [128, 1024]")
    return number


def clothes_session(data: bytes, threads: int):
    """CPU session for a SegFormer-style clothes model: float32 NCHW 3-channel in, [1, classes, h/4, w/4] logits out."""
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
    if (
        len(inputs) != 1
        or len(inputs[0].shape) != 4
        or (isinstance(inputs[0].shape[1], int) and inputs[0].shape[1] != 3)
        or inputs[0].type != "tensor(float)"
    ):
        raise ValueError("Clothes model requires one float32 NCHW RGB input")
    if len(outputs) != 1 or outputs[0].type != "tensor(float)" or len(outputs[0].shape) != 4:
        raise ValueError("Clothes model requires one float32 [N, classes, h, w] logits output")
    channels = outputs[0].shape[1]
    return session, channels if isinstance(channels, int) else None


def load_clothes_labels(config: Path | None, channels: int | None) -> dict[int, str]:
    """Class names from a Hugging Face config.json (id2label) or the built-in ATR label set."""
    if config is None:
        labels = dict(CLOTHES_LABELS)
    else:
        if config.stat().st_size > 1024 * 1024:
            raise ValueError("Clothes config is unexpectedly large")
        raw = json.loads(config.read_text(encoding="utf-8")).get("id2label")
        if not isinstance(raw, dict) or not raw:
            raise ValueError("Clothes config has no id2label")
        labels = {int(k): str(v) for k, v in raw.items()}
    if sorted(labels) != list(range(len(labels))):
        raise ValueError("Clothes labels must be contiguous from 0")
    if channels is not None and channels != len(labels):
        raise ValueError(
            f"Clothes model has {channels} classes but {len(labels)} labels were given; pass --clothes-config"
        )
    return labels


def preprocess_clothes(image, person, size: int, margin: float = 0.08):
    """Crop to the person's bounding box (+margin), long side -> size, sides to multiples of 32, ImageNet norm."""
    import numpy as np
    from PIL import Image

    width, height = image.size
    ys, xs = np.nonzero(person)
    if len(xs):
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        mx, my = int((x1 - x0) * margin), int((y1 - y0) * margin)
        x0, y0, x1, y1 = max(0, x0 - mx), max(0, y0 - my), min(width, x1 + mx), min(height, y1 + my)
    else:
        x0, y0, x1, y1 = 0, 0, width, height
    crop = image.crop((x0, y0, x1, y1))
    scale = size / max(crop.size)
    target = (max(32, round(crop.width * scale / 32) * 32), max(32, round(crop.height * scale / 32) * 32))
    pixels = np.asarray(crop.resize(target, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    pixels = (pixels - np.array(IMAGENET_MEAN, dtype=np.float32)) / np.array(IMAGENET_STD, dtype=np.float32)
    tensor = np.ascontiguousarray(pixels.transpose(2, 0, 1)[None], dtype=np.float32)
    return tensor, {
        "crop_xyxy": [x0, y0, x1, y1],
        "resized_wh": list(target),
        "long_side": size,
        "normalization": "RGB /255, ImageNet mean/std",
        "margin": margin,
    }


def decode_clothes(logits, geometry, person, labels):
    """Upsample logits to the crop, argmax, paste into a full-size map restricted to the person mask.

    Map values: 0 outside the person, class id + 1 inside (so 1 = person pixel classed Background).
    """
    import numpy as np
    from PIL import Image

    if not np.isfinite(logits).all():
        raise ValueError("Non-finite clothes logits")
    x0, y0, x1, y1 = geometry["crop_xyxy"]
    width, height = x1 - x0, y1 - y0
    scores = logits[0]
    if scores.shape[0] != len(labels):
        raise ValueError("Clothes logits channel count does not match the labels")
    best = np.full((height, width), -np.inf, dtype=np.float32)
    argmax = np.zeros((height, width), dtype=np.uint8)
    for k in range(scores.shape[0]):  # one class at a time keeps memory at two crop-sized float arrays
        up = np.asarray(
            Image.fromarray(scores[k].astype(np.float32)).resize((width, height), Image.Resampling.BILINEAR)
        )
        better = up > best
        best[better] = up[better]
        argmax[better] = k
    mapping = np.zeros(person.shape, dtype=np.uint8)
    window = mapping[y0:y1, x0:x1]
    inside = person[y0:y1, x0:x1] > 0
    window[inside] = argmax[inside] + 1
    counts = np.bincount(mapping.ravel(), minlength=len(labels) + 1)
    return mapping, {
        "label_pixels": {labels[k]: int(counts[k + 1]) for k in range(1, len(labels)) if counts[k + 1]},
        "background_inside_person_pixels": int(counts[1]),
    }


def render_clothes_overlay(image, mapping, labels):
    import numpy as np
    from PIL import Image

    pixels = np.asarray(image, dtype=np.float32).copy()
    for k in range(1, len(labels)):
        mask = mapping == k + 1
        if mask.any():
            color = np.array(PALETTE[k % len(PALETTE)], dtype=np.float32)
            pixels[mask] = pixels[mask] * 0.5 + color * 0.5
    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8))


CLOTHING_NAMES = frozenset({"upper-clothes", "skirt", "pants", "dress", "belt", "scarf"})
SKIN_NAMES = frozenset({"face", "left-leg", "right-leg", "left-arm", "right-arm", "skin"})
SKIN_LABEL = "Skin"


def cap_face(mapping, labels, detections, below: float):
    """Relabel 'Face' pixels far from every detected face box as 'Skin' (the model paints neck and chest as Face).

    A face may extend ``below`` x box height under the box, 1 x box height above it and half a box width to each side.
    Returns (mapping, labels incl. the Skin label, info). Without a face class or detection nothing changes.
    """
    import numpy as np

    face_ids = [k for k, v in labels.items() if v.lower() == "face"]
    faces = [d for d in detections if d.get("source") == "parts" and d["label"].startswith("FACE_")]
    info: dict = {"applied": False, "below_fraction_of_face_height": below}
    if not face_ids or not faces:
        info["reason"] = "no Face class" if not face_ids else "no face detection"
        return mapping, labels, info
    allowed = np.zeros(mapping.shape, bool)
    for d in faces:
        x0, y0, x1, y1 = d["box_xyxy"]
        w, h = x1 - x0, y1 - y0
        left, right = max(0, int(x0 - 0.5 * w)), min(mapping.shape[1], int(x1 + 0.5 * w) + 1)
        top, bottom = max(0, int(y0 - h)), min(mapping.shape[0], int(y1 + below * h) + 1)
        allowed[top:bottom, left:right] = True
    labels = dict(labels)
    skin_id = next((k for k, v in labels.items() if v == SKIN_LABEL), max(labels) + 1)
    labels[skin_id] = SKIN_LABEL
    moved = (mapping == face_ids[0] + 1) & ~allowed
    mapping = mapping.copy()
    mapping[moved] = skin_id + 1
    info.update({"applied": True, "relabeled_face_to_skin_pixels": int(moved.sum()), "faces_used": len(faces)})
    return mapping, labels, info


def clothes_counts(mapping, labels) -> dict[str, int]:
    import numpy as np

    counts = np.bincount(mapping.ravel(), minlength=max(labels) + 2)
    return {labels[k]: int(counts[k + 1]) for k in sorted(labels) if k and counts[k + 1]}


@dataclass(frozen=True)
class CutOptions:
    """How each grown part box is turned into a region of the person mask.

    Ported from the p0rnClassColab standalone mask tool (shape limiter, center component, smoothing).
    """

    grow: float = 0.0
    shape: str = "rectangle"  # rectangle | rounded | ellipse
    roundness: float = 0.5
    component: str = "none"  # none | center
    center_radius: int = 8
    min_component_area: int = 12
    fallback_nearest: bool = False
    close_kernel: int = 0
    open_kernel: int = 0
    blur_sigma: float = 0.0
    overlap: str = "smaller"  # smaller | center: who wins a pixel claimed by several part boxes


def label_components(mask):
    """8-connected component labels (1..n) of a boolean mask using row runs and union-find."""
    import numpy as np

    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    parent = [0]

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    previous = []  # (start, end, id) runs of the previous row
    for y in range(height):
        padded = np.concatenate(([0], mask[y].astype(np.int8), [0]))
        edges = np.diff(padded)
        current = []
        for a, b in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]):
            ids = [rid for (pa, pb, rid) in previous if pa < b + 1 and pb > a - 1]  # touch incl. diagonals
            if ids:
                root = find(ids[0])
                for other in ids[1:]:
                    parent[find(other)] = root
                rid = root
            else:
                parent.append(len(parent))
                rid = len(parent) - 1
            labels[y, a:b] = rid
            current.append((int(a), int(b), rid))
        previous = current
    roots = np.array([find(i) for i in range(len(parent))])
    unique = np.unique(roots[1:]) if len(roots) > 1 else roots[1:]
    remap = np.zeros(len(parent), dtype=np.int32)
    for n, root in enumerate(unique, start=1):
        remap[roots == root] = n
    remap[0] = 0
    return remap[labels], len(unique)


def keep_component_from_center(mask, cx, cy, radius=8, min_area=12, fallback_nearest=False):
    """Keep only the connected component under (cx, cy); else the one most present in a disk around it.

    With no seed found, return an empty mask unless fallback_nearest picks the component nearest to the
    center. Components smaller than min_area are dropped.
    """
    import numpy as np

    if not mask.any():
        return mask
    labels, count = label_components(mask)
    height, width = mask.shape
    cx, cy = min(max(int(cx), 0), width - 1), min(max(int(cy), 0), height - 1)
    chosen = int(labels[cy, cx])
    if not chosen:
        ys, xs = np.nonzero(mask)
        d2 = (xs - cx) ** 2 + (ys - cy) ** 2
        near = d2 <= radius * radius
        if near.any():
            chosen = int(np.bincount(labels[ys[near], xs[near]]).argmax())
        elif fallback_nearest:
            chosen = int(labels[ys[d2.argmin()], xs[d2.argmin()]])
    if not chosen:
        return np.zeros_like(mask)
    kept = labels == chosen
    return kept if int(kept.sum()) >= min_area else np.zeros_like(mask)


def shape_mask(height, width, shape, roundness=0.5):
    """Boolean mask of the limiter shape inscribed in a height x width box."""
    import numpy as np
    from PIL import Image, ImageDraw

    if shape == "rectangle" or height < 2 or width < 2:
        return np.ones((height, width), dtype=bool)
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    if shape == "ellipse":
        draw.ellipse((0, 0, width - 1, height - 1), fill=255)
    else:
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=roundness * min(width, height) / 2, fill=255)
    return np.asarray(image) > 0


def smooth_mask(mask, close_kernel=0, open_kernel=0, blur_sigma=0.0):
    """Close, open, then Gaussian-blur and re-threshold a boolean mask (Pillow, square kernels)."""
    import numpy as np
    from PIL import Image, ImageFilter

    if not mask.any() or not (close_kernel or open_kernel or blur_sigma):
        return mask
    pad = max(close_kernel, open_kernel, int(math.ceil(blur_sigma * 3)), 1)
    image = Image.fromarray(np.pad(mask, pad).astype(np.uint8) * 255)

    def odd(k):
        return k if k % 2 else k + 1

    if close_kernel > 1:
        image = image.filter(ImageFilter.MaxFilter(odd(close_kernel))).filter(ImageFilter.MinFilter(odd(close_kernel)))
    if open_kernel > 1:
        image = image.filter(ImageFilter.MinFilter(odd(open_kernel))).filter(ImageFilter.MaxFilter(odd(open_kernel)))
    if blur_sigma > 0:
        image = image.filter(ImageFilter.GaussianBlur(blur_sigma))
    return (np.asarray(image) >= 128)[pad:-pad, pad:-pad]


def fill_person_holes(person, max_area):
    """Fill enclosed background holes of at most max_area px (components not touching the image border)."""
    import numpy as np

    if not max_area:
        return person, 0
    background = person == 0
    if not background.any():
        return person, 0
    labels, count = label_components(background)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    border = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    fill = np.zeros(count + 1, dtype=bool)
    fill[1:] = sizes[1:] <= max_area
    fill[border] = False
    fill[0] = False
    holes = fill[labels]
    if not holes.any():
        return person, 0
    result = person.copy()
    result[holes] = 255
    return result, int(holes.sum())


def split_person_mask(person, detections, options=CutOptions(), zones=None, axes=None):
    """Partition the person mask: smaller regions win overlaps, ties go to the higher score.

    Each box is scaled by (1 + grow) around its center and clipped. Its region is the person mask inside
    that box, optionally limited to an inscribed shape, reduced to the component under the detection's
    own center, smoothed, and re-intersected with the person mask.

    Returns (labels, int_boxes, region_pixels). labels: 0 outside person, 1 person without a part,
    detection index + 2 otherwise. zones maps a detection index to a ready-made boolean region (pose limb
    zones): it is used as is, ignores grow/shape/component/smoothing, and is painted after every box part so
    limbs win overlaps.
    """
    import numpy as np

    zones = zones or {}
    height, width = person.shape
    labels = (person > 0).astype(np.uint8)
    boxes, regions = [], []
    for n, d in enumerate(detections):
        if d["index"] in zones:
            ys, xs = np.nonzero(zones[d["index"]])
            box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else (0, 0, 0, 0)
            boxes.append(box)
            regions.append(
                zones[d["index"]][box[1] : box[3], box[0] : box[2]] & (person[box[1] : box[3], box[0] : box[2]] > 0)
            )
            continue
        x0, y0, x1, y1 = d["box_xyxy"]
        dx, dy = (x1 - x0) * options.grow / 2, (y1 - y0) * options.grow / 2
        box = (
            max(0, math.floor(x0 - dx)),
            max(0, math.floor(y0 - dy)),
            min(width, math.ceil(x1 + dx)),
            min(height, math.ceil(y1 + dy)),
        )
        boxes.append(box)
        bx0, by0, bx1, by1 = box
        crop = person[by0:by1, bx0:bx1] > 0
        if crop.size and crop.any():
            crop = crop & shape_mask(by1 - by0, bx1 - bx0, options.shape, options.roundness)
            if options.component == "center":
                crop = keep_component_from_center(
                    crop,
                    (x0 + x1) / 2 - bx0,
                    (y0 + y1) / 2 - by0,
                    options.center_radius,
                    options.min_component_area,
                    options.fallback_nearest,
                )
            crop = smooth_mask(crop, options.close_kernel, options.open_kernel, options.blur_sigma)
            crop = crop & (person[by0:by1, bx0:bx1] > 0)
        regions.append(crop)
    inside = [int(np.count_nonzero(r)) for r in regions]
    areas = [(x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes]
    is_zone = [d["index"] in zones for d in detections]
    box_parts = [i for i in range(len(detections)) if not is_zone[i]]
    if options.overlap in ("center", "nearest"):
        # A pixel claimed by several boxes goes to the part whose detection center is closest: "center" measures in
        # units of that detection's own half-size (<1 inside its box, favors large boxes); "nearest" in plain
        # pixels (a Voronoi split between detection centers). Ties go to the higher score.
        best = np.full((height, width), np.inf, dtype=np.float32)
        for i in sorted(box_parts, key=lambda k: (-detections[k]["score"], k)):
            x0, y0, x1, y1 = boxes[i]
            if not regions[i].any():
                continue
            rx0, ry0, rx1, ry1 = detections[i]["box_xyxy"]
            cx, cy = (rx0 + rx1) / 2, (ry0 + ry1) / 2
            hw, hh = max((rx1 - rx0) / 2, 1.0), max((ry1 - ry0) / 2, 1.0)
            yy, xx = np.mgrid[y0:y1, x0:x1]
            if options.overlap == "nearest":
                hw = hh = 1.0
            distance = np.hypot((xx + 0.5 - cx) / hw, (yy + 0.5 - cy) / hh).astype(np.float32)
            window = best[y0:y1, x0:x1]
            take = regions[i] & (distance < window)
            window[take] = distance[take]
            labels[y0:y1, x0:x1][take] = i + 2
    else:
        # Paint large boxes first so smaller ones overwrite; equal area: lower score first, higher score wins.
        for i in sorted(box_parts, key=lambda k: (-areas[k], -k)):
            x0, y0, x1, y1 = boxes[i]
            labels[y0:y1, x0:x1][regions[i]] = i + 2
    # Limb zones last: they win over box parts. Where two limb zones overlap (legs side by side) the pixel goes to
    # the zone whose axis is nearer; without axes the later-painted zone wins.
    axes = axes or {}
    nearest_axis = np.full((height, width), np.inf, dtype=np.float32)
    for i in sorted((k for k in range(len(detections)) if is_zone[k]), key=lambda k: (-areas[k], -k)):
        x0, y0, x1, y1 = boxes[i]
        if i in axes:
            (ax, ay), (bx, by) = axes[i]
            yy, xx = np.mgrid[y0:y1, x0:x1]
            dx, dy = bx - ax, by - ay
            t = np.clip(((xx + 0.5 - ax) * dx + (yy + 0.5 - ay) * dy) / max(dx * dx + dy * dy, 1e-6), 0.0, 1.0)
            distance = np.hypot(xx + 0.5 - (ax + t * dx), yy + 0.5 - (ay + t * dy)).astype(np.float32)
        else:
            distance = np.zeros(regions[i].shape, dtype=np.float32)
        window = nearest_axis[y0:y1, x0:x1]
        take = regions[i] & (distance <= window)
        window[take] = distance[take]
        labels[y0:y1, x0:x1][take] = i + 2
    return labels, boxes, inside


def expand_to_mask(core, boxes, detections, max_distance=0.0):
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
    take = best <= max_distance * max_distance if max_distance else np.ones(len(xs), dtype=bool)
    labels[ys[take], xs[take]] = (owner[take] + 2).astype(np.uint8)
    return labels


def expand_geodesic(core, boxes, detections, max_distance=0.0, limb_max=None):
    """Flood each part outward through the person mask (geodesic distance), so labels follow the mask curves.

    Pixels only join a part reachable through connected person pixels; disconnected islands stay label 1.
    max_distance caps the number of rings (about pixels); 0 means unlimited. limb_max does the same for pose
    limb zones only (None = same as max_distance, 0 = limbs do not flood at all).
    """
    import numpy as np

    labels = core.copy()
    height, width = core.shape
    if not boxes or not (core >= 2).any():
        return labels
    areas = [(x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes]
    order = sorted(range(len(detections)), key=lambda k: (areas[k], k))
    rank = np.zeros(len(detections) + 2, dtype=np.int64)
    for position, i in enumerate(order):
        rank[i + 2] = position
    ys, xs = np.nonzero(core >= 2)
    frontier = (ys, xs)
    is_limb = np.zeros(len(detections) + 2, dtype=bool)
    for k, d in enumerate(detections):
        is_limb[k + 2] = d.get("source") == "pose"
    limb_cap = max_distance if limb_max is None else limb_max
    eight = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    four = ((-1, 0), (0, -1), (0, 1), (1, 0))
    ring = 0
    while len(frontier[0]) and (not max_distance or ring < max_distance):
        fy, fx = frontier
        if limb_max is not None and ring >= limb_cap:  # limb zones stop flooding after limb_cap rings
            keep = ~is_limb[labels[fy, fx]]
            fy, fx = fy[keep], fx[keep]
            if not len(fy):
                break
        cy, cx, cl = [], [], []
        for dy, dx in eight if ring % 2 == 0 else four:
            ny, nx = fy + dy, fx + dx
            ok = (ny >= 0) & (ny < height) & (nx >= 0) & (nx < width)
            ny, nx, src = ny[ok], nx[ok], labels[fy[ok], fx[ok]]
            free = labels[ny, nx] == 1
            cy.append(ny[free])
            cx.append(nx[free])
            cl.append(src[free])
        cy, cx, cl = np.concatenate(cy), np.concatenate(cx), np.concatenate(cl)
        if not len(cy):
            break
        key = (cy * width + cx) * len(rank) + rank[cl]
        order_idx = np.argsort(key, kind="stable")
        pixel = (cy * width + cx)[order_idx]
        first = np.ones(len(pixel), dtype=bool)
        first[1:] = pixel[1:] != pixel[:-1]
        pick = order_idx[first]
        labels[cy[pick], cx[pick]] = cl[pick]
        frontier = (cy[pick], cx[pick])
        ring += 1
    return labels


def expand_rows(core):
    """Extend each part horizontally along its rows until the mask border or another part.

    For every maximal run of unassigned person pixels in a row, the part touching the run's left end and
    the part touching its right end claim it; if both exist the run is split at its midpoint (left part
    takes the extra pixel). A run touching no part, or bounded by background on both sides, stays
    unassigned. Rows outside every part's (grown) box span are untouched, so nothing grows vertically.
    """
    import numpy as np

    labels = core.copy()
    width = core.shape[1]
    free = core == 1
    for y in np.nonzero(free.any(axis=1))[0]:
        row = core[y]
        padded = np.concatenate(([0], free[y].astype(np.int8), [0]))
        edges = np.diff(padded)
        for start, end in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]):  # end is exclusive
            left = row[start - 1] if start > 0 else 0
            right = row[end] if end < width else 0
            left, right = (left if left >= 2 else 0), (right if right >= 2 else 0)
            if left and right:
                middle = start + (end - start + 1) // 2
                labels[y, start:middle] = left
                labels[y, middle:end] = right
            elif left or right:
                labels[y, start:end] = left or right
    return labels


def render_overlay(image, labels, core, detections, boxes, pose_people=()):
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
        if det.get("source") != "pose":
            draw.rectangle(box, outline=colors[i], width=2)
        draw.text((box[0] + 3, box[1] + 3), f"{det['index']}:{det['label']}", fill=colors[i])
    for who in pose_people:  # keypoints and limb vectors, for checking the pose model
        points = who["keypoints"]
        for _, a, b, kind in POSE_LIMBS:
            if kind == "segment" and min(points[a][2], points[b][2]) > 0:
                draw.line((points[a][0], points[a][1], points[b][0], points[b][1]), fill=(255, 255, 255), width=1)
        for x, y, c in points[5:17]:
            if c > 0:
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), outline=(255, 255, 255), width=1)
    return result


OWNED_FILES = (
    "person_mask.png",
    "parts_label_map.png",
    "parts_core_label_map.png",
    "overlay.jpg",
    "report.json",
    "clothes_label_map.png",
    "clothes_overlay.jpg",
    "clothed_mask.png",
    "skin_mask.png",
    "parts_label_map_view.png",
    "parts_core_label_map_view.png",
    "clothes_label_map_view.png",
    "combined_overlay.png",
)


def resolve_output(path: Path, overwrite: bool) -> Path:
    """Validate the output directory: new by default; with overwrite, an existing real directory is reused."""
    if not overwrite:
        return output_path(path)
    scratch = ROOT / "tmp"
    resolved = path.resolve()
    if scratch.resolve() != scratch.absolute() or not resolved.is_relative_to(scratch) or resolved == scratch:
        raise ValueError("Output must be a directory below repository tmp/")
    if resolved != path.absolute() or (resolved.exists() and (resolved.is_symlink() or not resolved.is_dir())):
        raise ValueError("Output must be a real directory, not redirected")
    return resolved


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def folder_jobs(folder: Path) -> list[tuple[Path, Path]]:
    """List (image, output directory) pairs for the images directly inside folder.

    Outputs go to <folder>/analyze/<stem>/; if two images share a stem (a.jpg, a.png) the
    full file name is used instead so they cannot collide. Symlinks are ignored.
    """
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("--source-dir must be a real directory")
    root = folder / "analyze"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError(f"{root} must be a real directory")
    images = sorted(
        p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    stems = [p.stem.lower() for p in images]
    return [(p, root / (p.stem if stems.count(p.stem.lower()) == 1 else p.name)) for p in images]


def resolve_batch_output(path: Path, overwrite: bool) -> Path:
    """Validate a folder-mode output directory (already built by folder_jobs, next to its source)."""
    if path.is_symlink() or (path.exists() and (not overwrite or not path.is_dir())):
        raise ValueError("Output must not exist (or use --overwrite) and must not be redirected")
    return path.absolute()


def clear_owned_outputs(destination: Path) -> None:
    """Delete only files this script writes (known names, parts/*.png, labels/*.png) before an overwrite."""
    for name in OWNED_FILES:
        (destination / name).unlink(missing_ok=True)
    for folder in ("parts", "labels", "clothes", "parts_fused"):
        for stale in (destination / folder).glob("*.png"):
            if stale.is_file() and not stale.is_symlink():
                stale.unlink()


def part_names(detections) -> dict[int, str]:
    """Label-map id -> readable name: 1 is person without a part, detection index + 2 is a part."""
    names = {1: "PERSON (no part)"}
    for det in detections:
        names[det["index"] + 2] = f"{det['index']}:{det['label']}"
    return names


def label_palette(top: int):
    """uint8 palette [top + 1, 3]: row 0 black, other rows distinct deterministic colors."""
    import colorsys

    import numpy as np

    palette = np.zeros((max(top, 1) + 1, 3), np.uint8)
    for k in range(1, top + 1):
        r, g, b = colorsys.hsv_to_rgb((k * 0.61803398875) % 1.0, 0.65 if k % 2 else 0.9, 0.95)
        palette[k] = (int(r * 255), int(g * 255), int(b * 255))
    return palette


def draw_label_names(image, array, names: dict[int, str]) -> None:
    """Draw each id's name (outlined text) at a collision-avoiding point inside its largest region."""
    import numpy as np
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=max(13, min(array.shape) // 48))
    except TypeError:  # Pillow < 10.1 has a fixed-size default font
        font = ImageFont.load_default()
    height, width = array.shape
    placed: list[tuple[int, int, int, int]] = []
    sizes = {key: int((array == key).sum()) for key in names}
    for key in sorted((k for k in names if sizes[k]), key=lambda k: sizes[k]):  # small regions pick first
        mask = array == key
        components, count = label_components(mask)
        if count > 1:
            counts = np.bincount(components.ravel())[1:]
            mask = components == (int(counts.argmax()) + 1)
        ys, xs = np.nonzero(mask)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        crop = np.pad(mask[y0:y1, x0:x1], 1)
        depth = np.zeros(crop.shape, np.int32)  # inward distance via repeated 4-neighbour erosion
        current = crop
        while current.any():
            depth += current
            current = (
                current
                & np.roll(current, 1, 0)
                & np.roll(current, -1, 0)
                & np.roll(current, 1, 1)
                & np.roll(current, -1, 1)
            )
        dy, dx = np.nonzero(depth >= max(1, int(depth.max() * 0.6)))
        order = np.argsort(-depth[dy, dx], kind="stable")
        if len(order) > 400:  # thin out candidates evenly; keeps the deepest first
            order = order[np.linspace(0, len(order) - 1, 400).astype(int)]
        text = str(names[key])
        box = draw.textbbox((0, 0), text, font=font, anchor="mm", stroke_width=2)
        half_w, half_h = (box[2] - box[0]) // 2 + 2, (box[3] - box[1]) // 2 + 2
        best = None
        for index in order:
            cx, cy = int(dx[index] + x0 - 1), int(dy[index] + y0 - 1)
            cx = min(max(cx, half_w), width - half_w)
            cy = min(max(cy, half_h), height - half_h)
            rect = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
            overlap = sum(
                max(0, min(rect[2], o[2]) - max(rect[0], o[0])) * max(0, min(rect[3], o[3]) - max(rect[1], o[1]))
                for o in placed
            )
            if best is None or overlap < best[0]:
                best = (overlap, rect, cx, cy)
            if overlap == 0:
                break
        _, rect, cx, cy = best
        placed.append(rect)
        draw.text((cx, cy), text, fill=(255, 255, 255), font=font, anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))


def colorize_labels(array, names: dict[int, str] | None = None):
    """Viewable RGB id map: 0 black, other ids get distinct deterministic colors.

    With ``names`` (id -> text) each id's name is drawn at a point inside its largest
    region, with a dark outline so it stays readable.
    """
    import numpy as np
    from PIL import Image

    rgb = label_palette(int(array.max()) if array.size else 0)[array]
    if not names:
        return rgb
    image = Image.fromarray(rgb)
    draw_label_names(image, array, names)
    return np.asarray(image)


EXPOSED_COLOR = (255, 45, 85)
SKIN_COLOR = (255, 190, 60)
CLOTHED_COLOR = (40, 120, 255)
OTHER_COLOR = (150, 150, 150)
COMBINED_LAYERS = ("fill", "parts", "clothes", "names", "boxes", "pose")
DEFAULT_COMBINED_LAYERS = "fill,parts,clothes,names"


def combined_layers(text: str) -> tuple[str, ...]:
    """argparse type: comma list of COMBINED_LAYERS, or 'none' to skip the combined overlay."""
    if text.strip().lower() == "none":
        return ()
    layers = tuple(part.strip().lower() for part in text.split(",") if part.strip())
    if not layers or any(part not in COMBINED_LAYERS for part in layers):
        raise argparse.ArgumentTypeError(f"use a comma list of {', '.join(COMBINED_LAYERS)}, or none")
    return layers


def mask_edge(mask, width: int = 1):
    """Inner border of a boolean mask, ``width`` pixels thick (the image border does not count as outside)."""
    import numpy as np

    inner = mask
    for _ in range(width):
        padded = np.pad(inner, 1, constant_values=False)
        inner = inner & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    return mask & ~inner


def pixel_status(person, labels, detections, clothes):
    """Boolean masks (exposed, clothed, bare, other) over the person.

    exposed: pixels of a detector ``*_EXPOSED`` part that are not a garment (the clothes model often
    calls bare feet Background, so every non-garment pixel of such a part counts); clothed: garment
    pixels (without the clothes layer: pixels of ``*_COVERED`` parts); bare: other skin-labelled pixels.
    """
    import numpy as np

    inside = person > 0
    exposed_ids = [d["index"] + 2 for d in detections if d["label"].endswith("_EXPOSED")]
    if clothes is not None:
        clothed = np.isin(clothes["map"], [k + 1 for k, v in clothes["labels"].items() if v.lower() in CLOTHING_NAMES])
        bare = np.isin(clothes["map"], [k + 1 for k, v in clothes["labels"].items() if v.lower() in SKIN_NAMES])
    else:
        clothed = np.isin(labels, [d["index"] + 2 for d in detections if d["label"].endswith("_COVERED")])
        bare = np.zeros_like(inside)
    clothed = clothed & inside
    exposed = np.isin(labels, exposed_ids) & inside & ~clothed
    bare = bare & inside & ~clothed & ~exposed
    other = inside & ~clothed & ~exposed & ~bare
    return exposed, clothed, bare, other


def dashed_rectangle(draw, box, color, dash: int = 8) -> None:
    """Dashed rectangle outline."""
    x0, y0, x1, y1 = (float(v) for v in box)
    for x in range(int(x0), int(x1), dash * 2):
        for y in (y0, y1):
            draw.line((x, y, min(x + dash, x1), y), fill=color, width=1)
    for y in range(int(y0), int(y1), dash * 2):
        for x in (x0, x1):
            draw.line((x, y, x, min(y + dash, y1)), fill=color, width=1)


def render_combined(image, person, labels, detections, clothes, layers, pose_people=()):
    """One overlay with everything: status fill, part contours, clothing edges, names, legend.

    Fill color = status: red exposed (detector *_EXPOSED part, not a garment), amber bare skin,
    blue clothing, grey other (hair, shoes, background inside the mask). Contour hue = part.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    exposed, clothed, bare, other = pixel_status(person, labels, detections, clothes)
    inside = person > 0
    pixels = np.asarray(image, dtype=np.float32).copy()
    if "fill" in layers:
        pixels *= 0.7
        for mask, color, alpha in (
            (exposed, EXPOSED_COLOR, 0.55),
            (bare, SKIN_COLOR, 0.5),
            (clothed, CLOTHED_COLOR, 0.5),
            (other, OTHER_COLOR, 0.3),
        ):
            if mask.any():
                pixels[mask] = pixels[mask] * (1 - alpha) + np.array(color, np.float32) * alpha
    rgb = pixels.clip(0, 255).astype(np.uint8)
    palette = label_palette(int(labels.max()) if labels.size else 0)
    if "parts" in layers:
        for k in range(2, len(palette)):
            region = labels == k
            if region.any():
                rgb[mask_edge(region, 2)] = palette[k]
    if "clothes" in layers and clothes is not None:
        rgb[mask_edge(clothed, 1) & inside] = (0, 0, 0)
    rgb[mask_edge(inside, 2)] = (255, 255, 255)
    canvas = Image.fromarray(rgb)
    draw = ImageDraw.Draw(canvas)
    if "boxes" in layers:
        for det in detections:
            if det.get("source") == "parts":
                dashed_rectangle(draw, det["box_xyxy"], tuple(int(v) for v in palette[det["index"] + 2]))
    if "pose" in layers:
        for who in pose_people:
            points = who["keypoints"]
            for _, a, b, kind in POSE_LIMBS:
                if kind == "segment" and min(points[a][2], points[b][2]) > 0:
                    draw.line((points[a][0], points[a][1], points[b][0], points[b][1]), fill=(255, 255, 255), width=1)
            for x, y, c in points[5:17]:
                if c > 0:
                    draw.ellipse((x - 3, y - 3, x + 3, y + 3), outline=(255, 255, 255), width=1)
    if "names" in layers:
        draw_label_names(canvas, labels, {k: v for k, v in part_names(detections).items() if k != 1})
    px = max(13, min(rgb.shape[:2]) // 48)
    try:
        font = ImageFont.load_default(size=px)
    except TypeError:
        font = ImageFont.load_default()
    width, height = canvas.size
    bar = int(px * 3.6)
    result = Image.new("RGB", (width, height + bar), (24, 24, 24))
    result.paste(canvas, (0, 0))
    legend = ImageDraw.Draw(result)
    x, y = 8, height + px // 3
    for text, color in (
        ("exposed", EXPOSED_COLOR),
        ("bare skin", SKIN_COLOR),
        ("clothing", CLOTHED_COLOR),
        ("other", OTHER_COLOR),
    ):
        legend.rectangle((x, y, x + px, y + px), fill=color, outline=(255, 255, 255))
        legend.text((x + px + 5, y), text, fill=(255, 255, 255), font=font)
        x += px + 5 + int(legend.textlength(text, font=font)) + 14
    legend.text(
        (8, y + px + 4),
        "contour hue = part  |  black line = clothing edge  |  white = person",
        fill=(210, 210, 210),
        font=font,
    )
    return result


def save_outputs(
    destination: Path,
    person,
    labels,
    core,
    detections,
    overlay,
    report,
    timings,
    began,
    overwrite: bool = False,
    clothes=None,
    combined=None,
) -> None:
    import numpy as np
    from PIL import Image

    mode = "wb" if overwrite else "xb"

    def save_png(path: Path, array) -> None:
        with path.open(mode) as handle:
            Image.fromarray(array).save(handle, format="PNG")

    # New directory only; exclusive files ensure even partial runs are never overwritten.
    if overwrite:
        clear_owned_outputs(destination)
    destination.mkdir(parents=True, exist_ok=overwrite)
    (destination / "parts").mkdir(exist_ok=overwrite)
    (destination / "labels").mkdir(exist_ok=overwrite)
    start = time.perf_counter()
    save_png(destination / "person_mask.png", person)
    save_png(destination / "parts_label_map.png", labels)
    save_png(destination / "parts_core_label_map.png", core)
    save_png(destination / "parts_label_map_view.png", colorize_labels(labels, part_names(detections)))
    save_png(destination / "parts_core_label_map_view.png", colorize_labels(core, part_names(detections)))
    for det in detections:
        mask = labels == det["index"] + 2
        if mask.any():
            name = f"{det['index']:02d}_{re.sub(r'[^A-Za-z0-9_]', '_', det['label'])}.png"
            save_png(destination / "parts" / name, mask.astype(np.uint8) * 255)
            det["mask_file"] = f"parts/{name}"
        else:
            det["mask_file"] = None
    label_masks = {}  # union of all parts sharing a label, e.g. both FEMALE_BREAST_COVERED boxes
    for label in sorted({d["label"] for d in detections}):
        union = np.isin(labels, [d["index"] + 2 for d in detections if d["label"] == label])
        if union.any():
            name = re.sub(r"[^A-Za-z0-9_]", "_", label) + ".png"
            save_png(destination / "labels" / name, union.astype(np.uint8) * 255)
            label_masks[label] = f"labels/{name}"
    report["label_masks"] = label_masks
    if clothes is not None:  # separate layer: a pixel can be a body part and a garment at once
        (destination / "clothes").mkdir(exist_ok=overwrite)
        save_png(destination / "clothes_label_map.png", clothes["map"])
        save_png(
            destination / "clothes_label_map_view.png",
            colorize_labels(clothes["map"], {k + 1: v for k, v in clothes["labels"].items()}),
        )
        clothes_files = {}
        for k in range(1, len(clothes["labels"])):
            mask = clothes["map"] == k + 1
            if mask.any():
                name = f"{k:02d}_{re.sub(r'[^A-Za-z0-9_]', '_', clothes['labels'][k])}.png"
                save_png(destination / "clothes" / name, mask.astype(np.uint8) * 255)
                clothes_files[clothes["labels"][k]] = f"clothes/{name}"
        # Fusion: garment pixels vs skin pixels (Face/arms/legs/Skin), intersected with every part region.
        clothing_values = [k + 1 for k, v in clothes["labels"].items() if v.lower() in CLOTHING_NAMES]
        skin_values = [k + 1 for k, v in clothes["labels"].items() if v.lower() in SKIN_NAMES]
        clothed = np.isin(clothes["map"], clothing_values)
        skin_pixels = np.isin(clothes["map"], skin_values)
        save_png(destination / "clothed_mask.png", clothed.astype(np.uint8) * 255)
        save_png(destination / "skin_mask.png", skin_pixels.astype(np.uint8) * 255)
        (destination / "parts_fused").mkdir(exist_ok=overwrite)
        for det in detections:
            region = labels == det["index"] + 2
            covered, bare = region & clothed, region & skin_pixels
            det["clothed_pixels"], det["skin_pixels"] = int(covered.sum()), int(bare.sum())
            det["other_pixels"] = int(region.sum()) - det["clothed_pixels"] - det["skin_pixels"]
            base = f"{det['index']:02d}_{re.sub(r'[^A-Za-z0-9_]', '_', det['label'])}"
            det["fused_files"] = {}
            for tag, selected in (("covered", covered), ("skin", bare)):
                if selected.any():
                    save_png(destination / "parts_fused" / f"{base}_{tag}.png", selected.astype(np.uint8) * 255)
                    det["fused_files"][tag] = f"parts_fused/{base}_{tag}.png"
        report["clothes_fusion"] = {
            "clothing_labels": sorted(v for v in clothes["labels"].values() if v.lower() in CLOTHING_NAMES),
            "skin_labels": sorted(v for v in clothes["labels"].values() if v.lower() in SKIN_NAMES),
            "clothed_pixels": int(clothed.sum()),
            "skin_pixels": int(skin_pixels.sum()),
            "clothed_mask": "clothed_mask.png",
            "skin_mask": "skin_mask.png",
            "per_part": "each part carries clothed_pixels / skin_pixels / other_pixels and parts_fused/NN_LABEL_covered|skin.png",
        }
        with (destination / "clothes_overlay.jpg").open(mode) as handle:
            clothes["overlay"].save(handle, format="JPEG", quality=90)
        report["clothes_masks"] = clothes_files
    with (destination / "overlay.jpg").open(mode) as handle:
        overlay.save(handle, format="JPEG", quality=90)
    if combined is not None:
        with (destination / "combined_overlay.png").open(mode) as handle:
            combined.save(handle, format="PNG")
    timings["image_output_write"] = (time.perf_counter() - start) * 1000
    timings["total_before_report_write"] = (time.perf_counter() - began) * 1000
    with (destination / "report.json").open("w" if overwrite else "x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")


def cut_options(args) -> CutOptions:
    """Build CutOptions from parsed CLI arguments."""
    smooth = not args.no_mask_smoothing
    return CutOptions(
        grow=args.grow,
        shape=args.mask_box_limit_shape,
        roundness=args.mask_box_roundness,
        component=args.component_mode,
        center_radius=int(args.component_center_radius),
        min_component_area=int(args.component_min_area),
        fallback_nearest=args.component_fallback_nearest,
        close_kernel=int(args.mask_smooth_close) if smooth else 0,
        open_kernel=int(args.mask_smooth_open) if smooth else 0,
        blur_sigma=float(args.mask_smooth_blur) if smooth else 0.0,
        overlap=args.overlap,
    )


def run(args):
    import numpy as np
    import onnxruntime as ort

    began = time.perf_counter()
    timings = {}
    out = (
        resolve_batch_output(args.out, args.overwrite)
        if getattr(args, "batch", False)
        else resolve_output(args.out, args.overwrite)
    )
    LOG.info(
        "START YOLO26 + parts CPU threads=%d yolo_conf=%.3f parts_conf=%.3f iou=%.2f",
        args.threads,
        args.confidence,
        args.parts_confidence,
        args.parts_iou,
    )
    start = time.perf_counter()
    yolo_data, yolo_digest = read_verified(args.yolo_model, args.yolo_sha256, "yolo")
    parts_data, parts_digest = read_verified(args.parts_model, args.parts_sha256, "parts")
    clothes_data = clothes_digest = None
    if args.clothes_model:
        clothes_data, clothes_digest = read_verified(args.clothes_model, args.clothes_sha256, "clothes")
    pose_data = pose_digest = None
    if args.pose_model:
        pose_data, pose_digest = read_verified(args.pose_model, args.pose_sha256, "pose")
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
    person, yolo_diag = decode_person(
        detections_raw, prototypes, yolo_geometry, image.size, args.confidence, yolo_names, args.person_box_margin
    )
    timings["yolo_decode"] = (time.perf_counter() - start) * 1000
    yolo_io, yolo_providers = io_summary(yolo), yolo.get_providers()
    del detections_raw, prototypes, tensor, yolo
    person, holes_filled = fill_person_holes(person, args.fill_holes)
    LOG.info(
        "PERSON pixels=%d instances=%d holes_filled_px=%d",
        np.count_nonzero(person),
        len(yolo_diag["person_instances"]),
        holes_filled,
    )

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
    name_to_id = {name: index for index, name in parts_names.items()}
    unknown = [name for name in args.exclude if name not in name_to_id]
    if unknown:
        raise ValueError(f"Unknown --exclude class(es) {unknown}; known: {sorted(name_to_id)}")
    detections, parts_diag = decode_parts(
        raw,
        parts_geometry,
        image.size,
        args.parts_confidence,
        args.parts_iou,
        parts_names,
        [name_to_id[n] for n in args.exclude],
    )
    timings["parts_decode"] = (time.perf_counter() - start) * 1000
    parts_io, parts_providers = io_summary(parts), parts.get_providers()
    del raw, parts_tensor, parts

    for det in detections:
        det["source"] = "parts"

    # Stage 2b (optional): pose keypoints -> limb vector zones that run to the mask border.
    pose_people, pose_diag, pose_meta, pose_io, pose_providers, pose_geometry = [], None, None, None, None, None
    zones: dict[int, object] = {}
    zone_axes: dict[int, object] = {}
    pose_warnings = []
    if pose_data is not None:
        start = time.perf_counter()
        pose, pose_meta, pose_layout = pose_session(pose_data, args.threads)
        del pose_data
        timings["pose_session_load"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        pose_tensor, pose_geometry = letterbox(image)
        timings["pose_preprocessing"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        pose_raw = pose.run([pose.get_outputs()[0].name], {pose.get_inputs()[0].name: pose_tensor})[0]
        timings["pose_inference_cold_single_run"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        pose_people, pose_diag = decode_pose(
            pose_raw, pose_layout, pose_geometry, args.pose_confidence, args.pose_kpt_confidence, args.parts_iou
        )
        found = pose_zones(
            pose_people,
            person,
            args.pose_kpt_confidence,
            args.limb_width,
            args.hand_extent,
            args.leg_width,
            args.foot_extent,
            not args.no_leg_zones,
        )
        pose_io, pose_providers = io_summary(pose), pose.get_providers()
        del pose_raw, pose_tensor, pose
        feet_boxes = any(d["label"].startswith("FEET_") for d in detections)
        skip_feet = args.foot_zones == "never" or (args.foot_zones == "auto" and feet_boxes)
        if skip_feet:
            dropped = [f["label"] for f in found if f["label"].endswith("_FOOT")]
            found = [f for f in found if not f["label"].endswith("_FOOT")]
            pose_diag["foot_zones_skipped"] = {
                "policy": args.foot_zones,
                "nudenet_feet_boxes": feet_boxes,
                "labels": dropped,
            }
        room = MAX_PARTS - len(detections)
        if len(found) > room:
            pose_warnings.append(f"Limb zones capped: {len(found) - max(room, 0)} dropped at {MAX_PARTS} total parts")
            found = found[: max(room, 0)]
        for item in found:
            n = len(detections)
            ys, xs = np.nonzero(item["zone"])
            detections.append(
                {
                    "index": n,
                    "class_id": -1,
                    "label": item["label"],
                    "score": item["score"],
                    "box_xyxy": [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)],
                    "source": "pose",
                    "person_index": item["person_index"],
                }
            )
            zones[n] = item["zone"]
            zone_axes[n] = item["axis"]
        pose_diag["limb_zones"] = [d["label"] for d in detections if d["source"] == "pose"]
        timings["pose_decode_and_zones"] = (time.perf_counter() - start) * 1000
        LOG.info("POSE people=%d limb_zones=%d", len(pose_people), len(zones))

    # Stage 2c (optional): clothes segmentation on the person's crop; a separate label layer, not part of the cut.
    clothes = clothes_diag = clothes_io = clothes_geometry = None
    if clothes_data is not None:
        start = time.perf_counter()
        cloth_session, cloth_channels = clothes_session(clothes_data, args.threads)
        del clothes_data
        cloth_labels = load_clothes_labels(args.clothes_config, cloth_channels)
        timings["clothes_session_load"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        cloth_tensor, clothes_geometry = preprocess_clothes(image, person, args.clothes_size)
        timings["clothes_preprocessing"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        cloth_logits = cloth_session.run(
            [cloth_session.get_outputs()[0].name], {cloth_session.get_inputs()[0].name: cloth_tensor}
        )[0]
        timings["clothes_inference_cold_single_run"] = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        cloth_map, clothes_diag = decode_clothes(cloth_logits, clothes_geometry, person, cloth_labels)
        if args.face_below >= 0:
            cloth_map, cloth_labels, clothes_diag["face_cap"] = cap_face(
                cloth_map, cloth_labels, detections, args.face_below
            )
            clothes_diag["label_pixels"] = clothes_counts(cloth_map, cloth_labels)
        clothes = {
            "map": cloth_map,
            "labels": cloth_labels,
            "overlay": render_clothes_overlay(image, cloth_map, cloth_labels),
        }
        clothes_io = io_summary(cloth_session)
        del cloth_logits, cloth_tensor, cloth_session
        timings["clothes_decode_and_overlay"] = (time.perf_counter() - start) * 1000
        LOG.info("CLOTHES %s", clothes_diag["label_pixels"])

    if args.limb_skin_only and zones:
        clothed_now = np.isin(
            clothes["map"], [k + 1 for k, v in clothes["labels"].items() if v.lower() in CLOTHING_NAMES]
        )
        vetoed = {}
        for n, zone in zones.items():
            before = int(zone.sum())
            zone &= ~clothed_now
            vetoed[detections[n]["label"] + f"#{n}"] = before - int(zone.sum())
            if zone.any():
                ys, xs = np.nonzero(zone)
                detections[n]["box_xyxy"] = [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]
        pose_diag["limb_pixels_removed_as_clothing"] = vetoed

    # Stage 3: cut.
    start = time.perf_counter()
    options = cut_options(args)
    core, boxes, inside = split_person_mask(person, detections, options, zones, zone_axes)
    if args.expand == "rows":
        labels = expand_rows(core)
    elif args.expand == "nearest":
        labels = expand_to_mask(core, boxes, detections, args.expand_max)
    elif args.expand == "geodesic":
        labels = expand_geodesic(core, boxes, detections, args.expand_max, args.limb_expand_max)
    else:
        labels = core
    core_counts = np.bincount(core.ravel(), minlength=MAX_PARTS + 2)
    counts = np.bincount(labels.ravel(), minlength=MAX_PARTS + 2)
    label_pixels: dict[str, int] = {}
    for det, box, box_person in zip(detections, boxes, inside):
        k = det["index"] + 2
        det.update(
            box_grown_xyxy_int=list(box),
            box_person_pixels=box_person,
            core_pixels=int(core_counts[k]),
            part_pixels=int(counts[k]),
            expanded_pixels=int(counts[k] - core_counts[k]),
        )
        label_pixels[det["label"]] = label_pixels.get(det["label"], 0) + det["part_pixels"]
    overlay = render_overlay(image, labels, core, detections, boxes, pose_people)
    layers = getattr(args, "combined_layers", combined_layers(DEFAULT_COMBINED_LAYERS))
    combined = render_combined(image, person, labels, detections, clothes, layers, pose_people) if layers else None
    timings["split_and_overlay"] = (time.perf_counter() - start) * 1000

    person_px = int(np.count_nonzero(person))
    warnings = []
    if not person_px:
        warnings.append("Empty person mask; nothing to cut into parts")
    if not any(d["source"] == "parts" for d in detections):
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
    warnings.extend(pose_warnings)
    if pose_diag is not None and not zones:
        warnings.append(
            "Pose model ran but produced no limb zones (no confident shoulder/elbow/wrist keypoints, "
            "or zones empty on the person mask)"
        )
    for warning in warnings:
        LOG.warning(warning)
    LOG.info("PARTS %s", [(d["index"], d["label"], round(d["score"], 3), d["part_pixels"]) for d in detections])

    report = {
        "schema_version": 1,
        "algorithm": "yolo26-person-seg-nudenet-parts-v1",
        "models": {
            "person_segmentation": {
                "path": str(args.yolo_model.resolve()),
                "sha256": yolo_digest,
                "metadata": yolo_meta,
                "io": yolo_io,
            },
            "parts_detector": {
                "path": str(args.parts_model.resolve()),
                "sha256": parts_digest,
                "metadata": parts_meta,
                "io": parts_io,
                "class_names": parts_names,
            },
            **(
                {
                    "pose": {
                        "path": str(args.pose_model.resolve()),
                        "sha256": pose_digest,
                        "metadata": pose_meta,
                        "io": pose_io,
                    }
                }
                if pose_diag is not None
                else {}
            ),
            **(
                {
                    "clothes": {
                        "path": str(args.clothes_model.resolve()),
                        "sha256": clothes_digest,
                        "io": clothes_io,
                        "class_names": {str(k): v for k, v in clothes["labels"].items()},
                    }
                }
                if clothes is not None
                else {}
            ),
        },
        "source": {
            "path": str(args.source.resolve()),
            "raw_size_wh": list(raw_size),
            "oriented_size_wh": list(image.size),
        },
        "runtime": {
            "providers": {
                "person_segmentation": yolo_providers,
                "parts_detector": parts_providers,
                **({"pose": pose_providers} if pose_diag is not None else {}),
            },
            "onnxruntime": ort.__version__,
            "numpy": np.__version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "intra_op_threads": args.threads,
            "inter_op_threads": 1,
            "sequential": True,
            "spinning": False,
            "numpy_blas_threads": 1,
        },
        "preprocessing": {
            "person_segmentation": {
                "resize": "Pillow bilinear letterbox 640 square, pad114",
                "scale": "/255, no mean/std",
                **yolo_geometry,
            },
            "parts_detector": {
                "resize": "Pillow bilinear after black bottom/right pad to square, 320",
                "normalization": "/255, RGB, no mean/std",
                **parts_geometry,
            },
            **(
                {
                    "pose": {
                        "resize": "Pillow bilinear letterbox 640 square, pad114",
                        "scale": "/255, no mean/std",
                        **pose_geometry,
                    }
                }
                if pose_diag is not None
                else {}
            ),
            **(
                {
                    "clothes": {
                        "resize": "Pillow bilinear crop of the person bbox, sides multiples of 32",
                        **clothes_geometry,
                    }
                }
                if clothes is not None
                else {}
            ),
        },
        "decoding": {
            "person_segmentation": {
                "person_class": 0,
                "confidence": args.confidence,
                "mask_threshold": "logits >0",
                "mask_box_margin_input_px": args.person_box_margin,
                "fill_holes_max_px": args.fill_holes,
            },
            "parts_detector": {
                "layout": "cx,cy,w,h + per-class scores; best class per anchor",
                "confidence": args.parts_confidence,
                "excluded_classes": list(args.exclude),
                "nms": "greedy class-agnostic",
                "nms_iou": args.parts_iou,
                "max_parts": MAX_PARTS,
            },
            **(
                {
                    "pose": {
                        "confidence": args.pose_confidence,
                        "keypoint_confidence": args.pose_kpt_confidence,
                        "limb_width_fraction_of_length": args.limb_width,
                        "hand_extent_fraction_of_forearm": args.hand_extent,
                        "leg_width_fraction_of_length": args.leg_width,
                        "foot_extent_fraction_of_shin": args.foot_extent,
                        "leg_zones": not args.no_leg_zones,
                        "foot_zones": args.foot_zones,
                        "limb_skin_only": args.limb_skin_only,
                        "limbs": [label for label, *_ in POSE_LIMBS],
                        "zone_rule": "slab between planes perpendicular to the keypoint vector, within limb_width x "
                        "length of the axis, ∩ person ∩ connected piece through the axis; hands continue "
                        "past the wrist along the forearm vector for hand_extent x forearm length "
                        "(0 = until the mask ends); limb zones are painted after box parts and win "
                        "overlaps",
                    }
                }
                if pose_diag is not None
                else {}
            ),
            **(
                {
                    "clothes": {
                        "map_values": "0 outside person, class id + 1 inside (1 = person pixel classed Background)",
                        "rule": "bilinear-upsampled logits, per-pixel argmax over the crop, restricted to the person mask",
                        **clothes_diag,
                    }
                }
                if clothes is not None
                else {}
            ),
            "split": "person mask partitioned by part regions; overlaps between box parts follow cut.overlap "
            "(smaller: smaller box wins; center: nearest detection center in half-size units wins; nearest: "
            "nearest detection center in px wins; ties to higher score); pose limb zones are painted last",
            "grow": args.grow,
            "grow_rule": "each box scaled by (1 + grow) around its center, width and height, then clipped",
            "cut": asdict(options),
            "cut_rule": "grown box ∩ person ∩ limiter shape -> component under the detection center "
            "(disk fallback) -> close/open/blur smoothing -> ∩ person",
            "expand": args.expand,
            "expand_max_px": args.expand_max,
            "limb_expand_max_px": args.limb_expand_max,
            "expand_rule": EXPAND_RULES[args.expand],
        },
        "labels": {"outside_person": 0, "person_without_part_box": 1, "part": "detection index + 2"},
        "masks": {
            "person_pixels": person_px,
            "holes_filled_pixels": holes_filled,
            "unassigned_person_pixels": int(counts[1]),
            "assigned_fraction_of_person": (person_px - int(counts[1])) / person_px if person_px else 0.0,
            "core_unassigned_person_pixels": int(core_counts[1]),
            "core_assigned_fraction_of_person": (person_px - int(core_counts[1])) / person_px if person_px else 0.0,
            "label_pixels": dict(sorted(label_pixels.items(), key=lambda kv: -kv[1])),
            "polarity": "255 selected; 0 unselected",
        },
        "diagnostics": {
            "person_segmentation": yolo_diag,
            "parts_detector": parts_diag,
            **(
                {
                    "pose": {
                        **pose_diag,
                        "people_keypoints": [
                            {
                                "score": round(w["score"], 4),
                                "box_source_xyxy": [round(v, 1) for v in w["box_source_xyxy"]],
                                "keypoints_xyc": [[round(v, 1) for v in kp] for kp in w["keypoints"]],
                            }
                            for w in pose_people
                        ],
                    }
                }
                if pose_diag is not None
                else {}
            ),
        },
        "parts": detections,
        "warnings": warnings,
        "timings_ms": timings,
        "limitations": [
            "Parts are detector boxes intersected with the person mask: rectangular cuts, not part segmentation",
            "Growth and expansion are geometric, not anatomy-aware: rows mode leaves pixels outside every part's "
            "row band unassigned, nearest mode forces them onto the nearest part; with several people both can "
            "cross instances",
            "Smoothing uses Pillow square-kernel Max/Min filters, not OpenCV elliptical kernels; the component "
            "filter is 8-connected and seeded at the raw detection center",
            "Pose zones come from 17 COCO keypoints: no finger detail, the hand is only the area past the wrist "
            "along the forearm vector; arms pressed against the torso can leak a little into it, and a hand "
            "extension can run into hair or the head when the vector points there (set --hand-extent)",
            "Clothes layer: SegFormer logits are 1/4 of the input resolution, so edges are coarse; the ATR label "
            "set was trained on fashion photos and may mislabel skin, underwear or unusual poses; Left/Right follow "
            "the model's own convention; its license was not checked",
            "Detections are model output, not ground truth; expect false positives and misses",
            "Class-agnostic NMS can suppress overlapping parts (for example covered/exposed variants)",
            "Person mask is a union of up to 20 instances; parts are not attributed to instances",
            "Pillow resize and black-padding preprocessing not verified identical to the NudeNet reference",
            "Both models are AGPL-3.0 Ultralytics exports; check licensing before shipping in an app",
            "One cold run per model, not a benchmark",
            "Report paths and overlay may be sensitive",
        ],
    }
    if combined is not None:
        report["combined_overlay"] = {
            "file": "combined_overlay.png",
            "layers": list(layers),
            "fill_rgb": {
                "exposed": list(EXPOSED_COLOR),
                "bare_skin": list(SKIN_COLOR),
                "clothing": list(CLOTHED_COLOR),
                "other": list(OTHER_COLOR),
            },
            "rule": "exposed = non-garment pixels of detector *_EXPOSED parts; clothing = garment labels "
            "(or *_COVERED parts without the clothes layer); bare skin = other skin labels; "
            "contour hue = part; black line = clothing edge; white = person outline",
        }
    save_outputs(
        out, person, labels, core, detections, overlay, report, timings, began, args.overwrite, clothes, combined
    )
    LOG.info("DONE %s timings_ms=%s", out, timings)
    return out


def run_folder(args) -> int:
    """Run the pipeline on every image of args.source_dir; one failure does not stop the others."""
    try:
        jobs = folder_jobs(args.source_dir)
    except ValueError as error:
        LOG.error("%s", error)
        return 1
    if not jobs:
        LOG.error("No images (%s) in %s", "/".join(IMAGE_SUFFIXES), args.source_dir)
        return 1
    done, skipped, failed = [], [], []
    for number, (image, destination) in enumerate(jobs, 1):
        if destination.exists() and not args.overwrite:
            LOG.info("[%d/%d] skip %s: %s exists (use --overwrite)", number, len(jobs), image.name, destination)
            skipped.append(image.name)
            continue
        LOG.info("[%d/%d] %s -> %s", number, len(jobs), image.name, destination)
        job = copy.copy(args)
        job.source, job.out, job.batch = image, destination, True
        try:
            run(job)
            done.append(image.name)
        except Exception:
            LOG.exception("Failed on %s; continuing; any partial outputs are preserved in %s", image.name, destination)
            failed.append(image.name)
    print(
        f"Folder {args.source_dir}: {len(done)} done, {len(skipped)} skipped, {len(failed)} failed"
        + (f" ({', '.join(failed)})" if failed else "")
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("-s", "--source", type=Path, help="One local image (read only); needs -o")
    where.add_argument(
        "-d",
        "--source-dir",
        type=Path,
        help="Folder of images (not recursive, read only): each one is written to "
        "<folder>/analyze/<filename>/; existing result folders are skipped unless --overwrite",
    )
    parser.add_argument("-m", "--yolo-model", type=Path, required=True, help="YOLO26 end2end seg ONNX")
    parser.add_argument("-c", "--yolo-sha256", required=True, help="Expected SHA-256 of the YOLO model")
    parser.add_argument("--parts-model", type=Path, required=True, help="NudeNet-style 320 detect ONNX")
    parser.add_argument("--parts-sha256", required=True, help="Expected SHA-256 of the parts model")
    parser.add_argument("-f", "--confidence", type=confidence, default=0.25, help="YOLO person confidence")
    parser.add_argument(
        "--person-box-margin",
        type=pixel_count,
        default=8.0,
        help="widen the YOLO box crop of the person mask by this many model-input px (default 8; "
        "0 = crop exactly to the detection box, which can leave a strip at the image border)",
    )
    parser.add_argument("--clothes-model", type=Path, help="Optional SegFormer clothes ONNX (dynamic NCHW input)")
    parser.add_argument(
        "--clothes-sha256", help="Expected SHA-256 of the clothes model (required with --clothes-model)"
    )
    parser.add_argument("--clothes-config", type=Path, help="Optional Hugging Face config.json with id2label")
    parser.add_argument(
        "--face-below",
        type=float,
        default=0.15,
        help="clothes layer: Face label may reach this fraction of the NudeNet face box height below "
        "the box; Face pixels further away become Skin (default 0.15, negative disables)",
    )
    parser.add_argument(
        "--clothes-size",
        type=clothes_side,
        default=512,
        help="long side of the person crop fed to the clothes model (multiple of 32, default 512)",
    )
    parser.add_argument("--pose-model", type=Path, help="Optional Ultralytics pose ONNX (17 COCO keypoints, 640)")
    parser.add_argument("--pose-sha256", help="Expected SHA-256 of the pose model (required with --pose-model)")
    parser.add_argument("--pose-confidence", type=confidence, default=0.25, help="Pose person confidence")
    parser.add_argument(
        "--pose-kpt-confidence", type=confidence, default=0.3, help="Minimum keypoint confidence for a limb endpoint"
    )
    parser.add_argument(
        "--limb-width",
        type=grow_factor,
        default=0.35,
        help="max distance from the limb axis as a fraction of its length (default 0.35)",
    )
    parser.add_argument(
        "--limb-expand-max",
        type=pixel_count,
        default=None,
        help="geodesic only: max rings pose limb zones may flood beyond their zone (default: same as "
        "--expand-max; 0 = limbs keep exactly their zone)",
    )
    parser.add_argument(
        "--hand-extent",
        type=grow_factor,
        default=0.0,
        help="hand zone length past the wrist as a fraction of the forearm; 0 (default) = until the mask ends",
    )
    parser.add_argument(
        "--leg-width",
        type=grow_factor,
        default=0.20,
        help="leg zones: max distance from the leg axis as a fraction of its length (default 0.20)",
    )
    parser.add_argument(
        "--foot-extent",
        type=grow_factor,
        default=0.0,
        help="foot zone length past the ankle as a fraction of the shin; 0 (default) = until the mask ends",
    )
    parser.add_argument(
        "--foot-zones",
        choices=("auto", "always", "never"),
        default="auto",
        help="pose foot zones: auto (default) skips them when the parts detector found FEET_* boxes, "
        "which then keep their own region; always/never force them on/off",
    )
    parser.add_argument("--no-leg-zones", action="store_true", help="arms and hands only, no thigh/shin/foot zones")
    parser.add_argument(
        "--limb-skin-only",
        action="store_true",
        help="remove clothing pixels (needs --clothes-model) from every pose limb zone, so a hand "
        "behind a skirt or an arm under a strap does not claim the garment",
    )
    parser.add_argument("--parts-confidence", type=confidence, default=0.25, help="Parts detector confidence")
    parser.add_argument("--parts-iou", type=confidence, default=0.45, help="Parts NMS IoU threshold")
    parser.add_argument(
        "--exclude",
        nargs="*",
        type=str.upper,
        metavar="CLASS",
        default=["ARMPITS_COVERED", "ARMPITS_EXPOSED"],
        help="Part classes to ignore (default: both armpit classes); pass --exclude alone to keep all",
    )
    parser.add_argument(
        "--grow",
        type=grow_factor,
        default=0.30,
        help="scale each part box by (1+GROW) before cutting (default 0.30 = 130%%)",
    )
    parser.add_argument(
        "--mask-box-limit-shape",
        choices=("rectangle", "rounded", "ellipse"),
        default="rectangle",
        help="limit each grown box to an inscribed shape (ellipse drops the corners)",
    )
    parser.add_argument(
        "--mask-box-roundness",
        type=grow_factor,
        default=0.5,
        help="rounded shape only: corner radius as a fraction of the half short side",
    )
    parser.add_argument(
        "--component-mode",
        choices=("center", "none"),
        default="center",
        help="center (default): keep only the person component under the detection center",
    )
    parser.add_argument(
        "--component-center-radius",
        type=pixel_count,
        default=8,
        help="disk radius in px searched when the center pixel is not on the mask",
    )
    parser.add_argument(
        "--component-min-area", type=pixel_count, default=12, help="drop components smaller than this many px"
    )
    parser.add_argument(
        "--component-fallback-nearest",
        action="store_true",
        help="if no component is near the center, use the nearest one",
    )
    parser.add_argument("--mask-smooth-close", type=pixel_count, default=9, help="closing kernel px (0 = off)")
    parser.add_argument("--mask-smooth-open", type=pixel_count, default=3, help="opening kernel px (0 = off)")
    parser.add_argument("--mask-smooth-blur", type=pixel_count, default=1.2, help="Gaussian sigma px (0 = off)")
    parser.add_argument("--no-mask-smoothing", action="store_true", help="disable close/open/blur")
    parser.add_argument(
        "--overlap",
        choices=("center", "nearest", "smaller"),
        default="center",
        help="who wins pixels claimed by several part boxes: center (default) = nearest detection "
        "center in units of its own half-size (favors big boxes); nearest = nearest detection "
        "center in px; smaller = smaller box",
    )
    parser.add_argument(
        "--fill-holes",
        type=pixel_count,
        default=1500,
        help="fill enclosed holes of the person mask up to this many px before cutting (default 1500; 0 = off)",
    )
    parser.add_argument(
        "--expand",
        choices=tuple(EXPAND_RULES),
        default="rows",
        help="rows (default): extend parts horizontally to the mask border or another part; "
        "geodesic: flood through the person mask from each cut, following limbs and curves; "
        "nearest: nearest-box fill; none: grown-box cuts only",
    )
    parser.add_argument(
        "--expand-max",
        type=pixel_count,
        default=0.0,
        help="nearest: max px from a box; geodesic: max rings (about px) of flooding (0 = unlimited)",
    )
    parser.add_argument(
        "--combined-layers",
        type=combined_layers,
        default=DEFAULT_COMBINED_LAYERS,
        help="combined_overlay.png layers, comma list of "
        + ", ".join(COMBINED_LAYERS)
        + f" (default {DEFAULT_COMBINED_LAYERS}); none = skip the image",
    )
    parser.add_argument("-t", "--threads", type=thread_count, default=1)
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        help="Directory below repository tmp/ (new unless --overwrite); required with -s, not allowed with -d",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="reuse an existing -o directory: replaces this script's own outputs "
        "(known files, parts/*.png, labels/*.png) after a successful run; nothing else is deleted",
    )
    args = parser.parse_args(argv)
    if args.source_dir is None and args.out is None:
        parser.error("-o/--out is required with -s/--source")
    if args.source_dir is not None and args.out is not None:
        parser.error("-o/--out cannot be combined with -d/--source-dir (outputs go to <folder>/analyze/<filename>/)")
    if bool(args.clothes_model) != bool(args.clothes_sha256):
        parser.error("--clothes-model and --clothes-sha256 must be given together")
    if args.limb_skin_only and not args.clothes_model:
        parser.error("--limb-skin-only needs --clothes-model")
    if bool(args.pose_model) != bool(args.pose_sha256):
        parser.error("--pose-model and --pose-sha256 must be given together")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.source_dir is not None:
        return run_folder(args)
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

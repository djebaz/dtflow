"""Worker-only COCO body extraction and OpenPose-18 maps; no implicit downloads or submission.

YOLO11n raw ONNX: RGB /255, centered 640 letterbox, [1,56,8400] xywh/score/17*(x,y,p).
OpenPose body palette/topology follows controlnet_aux b1aac318b15c4e65a21bc516dc236ad4bf593d46.
Only the first 17 limbs are drawn, as in its body renderer. No fingers or facial contours.
"""

import hashlib
import json
import logging
import math
import os
from dataclasses import asdict
from pathlib import Path
from time import monotonic

import numpy as np
from PIL import Image, ImageDraw

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event
from dtflow.persistence.person_models import ModelRef
from dtflow.services import image_preparation as pixels
from dtflow.services.model_provisioning import CatalogueEntry
from dtflow.services.person_selection import _cpu_session, _nms, _read_model, _run, _verified_session

log = logging.getLogger(__name__)
DETECTOR = CatalogueEntry(
    "pose", "YOLO11n body pose (COCO-17, AGPL-3.0)",
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n-pose.onnx",
    "93e2866b0ce678f99b4dd88af0c12e9ea2edf079a361dc2ecbc6226b77ff6408", 11.31,
    "Ultralytics AGPL-3.0", "https://github.com/ultralytics/ultralytics/blob/main/LICENSE",
    "https://github.com/ultralytics/assets/releases/tag/v8.4.0",
)
PALETTE = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0),
    (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255),
    (0, 0, 255), (85, 0, 255), (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85),
)
LIMBS = ((1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
         (1, 11), (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17))
ORDER = (0, None, 6, 8, 10, 5, 7, 9, 12, 14, 16, 11, 13, 15, 2, 1, 4, 3)


def _pose_session(data: bytes):
    session = _cpu_session(data)
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (
        len(inputs) != 1 or inputs[0].shape != [1, 3, 640, 640] or inputs[0].type != "tensor(float)"
        or len(outputs) != 1 or outputs[0].shape != [1, 56, 8400] or outputs[0].type != "tensor(float)"
    ):
        raise ValidationError("Expected pinned YOLO11n-pose float32 [1,3,640,640] -> [1,56,8400] ONNX export.")
    return session


def model_path(state: Path) -> Path:
    return state / "models" / "person" / f"{DETECTOR.sha256}.onnx"


def install_detector(state: Path, progress=None) -> dict:
    """Explicit download only; reuse the common HTTPS/checksum/atomic-install boundary."""
    from dtflow.services.model_provisioning import _install, install_dir

    folder = install_dir(state)
    folder.mkdir(parents=True, exist_ok=True)
    ref = _install(DETECTOR, folder, progress or (lambda *args: None), None)
    return {"detector": asdict(DETECTOR), "path": ref.path}


def extract_body(source: Path, state: Path):
    """Require exactly one confident body after NMS; omit uncertain joints, never invent a person."""
    started = monotonic()
    path = model_path(state)
    if not path.is_file():
        raise ValidationError("Pose detector is not provisioned. Explicitly run 'pose install' after reviewing its licence.")
    # Always rehash this small pinned model before constructing/reusing the shared CPU session.
    if hashlib.sha256(_read_model(str(path))).hexdigest() != DETECTOR.sha256:
        raise ValidationError("Pose detector differs from the pinned publisher checksum; not used.")
    session = _verified_session(ModelRef(str(path), DETECTOR.sha256), _pose_session)
    content = pixels.load_source_file(source)
    image = pixels._verified(content)
    scale = min(640 / image.width, 640 / image.height)
    size = (round(image.width * scale), round(image.height * scale))
    left, top = (640 - size[0]) // 2, (640 - size[1]) // 2
    letterbox = Image.new("RGB", (640, 640), (114, 114, 114))
    letterbox.paste(image.resize(size, Image.Resampling.BILINEAR), (left, top))
    tensor = np.asarray(letterbox, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    raw = _run(session, tensor, role="pose")[0]
    if raw.shape != (1, 56, 8400) or not np.isfinite(raw).all():
        raise ValidationError("Pose detector returned malformed or non-finite coordinates.")
    rows = raw[0].T
    rows = rows[(rows[:, 4] >= 0.5) & (rows[:, 2] > 0) & (rows[:, 3] > 0)]
    if len(rows) > 1000:
        raise ValidationError("Too many pose candidates; use a clear single-person photograph.")
    boxes = np.column_stack((rows[:, :2] - rows[:, 2:4] / 2, rows[:, :2] + rows[:, 2:4] / 2))
    keep = _nms(boxes, rows[:, 4], 0.45)
    if len(keep) != 1:
        raise ValidationError(f"Expected exactly one body above confidence 0.5; found {len(keep)} after NMS. Use a single-person photo.")
    joints = rows[keep[0], 5:].reshape(17, 3).copy()
    joints[:, 0] = (joints[:, 0] - left) * image.width / size[0]
    joints[:, 1] = (joints[:, 1] - top) * image.height / size[1]
    points = []
    for index in ORDER:
        joint = (joints[5] + joints[6]) / 2 if index is None else joints[index]
        confidence = min(joints[5, 2], joints[6, 2]) if index is None else joint[2]
        points.append(tuple(map(float, joint[:2])) if confidence >= 0.5 and
                      0 <= joint[0] < image.width and 0 <= joint[1] < image.height else None)
    if not all(points[i] is not None for i in (1, 2, 5, 8, 11)):
        raise ValidationError("Shoulders/hips are incomplete at confidence 0.5; choose a clearer body photo.")
    canvas = Image.new("RGB", image.size, "black")
    # Elliptical sticks and radius-4 joints match the reference body renderer's colour order.
    for i, (a, b) in enumerate(LIMBS):
        if points[a] is None or points[b] is None:
            continue
        x1, y1 = points[a]
        x2, y2 = points[b]
        angle = math.atan2(y2 - y1, x2 - x1)
        length = math.hypot(x2 - x1, y2 - y1) / 2
        polygon = []
        for degrees in range(0, 360, 5):
            theta = math.radians(degrees)
            x, y = length * math.cos(theta), 4 * math.sin(theta)
            polygon.append(((x1 + x2) / 2 + x * math.cos(angle) - y * math.sin(angle),
                            (y1 + y2) / 2 + x * math.sin(angle) + y * math.cos(angle)))
        layer = canvas.copy()
        ImageDraw.Draw(layer).polygon(polygon, fill=PALETTE[i])
        canvas = Image.blend(canvas, layer, 0.6)
    draw = ImageDraw.Draw(canvas)
    for point, colour in zip(points, PALETTE, strict=True):
        if point is not None:
            x, y = point
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=colour)
    result = pixels._content(canvas)
    elapsed = monotonic() - started
    evidence = {"detector": DETECTOR.name, "detector_sha256": DETECTOR.sha256,
                "detector_input": "640x640", "source_sha256": content.sha256,
                "source_dimensions": f"{image.width}x{image.height}", "seconds": str(elapsed),
                "person_selection": "exactly_one_after_nms", "joint_threshold": "0.5",
                "missing_joints": str(sum(p is None for p in points)), "topology": "openpose18_body_only"}
    event(log, "pose.extracted", data={"detector_sha256": DETECTOR.sha256, "seconds": elapsed,
                                     "missing_joints": sum(p is None for p in points)})
    return result, evidence


def save_extraction(path: Path, content, evidence: dict) -> dict:
    """Exclusive local output plus hash-bound extraction evidence; no state store or backend."""
    sidecar = path.with_suffix(".pose.json")
    if path.exists() or sidecar.exists():
        raise ValidationError("Extraction output or evidence already exists; choose a new output name.")
    report = {"map_sha256": content.sha256, "evidence": evidence}
    for target, data in ((path, content.encoded), (sidecar, json.dumps(report, indent=2).encode("utf-8") + b"\n")):
        with target.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return {"map": str(path), "evidence": str(sidecar), **report}


def prepare_pose(path: Path, width: int, height: int, *, photo: bool = False, state: Path):
    """Shared future UI seam: immutable map + bounded evidence, not dispatch consent.

    Fit without crop/stretch, black padding, RGB PNG. The existing snapshot validator
    retains the effective target-grid map; original fit is recorded separately below.
    """
    if photo:
        content, evidence = extract_body(path, state)
    else:
        content = pixels.load_source_file(path)
        evidence = {"origin": "prepared_map", "topology": "owner_supplied_openpose_body"}
        sidecar = path.with_suffix(".pose.json")
        if sidecar.exists():
            from dtflow.services.config import read_json_file

            report = read_json_file(sidecar, "Pose extraction evidence")
            if not isinstance(report, dict) or report.get("map_sha256") != content.sha256:
                raise ValidationError("Prepared map differs from its extraction evidence.")
            evidence.update(report["evidence"])
    image = pixels._verified(content)
    if not image.getbbox() or image.getextrema() == ((255, 255),) * 3:
        raise ValidationError("Pose map is empty; no guidance was submitted.")
    fit = pixels._fit(image.size, (width, height))
    canvas = Image.new("RGB", (width, height), "black")
    canvas.paste(image.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.LANCZOS), (fit.left, fit.top))
    evidence.update({"map_sha256": content.sha256, "preparation": "aspect_fit_black_padding_no_crop",
                     "fit": json.dumps(asdict(fit), separators=(",", ":"))})
    prepared = pixels.prepare_image_bytes(pixels._content(canvas).encoded, width, height)
    return prepared, tuple(sorted((str(k), str(v)) for k, v in evidence.items()))

"""Worker-only COCO body extraction and OpenPose-18 maps; no implicit downloads or submission.

YOLO26n-pose end2end ONNX: RGB /255, centered 640 letterbox, [1,300,57] rows of x1,y1,x2,y2,score,class and
17*(x,y,confidence) in letterboxed pixels; no NMS step (the export already did it).
OpenPose body palette/topology follows controlnet_aux b1aac318b15c4e65a21bc516dc236ad4bf593d46.
Only the first 17 limbs are drawn, as in its body renderer. No fingers or facial contours.
"""

import ast
import hashlib
import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic

import numpy as np
from PIL import Image, ImageDraw

from dtflow.core.models import ValidationError
from dtflow.diagnostics import event
from dtflow.persistence.person_models import ModelRef
from dtflow.services import image_preparation as pixels
from dtflow.services.model_provisioning import CatalogueEntry
from dtflow.services.person_selection import _cpu_session, _letterbox, _read_model, _run, _verified_session

log = logging.getLogger(__name__)
DETECTOR = CatalogueEntry(
    "pose",
    "YOLO26n body pose (COCO-17, AGPL-3.0)",
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.onnx",
    "93fc5e1d6b7690f33b4e1d60d6e9aec1cea14bdbc361bfae11778969be662078",
    11.56,
    "Ultralytics AGPL-3.0",
    "https://github.com/ultralytics/ultralytics/blob/main/LICENSE",
    "https://github.com/ultralytics/assets/releases/tag/v8.4.0",
)
DETECTOR_BYTES = 12_125_856
CONFIDENCE = 0.5
MAX_CANDIDATES = 10
PALETTE = (
    (255, 0, 0),
    (255, 85, 0),
    (255, 170, 0),
    (255, 255, 0),
    (170, 255, 0),
    (85, 255, 0),
    (0, 255, 0),
    (0, 255, 85),
    (0, 255, 170),
    (0, 255, 255),
    (0, 170, 255),
    (0, 85, 255),
    (0, 0, 255),
    (85, 0, 255),
    (170, 0, 255),
    (255, 0, 255),
    (255, 0, 170),
    (255, 0, 85),
)
LIMBS = (
    (1, 2),
    (1, 5),
    (2, 3),
    (3, 4),
    (5, 6),
    (6, 7),
    (1, 8),
    (8, 9),
    (9, 10),
    (1, 11),
    (11, 12),
    (12, 13),
    (1, 0),
    (0, 14),
    (14, 16),
    (0, 15),
    (15, 17),
)
ORDER = (0, None, 6, 8, 10, 5, 7, 9, 12, 14, 16, 11, 13, 15, 2, 1, 4, 3)
JOINT_NAMES = (
    "nose",
    "neck",
    "right shoulder",
    "right elbow",
    "right wrist",
    "left shoulder",
    "left elbow",
    "left wrist",
    "right hip",
    "right knee",
    "right ankle",
    "left hip",
    "left knee",
    "left ankle",
    "right eye",
    "left eye",
    "right ear",
    "left ear",
)


def _pose_session(data: bytes):
    session = _cpu_session(data)
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (
        len(inputs) != 1
        or inputs[0].shape != [1, 3, 640, 640]
        or inputs[0].type != "tensor(float)"
        or len(outputs) != 1
        or outputs[0].shape != [1, 300, 57]
        or outputs[0].type != "tensor(float)"
    ):
        raise ValidationError("Expected pinned YOLO26n-pose float32 [1,3,640,640] -> [1,300,57] end2end ONNX export.")
    meta = session.get_modelmeta().custom_metadata_map
    try:
        keypoints = ast.literal_eval(meta.get("kpt_shape", ""))
    except (ValueError, SyntaxError):
        keypoints = None
    if meta.get("task") != "pose" or meta.get("end2end", "").lower() != "true" or list(keypoints or ()) != [17, 3]:
        raise ValidationError("Pose detector must be a YOLO26 end2end pose export with 17 COCO keypoints.")
    return session


def model_path(state: Path) -> Path:
    return state / "models" / "person" / f"{DETECTOR.sha256}.onnx"


def detector_ready(state: Path) -> bool:
    """GUI-side readiness: the pinned file (named by its SHA-256) is installed and has the pinned size."""
    try:
        return model_path(state).stat().st_size == DETECTOR_BYTES
    except OSError:
        return False


def install_detector(state: Path, progress=None, cancel=None) -> dict:
    """Explicit download only; reuse the common HTTPS/checksum/atomic-install boundary."""
    from dtflow.services.model_provisioning import _install, install_dir

    folder = install_dir(state)
    folder.mkdir(parents=True, exist_ok=True)
    ref = _install(DETECTOR, folder, progress or (lambda *args: None), cancel)
    return {"detector": asdict(DETECTOR), "path": ref.path}


@dataclass(frozen=True, slots=True)
class PoseCandidate:
    """One detected body: 1-based list index, score, box in source pixels, 17 COCO joints (x, y, confidence)."""

    index: int
    score: float
    box: tuple[int, int, int, int]
    joints: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True, slots=True)
class PoseDetection:
    width: int
    height: int
    source_sha256: str
    candidates: tuple[PoseCandidate, ...]
    seconds: float


def detect_people(content, state: Path) -> PoseDetection:
    """Every body above the confidence cutoff, strongest first (at most 10); worker only, nothing is submitted."""
    started = monotonic()
    path = model_path(state)
    if not path.is_file():
        raise ValidationError("Pose detector is not provisioned. Download it after reviewing its licence.")
    # Always rehash this small pinned model before constructing/reusing the shared CPU session.
    if hashlib.sha256(_read_model(str(path))).hexdigest() != DETECTOR.sha256:
        raise ValidationError("Pose detector differs from the pinned publisher checksum; not used.")
    session = _verified_session(ModelRef(str(path), DETECTOR.sha256), _pose_session)
    image = pixels._verified(content)
    tensor, (left, top, width, height) = _letterbox(image)
    raw = _run(session, tensor, role="pose")[0]
    if raw.shape != (1, 300, 57) or not np.isfinite(raw).all():
        raise ValidationError("Pose detector returned malformed or non-finite coordinates.")
    rows = raw[0]
    rows = rows[(rows[:, 4] >= CONFIDENCE) & (rows[:, 2] > rows[:, 0]) & (rows[:, 3] > rows[:, 1])]
    rows = rows[np.argsort(-rows[:, 4])][:MAX_CANDIDATES]
    candidates = []
    for index, row in enumerate(rows, start=1):
        joints = row[6:].reshape(17, 3).copy()
        joints[:, 0] = (joints[:, 0] - left) * image.width / width
        joints[:, 1] = (joints[:, 1] - top) * image.height / height
        x0, y0 = (row[0] - left) * image.width / width, (row[1] - top) * image.height / height
        x1, y1 = (row[2] - left) * image.width / width, (row[3] - top) * image.height / height
        box = (
            max(0, int(x0)),
            max(0, int(y0)),
            min(image.width, int(math.ceil(x1))),
            min(image.height, int(math.ceil(y1))),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        candidates.append(
            PoseCandidate(index, float(row[4]), box, tuple((float(x), float(y), float(c)) for x, y, c in joints))
        )
    seconds = monotonic() - started
    event(
        log, "pose.detected", data={"detector_sha256": DETECTOR.sha256, "seconds": seconds, "people": len(candidates)}
    )
    return PoseDetection(image.width, image.height, content.sha256, tuple(candidates), seconds)


def body_map(content, detection: PoseDetection, person: int):
    """OpenPose body map of one candidate (1-based index) at the source size; omit uncertain joints."""
    if detection.source_sha256 != content.sha256:
        raise ValidationError("The detection belongs to another image; extract again.")
    if not any(candidate.index == person for candidate in detection.candidates):
        raise ValidationError(f"There is no person {person}; found {len(detection.candidates)}.")
    candidate = next(candidate for candidate in detection.candidates if candidate.index == person)
    image = pixels._verified(content)
    joints = np.array(candidate.joints, dtype=np.float32)
    points, omitted = [], []
    for slot, index in enumerate(ORDER):
        joint = (joints[5] + joints[6]) / 2 if index is None else joints[index]
        confidence = min(joints[5, 2], joints[6, 2]) if index is None else joint[2]
        inside = 0 <= joint[0] < image.width and 0 <= joint[1] < image.height
        points.append(tuple(map(float, joint[:2])) if confidence >= 0.5 and inside else None)
        if points[-1] is None:
            omitted.append(f"{JOINT_NAMES[slot]} ({'outside the image' if confidence >= 0.5 else 'low confidence'})")
    if not all(points[i] is not None for i in (1, 2, 5, 8, 11)):
        raise ValidationError(
            f"Person {person}: shoulders or hips were not found with enough confidence. "
            "Pick another person or use a clearer body photo."
        )
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
            polygon.append(
                (
                    (x1 + x2) / 2 + x * math.cos(angle) - y * math.sin(angle),
                    (y1 + y2) / 2 + x * math.sin(angle) + y * math.cos(angle),
                )
            )
        layer = canvas.copy()
        ImageDraw.Draw(layer).polygon(polygon, fill=PALETTE[i])
        canvas = Image.blend(canvas, layer, 0.6)
    draw = ImageDraw.Draw(canvas)
    for point, colour in zip(points, PALETTE, strict=True):
        if point is not None:
            x, y = point
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=colour)
    result = pixels._content(canvas)
    missing = sum(p is None for p in points)
    evidence = {
        "detector": DETECTOR.name,
        "detector_sha256": DETECTOR.sha256,
        "detector_input": "640x640",
        "source_sha256": content.sha256,
        "source_dimensions": f"{image.width}x{image.height}",
        "seconds": str(detection.seconds),
        "person_selection": f"person_{person}_of_{len(detection.candidates)}",
        "joint_threshold": "0.5",
        "missing_joints": str(missing),
        "omitted_joints": ", ".join(omitted),
        "topology": "openpose18_body_only",
    }
    event(
        log,
        "pose.extracted",
        data={"detector_sha256": DETECTOR.sha256, "seconds": detection.seconds, "missing_joints": missing},
    )
    return result, evidence


def extract_body(source: Path, state: Path, person: int | None = None):
    """One confident body: with no index exactly one candidate is required (never guessed); omit uncertain joints."""
    content = pixels.load_source_file(source)
    detection = detect_people(content, state)
    if person is None:
        if len(detection.candidates) != 1:
            raise ValidationError(
                f"Expected exactly one body above confidence {CONFIDENCE}; found {len(detection.candidates)}. "
                "Use a single-person photo or choose one with --person."
            )
        person = 1
    return body_map(content, detection, person)


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


def fit_pose_map(content, evidence: dict, width: int, height: int):
    """Immutable map + bounded evidence, not dispatch consent.

    Fit without crop/stretch, black padding, RGB PNG. The existing snapshot validator
    retains the effective target-grid map; original fit is recorded separately below.
    """
    image = pixels._verified(content)
    if not image.getbbox() or image.getextrema() == ((255, 255),) * 3:
        raise ValidationError("Pose map is empty; no guidance was submitted.")
    fit = pixels._fit(image.size, (width, height))
    canvas = Image.new("RGB", (width, height), "black")
    canvas.paste(image.resize((fit.fitted_width, fit.fitted_height), Image.Resampling.LANCZOS), (fit.left, fit.top))
    evidence = dict(evidence)
    evidence.update(
        {
            "map_sha256": content.sha256,
            "preparation": "aspect_fit_black_padding_no_crop",
            "fit": json.dumps(asdict(fit), separators=(",", ":")),
        }
    )
    prepared = pixels.prepare_image_bytes(pixels._content(canvas).encoded, width, height)
    return prepared, tuple(sorted((str(k), str(v)) for k, v in evidence.items()))


def load_prepared_map(path: Path):
    """A prepared OpenPose body map from disk with its optional hash-bound extraction evidence."""
    content = pixels.load_source_file(path)
    evidence = {"origin": "prepared_map", "topology": "owner_supplied_openpose_body"}
    sidecar = path.with_suffix(".pose.json")
    if sidecar.exists():
        from dtflow.services.config import read_json_file

        report = read_json_file(sidecar, "Pose extraction evidence")
        if not isinstance(report, dict) or report.get("map_sha256") != content.sha256:
            raise ValidationError("Prepared map differs from its extraction evidence.")
        evidence.update(report["evidence"])
    return content, evidence


def prepare_pose(path: Path, width: int, height: int, *, photo: bool = False, state: Path, person: int | None = None):
    """Seam shared by the CLI and the Controlnet mode: the map for one request, fitted to its size."""
    if photo:
        content, evidence = extract_body(path, state, person)
    else:
        content, evidence = load_prepared_map(path)
    return fit_pose_map(content, evidence, width, height)

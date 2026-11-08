"""Headless, CPU-only person selection for the Inpaint editor: YOLO26 person instances plus an optional
SegFormer clothes model, from owner-selected local ONNX files (no downloads, no bundled weights, no GPU).

Run every function here on a worker. Suggestions can be wrong; the editor lets the user refine in Paint.
Masks are cropped to each person's source-pixel box to bound memory.
"""

import ast
import hashlib
import io
import logging
import math
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from dtflow.core.image_inputs import ImageContent
from dtflow.core.models import ValidationError
from dtflow.diagnostics import event as diagnostic_event
from dtflow.persistence.person_models import ModelRef, PersonModels
from dtflow.services.artifact_paths import ArtifactReadPaths

log = logging.getLogger(__name__)

MAX_MODEL_BYTES = 256 * 1024 * 1024
SIDE = 640
MAX_PEOPLE = 10
BOX_MARGIN = 8.0
CLOTHES_SIZE = 512
CLOTHES_LABELS = (
    "Background",
    "Hat",
    "Hair",
    "Sunglasses",
    "Upper-clothes",
    "Skirt",
    "Pants",
    "Dress",
    "Belt",
    "Left-shoe",
    "Right-shoe",
    "Face",
    "Left-leg",
    "Right-leg",
    "Left-arm",
    "Right-arm",
    "Bag",
    "Scarf",
)
CLOTHING = frozenset({"upper-clothes", "skirt", "pants", "dress", "belt", "scarf"})
SKIN = frozenset({"face", "left-leg", "right-leg", "left-arm", "right-arm"})
SKIN_CLASS = len(CLOTHES_LABELS)  # synthetic: Face pixels the faces model places outside any head (neck, chest)
FACES_SIDE = 320
FACES_CONFIDENCE = 0.25
FACES_IOU = 0.45
FACE_BELOW = 0.15  # a face may reach this fraction of its box height under the box
# Selectable layers. "person" is the whole instance silhouette; the rest come from the clothes model's classes.
GARMENT_CLASSES = {
    "upper clothes": (4,),
    "pants": (6,),
    "skirt": (5,),
    "dress": (7,),
    "belt": (8,),
    "scarf": (17,),
    "hair": (2,),
    "hat": (1,),
    "sunglasses": (3,),
    "face": (11,),
    "arms": (14, 15),
    "legs": (12, 13),
    "shoes": (9, 10),
    "bag": (16,),
    "skin": (18,),
}
GROUP_CLASSES = {
    "body": (*(k for k, name in enumerate(CLOTHES_LABELS) if name.lower() in SKIN), SKIN_CLASS),
    "clothes": tuple(k for k, name in enumerate(CLOTHES_LABELS) if name.lower() in CLOTHING),
}
LAYERS = ("person", "body", "clothes", *GARMENT_CLASSES)
# One colour per layer for the hover preview and the layer list (RGB); group layers reuse their members' colours.
PART_COLORS = {
    "person": (150, 150, 150),
    "body": (255, 183, 77),
    "clothes": (66, 135, 245),
    "upper clothes": (66, 135, 245),
    "pants": (52, 168, 83),
    "skirt": (171, 71, 188),
    "dress": (233, 30, 99),
    "belt": (255, 193, 7),
    "scarf": (0, 188, 212),
    "hair": (121, 85, 72),
    "hat": (255, 87, 34),
    "sunglasses": (96, 125, 139),
    "face": (255, 112, 112),
    "arms": (255, 183, 77),
    "legs": (156, 204, 101),
    "shoes": (141, 110, 99),
    "bag": (0, 150, 136),
    "skin": (255, 224, 178),
}
_CLASS_PART = {k: name for name, ids in GARMENT_CLASSES.items() for k in ids}
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)
_sessions: dict[tuple[str, str], object] = {}
_hashed: dict[str, tuple[int, int, str]] = {}  # model path -> (size, mtime_ns, sha256) last hashed
_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class PersonLayers:
    index: int  # 1-based, strongest detection first
    score: float
    box: tuple[int, int, int, int]  # source px x0, y0, x1, y1; every mask below is cropped to it
    person: bytes
    classes: bytes | None = None  # clothes-model class id + 1 inside the person (0 outside), box-sized

    def layer(self, name: str) -> bytes | None:
        """Box-sized 0/255 mask of one layer, or None when it needs the clothes model."""
        if name == "person":
            return self.person
        if self.classes is None:
            return None
        import numpy as np

        ids = GROUP_CLASSES.get(name) or GARMENT_CLASSES[name]
        return (
            (np.isin(np.frombuffer(self.classes, dtype=np.uint8), [k + 1 for k in ids]) * 255)
            .astype(np.uint8)
            .tobytes()
        )


@dataclass(frozen=True, slots=True)
class PersonAnalysis:
    width: int
    height: int
    people: tuple[PersonLayers, ...]
    has_clothes: bool
    faces: int | None = None  # face boxes found by the faces model; None when it did not run


def describe_model(path: Path) -> ModelRef:
    """Bounded read and checksum of a user-selected file; the digest is recorded, not trusted from the name."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ValidationError("Model file could not be read.") from exc
    if not data or len(data) > MAX_MODEL_BYTES:
        raise ValidationError("Model file is empty or larger than 256 MiB.")
    return ModelRef(str(Path(path).resolve()), hashlib.sha256(data).hexdigest())


def classify_models(paths: list[Path]) -> tuple[ModelRef | None, ModelRef | None, ModelRef | None]:
    """Sort chosen files into (segmentation, clothes, faces) by their ONNX contract, so the pick order never matters."""
    segmentation = clothes = faces = None
    for path in paths:
        ref = describe_model(path)
        for kind, builder in (
            ("segmentation", _segmentation_session),
            ("clothes", _clothes_session),
            ("faces", _faces_session),
        ):
            try:
                _verified_session(ref, builder)
            except ValidationError:
                continue
            if kind == "segmentation" and segmentation is None:
                segmentation = ref
            elif kind == "clothes" and clothes is None:
                clothes = ref
            elif kind == "faces" and faces is None:
                faces = ref
            break
        else:
            raise ValidationError(
                f"{Path(path).name} is not a YOLO26 end2end segmentation model, a segformer_b2_clothes model "
                "or a 320 px face-detection model (Ultralytics detect export with FACE_* classes)."
            )
    return segmentation, clothes, faces


def model_read_path(stored: str) -> Path:
    """Where to read a saved model in this environment; the stored path and SHA-256 stay unchanged.

    A Windows path saved by the Windows build is read through a verified WSL drvfs alias. A WSL/Linux path
    cannot be mapped back on Windows. The checksum still rejects any different file.
    """
    if os.name == "nt" and stored.startswith("/"):
        raise ValidationError(
            "A selected model was chosen under WSL or Linux and cannot be opened from Windows; "
            "choose it again with Models…."
        )
    try:
        return Path(ArtifactReadPaths.discover().resolve(stored))
    except OSError as exc:
        raise ValidationError(
            "A selected model was chosen on Windows and is not reachable from this system; "
            "choose it again with Models…."
        ) from exc


def _read_model(stored: str) -> bytes:
    path = model_read_path(stored)
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        reason = "was not found on this system"
        error = exc
    except PermissionError as exc:
        reason = "cannot be read (permission denied)"
        error = exc
    except OSError as exc:
        reason = "could not be read"
        error = exc
    diagnostic_event(
        log,
        "images.person.read_failed",
        level=logging.WARNING,
        data={
            "reason": type(error).__name__,
        },
    )
    raise ValidationError(f"A selected model file {reason}; choose it again with Models….") from error


def model_issue(models: PersonModels) -> str | None:
    """Worker-only regular-file/read-access check; full SHA validation stays at loading."""
    for role, ref in (("segmentation", models.segmentation), ("clothes", models.clothes), ("faces", models.faces)):
        if ref is None:
            continue
        try:
            path = model_read_path(ref.path)
            if not stat.S_ISREG(path.stat().st_mode):
                diagnostic_event(
                    log,
                    "images.person.readiness_refused",
                    level=logging.WARNING,
                    data={
                        "model": role,
                        "reason": "not_regular",
                    },
                )
                return f"The saved {role} model is not a regular file; choose it again with Models…."
            with path.open("rb") as stream:
                stream.read(1)  # Check access, not model validity; never load the full model here.
        except ValidationError as exc:
            diagnostic_event(
                log,
                "images.person.readiness_refused",
                level=logging.WARNING,
                data={
                    "model": role,
                    "reason": "unreachable",
                },
            )
            return str(exc)
        except FileNotFoundError:
            diagnostic_event(
                log,
                "images.person.readiness_refused",
                level=logging.WARNING,
                data={
                    "model": role,
                    "reason": "not_found",
                },
            )
            return f"The saved {role} model was not found on this system; choose it again with Models…."
        except PermissionError:
            diagnostic_event(
                log,
                "images.person.readiness_refused",
                level=logging.WARNING,
                data={
                    "model": role,
                    "reason": "permission_denied",
                },
            )
            return f"The saved {role} model cannot be read (permission denied); choose it again with Models…."
        except OSError as exc:
            diagnostic_event(
                log,
                "images.person.readiness_failed",
                level=logging.WARNING,
                data={
                    "model": role,
                    "reason": type(exc).__name__,
                },
            )
            return f"The saved {role} model could not be read; choose it again with Models…."
    return None


def model_issues(models: PersonModels) -> dict[str, str | None]:
    """Check each configured model independently, without creating ONNX sessions."""
    return {
        role: model_issue(PersonModels(**{role: ref}))
        for role, ref in (
            ("segmentation", models.segmentation),
            ("clothes", models.clothes),
            ("faces", models.faces),
        )
        if ref is not None
    }


def _stamp(path) -> tuple[int, int] | None:
    try:
        info = os.stat(model_read_path(path))
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def verify_model_hashes(models: PersonModels, roles) -> None:
    """Hash the configured files for ``roles`` against their recorded SHA-256 before any work that depends on them
    (a workflow checks this before its first, possibly billable, submission). Unchanged files hashed before (same size
    and modification time) are not read again. Raises ValidationError naming the role."""
    for role in roles:
        ref = getattr(models, role)
        if ref is None:
            raise ValidationError(f"The {role} model is not chosen.")
        stamp = _stamp(ref.path)
        with _lock:
            if stamp is not None and _hashed.get(str(ref.path)) == (*stamp, ref.sha256):
                continue
        data = _read_model(ref.path)
        if len(data) > MAX_MODEL_BYTES or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValidationError(f"The {role} model file changed since it was chosen; download or choose it again.")
        with _lock:
            if stamp is not None and _stamp(ref.path) == stamp:
                _hashed[str(ref.path)] = (*stamp, ref.sha256)


def _verified_session(ref: ModelRef, builder):
    key = (ref.sha256, builder.__name__)
    stamp = _stamp(ref.path)
    with _lock:
        # A file already hashed with this size and modification time is not re-read for every image (workflow runs
        # detect once per branch); any change to the file makes it read and hashed again.
        if key in _sessions and stamp is not None and _hashed.get(str(ref.path)) == (*stamp, ref.sha256):
            return _sessions[key]
    data = _read_model(ref.path)
    if len(data) > MAX_MODEL_BYTES or hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ValidationError("A selected model file changed since it was chosen; choose it again.")
    with _lock:
        if stamp is not None and _stamp(ref.path) == stamp:
            _hashed[str(ref.path)] = (*stamp, ref.sha256)
        if key not in _sessions:
            _sessions[key] = builder(data)
        return _sessions[key]


def _options():
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return options


FLOATS = ("tensor(float)", "tensor(float16)")


def _run(session, tensor):
    """Run with the model's own float width (float32 or float16 exports); results come back as float32."""
    import numpy as np

    dtype = np.float16 if session.get_inputs()[0].type == "tensor(float16)" else np.float32
    outputs = session.run(None, {session.get_inputs()[0].name: tensor.astype(dtype, copy=False)})
    return [np.asarray(item, dtype=np.float32) for item in outputs]


def _describe(items) -> str:
    return ", ".join(f"{item.type} {item.shape}" for item in items) or "no tensors"


def _cpu_session(data: bytes):
    import onnxruntime as ort

    session = ort.InferenceSession(data, sess_options=_options(), providers=["CPUExecutionProvider"])
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise ValidationError("Model sessions must run on the CPU only.")
    return session


def _segmentation_session(data: bytes):
    session = _cpu_session(data)
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or inputs[0].shape != [1, 3, SIDE, SIDE] or inputs[0].type not in FLOATS:
        raise ValidationError(
            "The segmentation model must be a YOLO26 end2end segment export with one float32 or float16 [1,3,640,640] input; "
            f"this file has {_describe(inputs)}. Check that this is the "
            "640x640 YOLO26 end2end segment export."
        )
    if len(outputs) != 2 or outputs[0].shape != [1, 300, 38] or outputs[1].shape != [1, 32, 160, 160]:
        raise ValidationError(
            f"The segmentation model must output [1,300,38] and [1,32,160,160]; this file has {_describe(outputs)}."
        )
    meta = session.get_modelmeta().custom_metadata_map
    if meta.get("task") != "segment" or meta.get("end2end", "").lower() != "true":
        raise ValidationError("Segmentation model must be a YOLO26 end2end segment export.")
    try:
        names = ast.literal_eval(meta.get("names", "{}"))
    except (ValueError, SyntaxError):
        names = {}
    if not isinstance(names, dict) or names.get(0) != "person":
        raise ValidationError("Segmentation model must use COCO class 0 = person.")
    return session


def _clothes_session(data: bytes):
    session = _cpu_session(data)
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or len(inputs[0].shape) != 4 or inputs[0].type not in FLOATS:
        raise ValidationError(
            f"The clothes model must have one float32 or float16 NCHW RGB input; this file has {_describe(inputs)}. "
            "Use segformer_b2_clothes.onnx (full precision) as the second file."
        )
    shape = outputs[0].shape if len(outputs) == 1 else ()
    if len(shape) != 4 or outputs[0].type not in FLOATS or (isinstance(shape[1], int) and shape[1] != 18):
        raise ValidationError("Clothes model needs one float32 [N,18,h,w] logits output (segformer_b2_clothes).")
    return session


def _faces_session(data: bytes):
    session = _cpu_session(data)
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or len(inputs[0].shape) != 4 or inputs[0].type not in FLOATS:
        raise ValidationError(f"The faces model must have one float NCHW RGB input; this file has {_describe(inputs)}.")
    if any(isinstance(side, int) and side != FACES_SIDE for side in inputs[0].shape[2:]):
        raise ValidationError("The faces model must take 320x320 input.")
    meta = session.get_modelmeta().custom_metadata_map
    try:
        names = ast.literal_eval(meta.get("names", "{}"))
    except (ValueError, SyntaxError):
        names = {}
    if meta.get("task") != "detect" or not isinstance(names, dict) or sorted(names) != list(range(len(names))):
        raise ValidationError("The faces model must be an Ultralytics detect export with contiguous class names.")
    shape = outputs[0].shape if len(outputs) == 1 else ()
    if len(shape) != 3 or outputs[0].type not in FLOATS or shape[1] != 4 + len(names):
        raise ValidationError(f"The faces model must output one [1,{4 + len(names)},N] tensor.")
    face_ids = tuple(int(k) for k, v in names.items() if str(v).startswith("FACE_"))
    if not face_ids:
        raise ValidationError("The faces model has no FACE_* classes.")
    session.dtflow_face_ids = face_ids  # type: ignore[attr-defined]
    return session


def _nms(boxes, scores, iou_threshold):
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
        w = np.clip(np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]), 0, None)
        h = np.clip(np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]), 0, None)
        inter = w * h
        order = rest[inter / (areas[i] + areas[rest] - inter + 1e-9) <= iou_threshold]
    return kept


def _face_boxes(session, image: Image.Image):
    """Source-pixel boxes of the FACE_* detections (NudeNet-style: black bottom/right square padding, 320 px)."""
    import numpy as np

    width, height = image.size
    side = max(width, height)
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(image, (0, 0))
    small = canvas.resize((FACES_SIDE, FACES_SIDE), Image.Resampling.BILINEAR)
    tensor = np.ascontiguousarray(np.asarray(small, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0)
    output = _run(session, tensor)[0]
    if not np.isfinite(output).all():
        raise ValidationError("Faces model returned non-finite values.")
    rows = output[0].T
    scores = rows[:, 4:].copy()
    face_ids = list(session.dtflow_face_ids)
    others = [k for k in range(scores.shape[1]) if k not in face_ids]
    scores[:, others] = -1.0  # other parts can neither win an anchor nor suppress a face
    best = scores.max(axis=1)
    keep = best >= FACES_CONFIDENCE
    rows, best = rows[keep], best[keep]
    scale = side / FACES_SIDE
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
    boxes, best = boxes[valid], best[valid]
    return [tuple(float(v) for v in boxes[i]) for i in _nms(boxes, best, FACES_IOU)]


def _cap_face(classes, box, faces):
    """Relabel Face pixels far from every detected face as Skin: the clothes model paints neck and chest as Face."""
    import numpy as np

    x0, y0, x1, y1 = box
    shaped = np.frombuffer(classes, dtype=np.uint8).reshape(y1 - y0, x1 - x0).copy()
    allowed = np.zeros(shaped.shape, bool)
    for fx0, fy0, fx1, fy1 in faces:
        w, h = fx1 - fx0, fy1 - fy0
        left, right = max(0, int(fx0 - 0.5 * w) - x0), min(shaped.shape[1], int(fx1 + 0.5 * w) + 1 - x0)
        top, bottom = max(0, int(fy0 - h) - y0), min(shaped.shape[0], int(fy1 + FACE_BELOW * h) + 1 - y0)
        if right > left and bottom > top:
            allowed[top:bottom, left:right] = True
    shaped[(shaped == 11 + 1) & ~allowed] = SKIN_CLASS + 1
    return shaped.tobytes()


def _letterbox(image: Image.Image):
    import numpy as np

    width, height = image.size
    gain = min(SIDE / width, SIDE / height)
    resized = (max(1, round(width * gain)), max(1, round(height * gain)))
    left = round((SIDE - resized[0]) / 2 - 0.1)
    top = round((SIDE - resized[1]) / 2 - 0.1)
    canvas = Image.new("RGB", (SIDE, SIDE), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    tensor = np.ascontiguousarray(np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0)
    return tensor, (left, top, resized[0], resized[1])


def _instances(session, image: Image.Image, threshold: float, *, mask_threshold: float = 0.5):
    import numpy as np

    if not math.isfinite(mask_threshold) or not 0 < mask_threshold < 1:
        raise ValidationError("Person mask threshold must be finite and in (0, 1).")
    mask_logit = math.log(mask_threshold) - math.log1p(-mask_threshold)
    tensor, (left, top, rw, rh) = _letterbox(image)
    detections, prototypes = _run(session, tensor)
    if not np.isfinite(detections).all() or not np.isfinite(prototypes).all():
        raise ValidationError("Segmentation model returned non-finite values.")
    rows = detections[0]
    people = rows[(rows[:, 5] == 0) & (rows[:, 4] >= threshold)]
    people = people[np.argsort(-people[:, 4])][:MAX_PEOPLE]
    proto = prototypes[0].reshape(32, -1)
    yy, xx = np.ogrid[:SIDE, :SIDE]
    width, height = image.size
    found = []
    for row in people:
        x0, y0, x1, y1 = np.clip(row[:4], 0, SIDE)
        if x1 <= x0 or y1 <= y0:
            continue
        logits = (row[6:] @ proto).reshape(160, 160)
        full = np.asarray(Image.fromarray(logits).resize((SIDE, SIDE), Image.Resampling.BILINEAR)) > mask_logit
        full &= (
            (xx >= max(0.0, x0 - BOX_MARGIN))
            & (xx < min(float(SIDE), x1 + BOX_MARGIN))
            & (yy >= max(0.0, y0 - BOX_MARGIN))
            & (yy < min(float(SIDE), y1 + BOX_MARGIN))
        )
        cropped = full[top : top + rh, left : left + rw].astype(np.uint8) * 255
        mask = np.asarray(Image.fromarray(cropped).resize((width, height), Image.Resampling.NEAREST))
        ys, xs = np.nonzero(mask)
        if not len(xs):
            continue
        found.append((float(row[4]), mask, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)))
    return found


def _clothes_layers(session, image: Image.Image, mask, box):
    import numpy as np

    width, height = image.size
    x0, y0, x1, y1 = box
    mx, my = int((x1 - x0) * 0.08), int((y1 - y0) * 0.08)
    cx0, cy0, cx1, cy1 = max(0, x0 - mx), max(0, y0 - my), min(width, x1 + mx), min(height, y1 + my)
    crop = image.crop((cx0, cy0, cx1, cy1))
    scale = CLOTHES_SIZE / max(crop.size)
    target = (max(32, round(crop.width * scale / 32) * 32), max(32, round(crop.height * scale / 32) * 32))
    pixels = np.asarray(crop.resize(target, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    tensor = np.ascontiguousarray(
        ((pixels - np.asarray(_MEAN, dtype=np.float32)) / np.asarray(_STD, dtype=np.float32)).transpose(2, 0, 1)[None],
        dtype=np.float32,
    )
    logits = _run(session, tensor)[0]
    if not np.isfinite(logits).all():
        raise ValidationError("Clothes model returned non-finite values.")
    scores = logits[0]
    if scores.shape[0] != len(CLOTHES_LABELS):
        raise ValidationError("Clothes model must output the 18 segformer_b2_clothes classes.")
    best = np.full((cy1 - cy0, cx1 - cx0), -np.inf, dtype=np.float32)
    argmax = np.zeros(best.shape, dtype=np.uint8)
    for k in range(scores.shape[0]):
        up = np.asarray(
            Image.fromarray(scores[k].astype(np.float32)).resize((cx1 - cx0, cy1 - cy0), Image.Resampling.BILINEAR)
        )
        better = up > best
        best[better] = up[better]
        argmax[better] = k
    inside = mask[cy0:cy1, cx0:cx1] > 0
    classes = np.zeros(mask.shape, dtype=np.uint8)
    classes[cy0:cy1, cx0:cx1][inside] = argmax[inside] + 1
    return classes[y0:y1, x0:x1].tobytes()


def analyze_face_geometry(
    source: ImageContent,
    faces: ModelRef,
    segmentation: ModelRef | None = None,
    *,
    on_stage: Callable[[str], None] | None = None,
):
    """Detect NudeNet face boxes, optionally with YOLO person masks; never load SegFormer.

    Face boxes are ordered by NudeNet confidence (the existing NMS order). Person instances
    retain the same source-sized silhouettes and score ordering used by analyze_people.
    Run this CPU work on a worker, not the UI thread.
    """
    stage = on_stage or (lambda _name: None)
    image = Image.open(io.BytesIO(source.encoded)).convert("RGB")
    stage("Loading model")
    face_session = _verified_session(faces, _faces_session)
    stage("Detecting faces")
    boxes = _face_boxes(face_session, image)
    if segmentation is None or not boxes:
        return boxes, []
    seg_session = _verified_session(segmentation, _segmentation_session)
    stage("Detecting people")
    return boxes, _instances(seg_session, image, 0.25)


def analyze_people(
    source: ImageContent,
    segmentation: ModelRef,
    clothes: ModelRef | None = None,
    faces: ModelRef | None = None,
    *,
    threshold: float = 0.25,
    on_stage: Callable[[str], None] | None = None,
) -> PersonAnalysis:
    """Find people in the editor's source snapshot; Body/Clothes layers need the optional clothes model.

    ``on_stage`` (called on this worker thread) receives "Loading model", "Detecting people", "Detecting faces" and
    "Analysing clothing" as each really starts."""
    stage = on_stage or (lambda _name: None)
    if not 0 < threshold <= 1:
        raise ValidationError("Confidence must be >0 and <=1.")
    image = Image.open(io.BytesIO(source.encoded)).convert("RGB")
    stage("Loading model")
    seg = _verified_session(segmentation, _segmentation_session)
    clothes_session = _verified_session(clothes, _clothes_session) if clothes is not None else None
    faces_session = (
        _verified_session(faces, _faces_session) if clothes_session is not None and faces is not None else None
    )
    stage("Detecting people")
    instances = _instances(seg, image, threshold)
    face_boxes = None
    if faces_session is not None:
        stage("Detecting faces")
        face_boxes = _face_boxes(faces_session, image)
    if clothes_session is not None and instances:
        stage("Analysing clothing")
    people = []
    for index, (score, mask, box) in enumerate(instances, start=1):
        x0, y0, x1, y1 = box
        classes = _clothes_layers(clothes_session, image, mask, box) if clothes_session is not None else None
        if classes is not None and face_boxes:
            classes = _cap_face(classes, box, face_boxes)
        people.append(PersonLayers(index, score, box, mask[y0:y1, x0:x1].tobytes(), classes))
    return PersonAnalysis(
        source.width,
        source.height,
        tuple(people),
        clothes_session is not None,
        None if face_boxes is None else len(face_boxes),
    )


def person_at(analysis: PersonAnalysis, x: int, y: int) -> PersonLayers | None:
    """The strongest detection whose silhouette contains the source pixel, else None."""
    for person in analysis.people:
        x0, y0, x1, y1 = person.box
        if x0 <= x < x1 and y0 <= y < y1 and person.person[(y - y0) * (x1 - x0) + (x - x0)]:
            return person
    return None


def _allowed_classes(layers: frozenset[str]) -> set[int]:
    allowed: set[int] = set()
    for name in layers:
        allowed.update(GROUP_CLASSES.get(name) or GARMENT_CLASSES.get(name, ()))
    return allowed


def part_at(person: PersonLayers, x: int, y: int, layers: frozenset[str]) -> str | None:
    """The single ticked part under a source pixel ("person" when only the silhouette applies), else None."""
    x0, y0, x1, y1 = person.box
    offset = (y - y0) * (x1 - x0) + (x - x0)
    if not (x0 <= x < x1 and y0 <= y < y1) or not person.person[offset]:
        return None
    if person.classes is not None:
        value = person.classes[offset]
        if value:
            part = _CLASS_PART.get(value - 1)
            if part is not None and (value - 1) in _allowed_classes(layers):
                return part
    return "person" if "person" in layers else None


def colored_preview(person: PersonLayers, layers: frozenset[str], active: str | None = None) -> bytes:
    """Box-sized RGBA preview: each ticked part in its own colour, the ``active`` part (under the pointer) stronger."""
    import numpy as np

    x0, y0, x1, y1 = person.box
    shape = (y1 - y0, x1 - x0)
    rgba = np.zeros((*shape, 4), dtype=np.uint8)
    inside = np.frombuffer(person.person, dtype=np.uint8).reshape(shape) > 0
    if "person" in layers:
        rgba[inside] = (*PART_COLORS["person"], 90)
    if person.classes is not None:
        classes = np.frombuffer(person.classes, dtype=np.uint8).reshape(shape)
        allowed = _allowed_classes(layers)
        for k, name in _CLASS_PART.items():
            if k not in allowed:
                continue
            hit = classes == k + 1
            rgba[hit] = (*PART_COLORS[name], 255 if name == active else 150)
    if active == "person" and "person" in layers:
        plain = inside & (rgba[..., 3] == 90)
        rgba[plain, 3] = 200
    return rgba.tobytes()


def layers_union(person: PersonLayers, layers: frozenset[str]) -> bytes:
    """Box-sized 0/255 union of the chosen layers, for the hover preview; layers the model lacks are skipped."""
    import numpy as np

    total = np.zeros((person.box[3] - person.box[1]) * (person.box[2] - person.box[0]), dtype=np.uint8)
    for name in layers:
        data = person.layer(name)
        if data is not None:
            total |= np.frombuffer(data, dtype=np.uint8)
    return total.tobytes()


def apply_selection(
    current: bytes, analysis: PersonAnalysis, person: PersonLayers, layers: frozenset[str], *, remove: bool
) -> bytes:
    """New binary working mask: ``current`` with the person's chosen layers added (or removed)."""
    import numpy as np

    if not layers or not layers <= set(LAYERS):
        raise ValidationError("Tick at least one layer.")
    mask = np.frombuffer(current, dtype=np.uint8).reshape(analysis.height, analysis.width).copy()
    x0, y0, x1, y1 = person.box
    region = mask[y0:y1, x0:x1]
    for name in layers:
        data = person.layer(name)
        if data is None:
            raise ValidationError("That layer needs the clothes model.")
        selected = np.frombuffer(data, dtype=np.uint8).reshape(y1 - y0, x1 - x0) > 0
        region[selected] = 0 if remove else 255
    return mask.tobytes()

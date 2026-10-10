"""Headless, CPU-only person selection for the Inpaint editor: YOLO26 person instances plus an optional
SegFormer clothes model, from owner-selected local ONNX files (no downloads, no bundled weights, no GPU).

Run every function here on a worker. Suggestions can be wrong; the editor lets the user refine in Paint.
Masks are cropped to each person's source-pixel box to bound memory.
"""

import ast
import hashlib
import io
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from dtflow.core.image_inputs import ImageContent
from dtflow.core.models import ValidationError
from dtflow.persistence.person_models import ModelRef

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
}
GROUP_CLASSES = {
    "body": tuple(k for k, name in enumerate(CLOTHES_LABELS) if name.lower() in SKIN),
    "clothes": tuple(k for k, name in enumerate(CLOTHES_LABELS) if name.lower() in CLOTHING),
}
LAYERS = ("person", "body", "clothes", *GARMENT_CLASSES)
_MEAN = np.array((0.485, 0.456, 0.406), dtype=np.float32)
_STD = np.array((0.229, 0.224, 0.225), dtype=np.float32)
_sessions: dict[tuple[str, str], object] = {}
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


def describe_model(path: Path) -> ModelRef:
    """Bounded read and checksum of a user-selected file; the digest is recorded, not trusted from the name."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ValidationError("Model file could not be read.") from exc
    if not data or len(data) > MAX_MODEL_BYTES:
        raise ValidationError("Model file is empty or larger than 256 MiB.")
    return ModelRef(str(Path(path).resolve()), hashlib.sha256(data).hexdigest())


def classify_models(paths: list[Path]) -> tuple[ModelRef | None, ModelRef | None]:
    """Sort chosen files into (segmentation, clothes) by their ONNX contract, so the pick order never matters."""
    segmentation = clothes = None
    for path in paths:
        ref = describe_model(path)
        for kind, builder in (("segmentation", _segmentation_session), ("clothes", _clothes_session)):
            try:
                _verified_session(ref, builder)
            except ValidationError:
                continue
            if kind == "segmentation" and segmentation is None:
                segmentation = ref
            elif kind == "clothes" and clothes is None:
                clothes = ref
            break
        else:
            raise ValidationError(
                f"{Path(path).name} is neither a YOLO26 end2end segmentation model nor a "
                "segformer_b2_clothes model (both must be full-precision float32 ONNX files)."
            )
    return segmentation, clothes


def _verified_session(ref: ModelRef, builder):
    try:
        data = Path(ref.path).read_bytes()
    except OSError as exc:
        raise ValidationError("A selected model file is missing or unreadable; choose it again.") from exc
    if len(data) > MAX_MODEL_BYTES or hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ValidationError("A selected model file changed since it was chosen; choose it again.")
    key = (ref.sha256, builder.__name__)
    with _lock:
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


def _letterbox(image: Image.Image):
    width, height = image.size
    gain = min(SIDE / width, SIDE / height)
    resized = (max(1, round(width * gain)), max(1, round(height * gain)))
    left = round((SIDE - resized[0]) / 2 - 0.1)
    top = round((SIDE - resized[1]) / 2 - 0.1)
    canvas = Image.new("RGB", (SIDE, SIDE), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (left, top))
    tensor = np.ascontiguousarray(np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0)
    return tensor, (left, top, resized[0], resized[1])


def _instances(session, image: Image.Image, threshold: float):
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
        full = np.asarray(Image.fromarray(logits).resize((SIDE, SIDE), Image.Resampling.BILINEAR)) > 0
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
    width, height = image.size
    x0, y0, x1, y1 = box
    mx, my = int((x1 - x0) * 0.08), int((y1 - y0) * 0.08)
    cx0, cy0, cx1, cy1 = max(0, x0 - mx), max(0, y0 - my), min(width, x1 + mx), min(height, y1 + my)
    crop = image.crop((cx0, cy0, cx1, cy1))
    scale = CLOTHES_SIZE / max(crop.size)
    target = (max(32, round(crop.width * scale / 32) * 32), max(32, round(crop.height * scale / 32) * 32))
    pixels = np.asarray(crop.resize(target, Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    tensor = np.ascontiguousarray(((pixels - _MEAN) / _STD).transpose(2, 0, 1)[None], dtype=np.float32)
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


def analyze_people(
    source: ImageContent,
    segmentation: ModelRef,
    clothes: ModelRef | None = None,
    *,
    threshold: float = 0.25,
) -> PersonAnalysis:
    """Find people in the editor's source snapshot; Body/Clothes layers need the optional clothes model."""
    if not 0 < threshold <= 1:
        raise ValidationError("Confidence must be >0 and <=1.")
    image = Image.open(io.BytesIO(source.encoded)).convert("RGB")
    seg = _verified_session(segmentation, _segmentation_session)
    clothes_session = _verified_session(clothes, _clothes_session) if clothes is not None else None
    people = []
    for index, (score, mask, box) in enumerate(_instances(seg, image, threshold), start=1):
        x0, y0, x1, y1 = box
        classes = _clothes_layers(clothes_session, image, mask, box) if clothes_session is not None else None
        people.append(PersonLayers(index, score, box, mask[y0:y1, x0:x1].tobytes(), classes))
    return PersonAnalysis(source.width, source.height, tuple(people), clothes_session is not None)


def person_at(analysis: PersonAnalysis, x: int, y: int) -> PersonLayers | None:
    """The strongest detection whose silhouette contains the source pixel, else None."""
    for person in analysis.people:
        x0, y0, x1, y1 = person.box
        if x0 <= x < x1 and y0 <= y < y1 and person.person[(y - y0) * (x1 - x0) + (x - x0)]:
            return person
    return None


def layers_union(person: PersonLayers, layers: frozenset[str]) -> bytes:
    """Box-sized 0/255 union of the chosen layers, for the hover preview; layers the model lacks are skipped."""
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

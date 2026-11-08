"""Headless source-sized face masks for workflows.

Fast uses NudeNet boxes only; Balanced clips their ellipses to a YOLO person
silhouette and keeps one filled, face-anchored connected surface per face.
Legacy Precise modes retain their existing SegFormer face/hair implementation.
The downstream Inpaint step still owns mask grow and feather.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from dtflow.core.models import ValidationError
from dtflow.core.workflows import FACE_EXPANSION_DEFAULT, _face_expansion
from dtflow.persistence.person_models import PersonModels
from dtflow.services.image_preparation import load_source_bytes, mask_png
from dtflow.services.person_selection import analyze_face_geometry, analyze_people, apply_selection

MASK_REGIONS = {"face_skin": frozenset({"face"}), "face_skin_and_hair": frozenset({"face", "hair"})}
PERSON_CHOICES = ("strongest", "all")


@dataclass(frozen=True, slots=True)
class FaceMaskResult:
    mask_png: bytes | None  # source-sized 0/255 PNG, None when the branch has no target
    reason: str | None
    people: int
    faces: int


def _face_ellipse(box: tuple[float, float, float, float], size: tuple[int, int], expansion: float) -> Image.Image:
    """Fill an ellipse using the original face box centre and expanded width/height."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) * expansion / 2, (y1 - y0) * expansion / 2
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return mask


def _matching_person(box, people):
    """Prefer a face-centred person silhouette, then maximum face-box overlap.

    Ignore a person whose silhouette does not overlap the *original* face box.
    A low-overlap background person must not win merely because its expanded
    ellipse happens to intersect part of that person.
    """
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    preferred = None
    for score, mask, _person_box in people:
        height, width = mask.shape
        left, top = max(0, int(x0)), max(0, int(y0))
        right, bottom = min(width, int(np.ceil(x1))), min(height, int(np.ceil(y1)))
        if right <= left or bottom <= top:
            continue
        count = int(np.count_nonzero(mask[top:bottom, left:right]))
        area = (right - left) * (bottom - top)
        if count < max(1, int(area * 0.1)):
            continue
        px, py = int(cx), int(cy)
        contains = int(0 <= px < width and 0 <= py < height and mask[py, px] > 0)
        key = (contains, count / area, float(score))
        if preferred is None or key > preferred[0]:
            preferred = (key, mask)
    return None if preferred is None else preferred[1]


def _connected_face_region(candidate: np.ndarray, face_box, crop: tuple[int, int, int, int]) -> np.ndarray:
    """Keep the face-anchored 4-connected region; discard islands and fill enclosed holes.

    Select the nearest intersecting pixel inside the *original* face box when the
    centre is outside the silhouette. If no face-box pixel survives, omit the face.
    Work only in the expanded ellipse's crop, not the entire source image.
    """
    left, top, right, bottom = crop
    fx0, fy0, fx1, fy1 = face_box
    x0, y0 = max(left, int(fx0)), max(top, int(fy0))
    x1, y1 = min(right, int(np.ceil(fx1))), min(bottom, int(np.ceil(fy1)))
    if x1 <= x0 or y1 <= y0:
        return np.zeros(candidate.shape, dtype=bool)
    local = candidate[y0 - top : y1 - top, x0 - left : x1 - left]
    ys, xs = np.nonzero(local)
    if not len(xs):
        return np.zeros(candidate.shape, dtype=bool)
    cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
    closest = int(np.argmin((xs + x0 - cx) ** 2 + (ys + y0 - cy) ** 2))
    seed = (int(xs[closest] + x0 - left), int(ys[closest] + y0 - top))
    flood = Image.fromarray(candidate.astype(np.uint8) * 255, mode="L")
    ImageDraw.floodfill(flood, seed, 128)
    connected = np.asarray(flood) == 128

    # All voids enclosed by the connected surface become filled. Border-connected
    # background stays out. No disconnected foreground island can be reintroduced.
    inverse = ImageOps.expand(Image.fromarray(np.where(connected, 0, 255).astype(np.uint8), mode="L"), 1, 255)
    ImageDraw.floodfill(inverse, (0, 0), 128)
    return np.asarray(inverse)[1:-1, 1:-1] != 128


def _geometric_mask(source, models: PersonModels, *, method: str, person: str, expansion: float, stage):
    """Detect a source-sized geometric mask using only the selected model roles."""
    if models.faces is None or (method == "balanced" and models.segmentation is None):
        needed = "NudeNet faces and YOLO person segmentation" if method == "balanced" else "NudeNet faces"
        raise ValidationError(f"{method.title()} face detection needs {needed}; download the required models.")
    boxes, people = analyze_face_geometry(
        source,
        models.faces,
        models.segmentation if method == "balanced" else None,
        on_stage=stage,
    )
    if not boxes:
        return FaceMaskResult(None, "No face detected", len(people), 0)
    if method == "balanced" and not people:
        return FaceMaskResult(None, "No person detected", 0, len(boxes))
    stage("Preparing masks")
    # Fast's Strongest means the highest-confidence NudeNet face. Balanced's
    # Strongest keeps the highest-scoring YOLO person, as in the precise path.
    selected_boxes = boxes[:1] if method == "fast" and person == "strongest" else boxes
    selected_people = people[:1] if method == "balanced" and person == "strongest" else people
    result = np.zeros((source.height, source.width), dtype=bool)
    for box in selected_boxes:
        ellipse = _face_ellipse(box, (source.width, source.height), expansion)
        crop = ellipse.getbbox()
        if crop is None:
            continue
        x0, y0, x1, y1 = crop
        section = np.asarray(ellipse.crop(crop)) > 0
        if method == "fast":
            region = section
        else:
            target = _matching_person(box, selected_people)
            if target is None:
                continue
            region = _connected_face_region(section & (target[y0:y1, x0:x1] > 0), box, crop)
        result[y0:y1, x0:x1] |= region
    if not result.any():
        return FaceMaskResult(None, "Face region is empty", len(people), len(boxes))
    return FaceMaskResult(mask_png((result.astype(np.uint8) * 255).tobytes(), source.width, source.height),
                          None, len(people), len(boxes))


def detect_face_mask(
    source_png: bytes,
    models: PersonModels,
    *,
    mask_region: str = "face_skin",
    person: str = "strongest",
    method: str | None = None,
    expansion: float = FACE_EXPANSION_DEFAULT,
    on_stage: Callable[[str], None] | None = None,
) -> FaceMaskResult:
    """Blocking CPU analysis; None method preserves the existing precise mask semantics."""
    if mask_region not in MASK_REGIONS:
        raise ValidationError("Mask region must be face_skin or face_skin_and_hair.")
    if person not in PERSON_CHOICES:
        raise ValidationError("Person must be strongest or all.")
    if method not in (None, "fast", "balanced"):
        raise ValidationError("Mask method must be Fast, Balanced or an existing precise region.")
    stage = on_stage or (lambda _name: None)
    source = load_source_bytes(source_png)
    if method is not None:
        factor = _face_expansion(expansion, "Face box expansion")
        return _geometric_mask(source, models, method=method, person=person, expansion=factor, stage=stage)

    if models.segmentation is None or models.clothes is None or models.faces is None:
        raise ValidationError(
            "Precise face detection needs the segmentation, clothes and faces models; download them before running."
        )
    analysis = analyze_people(source, models.segmentation, models.clothes, models.faces, on_stage=stage)
    faces = analysis.faces or 0
    if not analysis.people:
        return FaceMaskResult(None, "No person detected", 0, faces)
    if faces == 0:
        return FaceMaskResult(None, "No face detected", len(analysis.people), 0)
    stage("Preparing masks")
    chosen = analysis.people[:1] if person == "strongest" else analysis.people
    mask = bytes(analysis.width * analysis.height)
    for target in chosen:
        mask = apply_selection(mask, analysis, target, MASK_REGIONS[mask_region], remove=False)
    if not mask.strip(b"\0"):
        return FaceMaskResult(None, "Face region is empty", len(analysis.people), faces)
    return FaceMaskResult(mask_png(mask, analysis.width, analysis.height), None, len(analysis.people), faces)

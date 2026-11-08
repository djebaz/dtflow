"""Headless Fast and Balanced face-mask geometry for workflows (#299): NudeNet boxes drawn as expanded ellipses,
optionally clipped to the matching YOLO person silhouette. SegFormer is never loaded here.

Pillow and NumPy stay in this image-analysis service; workflow orchestration and mask PNG packaging stay in
``face_mask``. Run every function here on a worker.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from PIL import Image, ImageDraw, ImageOps

from dtflow.core.image_inputs import ImageContent
from dtflow.persistence.person_models import ModelRef
from dtflow.services.person_selection import analyze_face_geometry

NO_OVERLAP = "Detected face does not overlap the selected person's silhouette"


def _face_ellipse(box: tuple[float, float, float, float], size: tuple[int, int], expansion: float) -> Image.Image:
    """Fill an ellipse using the original face box centre and expanded width/height."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) * expansion / 2, (y1 - y0) * expansion / 2
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return mask


def _ellipse_section(box, size: tuple[int, int], expansion: float):
    """The face ellipse cropped to its bounding box: ``(crop, boolean section)``, or None when it is empty."""
    ellipse = _face_ellipse(box, size, expansion)
    crop = ellipse.getbbox()
    if crop is None:
        return None
    return crop, np.asarray(ellipse.crop(crop)) > 0


def _matching_person(box, people, ellipse, crop):
    """Match by face pixels first, then by silhouette overlap with the ellipse; returns ``(score, mask)``.

    YOLO may omit pixels in the original NudeNet face box. Requiring a fixed
    face-box coverage or a person-box containment gate discards valid matches.
    Only silhouettes that actually overlap the expanded ellipse are eligible.
    """
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ex0, ey0, ex1, ey1 = crop
    preferred = None
    for score, mask, _person_box in people:
        expanded = mask[ey0:ey1, ex0:ex1] > 0
        overlap = int(np.count_nonzero(expanded & ellipse))
        if not overlap:
            continue
        height, width = mask.shape
        left, top = max(0, int(x0)), max(0, int(y0))
        right, bottom = min(width, int(np.ceil(x1))), min(height, int(np.ceil(y1)))
        count = int(np.count_nonzero(mask[top:bottom, left:right])) if right > left and bottom > top else 0
        area = max(1, (right - left) * (bottom - top))
        px, py = int(cx), int(cy)
        contains = int(0 <= px < width and 0 <= py < height and mask[py, px] > 0)
        key = (contains, count / area, overlap, float(score))
        if preferred is None or key > preferred[0]:
            preferred = (key, mask, float(score))
    return None if preferred is None else (preferred[2], preferred[1])


def _flood(mask: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    """The 4-connected True region of ``mask`` containing ``seed``."""
    # .copy(): Image.fromarray gives a read-only buffer-backed image and ImageDraw.floodfill silently fills
    # nothing on it (every Balanced mask came out empty).
    flood = Image.fromarray(mask.astype(np.uint8) * 255).copy()
    ImageDraw.floodfill(flood, seed, 128)
    return np.asarray(flood) == 128


def _fill_holes(region: np.ndarray) -> np.ndarray:
    """``region`` plus every void it encloses; background connected to the border stays out."""
    inverse = ImageOps.expand(Image.fromarray(np.where(region, 0, 255).astype(np.uint8)).copy(), border=1, fill=255)
    ImageDraw.floodfill(inverse, (0, 0), 128)
    return np.asarray(inverse)[1:-1, 1:-1] != 128


def _connected_face_region(candidate: np.ndarray, face_box, crop: tuple[int, int, int, int]) -> np.ndarray:
    """Keep the face-anchored 4-connected region; discard islands and fill enclosed holes.

    Anchor inside the original face box when possible. If YOLO clipped the
    whole face but retains adjacent silhouette pixels inside the expanded
    ellipse, start from the nearest surviving pixel instead of discarding it.
    Work only in the expanded ellipse's crop, not the entire source image.
    """
    left, top, right, bottom = crop
    fx0, fy0, fx1, fy1 = face_box
    x0, y0 = max(left, int(fx0)), max(top, int(fy0))
    x1, y1 = min(right, int(np.ceil(fx1))), min(bottom, int(np.ceil(fy1)))
    local = candidate[y0 - top : y1 - top, x0 - left : x1 - left]
    ys, xs = np.nonzero(local)
    origin_x, origin_y = x0 - left, y0 - top
    if not len(xs):
        ys, xs = np.nonzero(candidate)
        origin_x = origin_y = 0
    if not len(xs):
        return np.zeros(candidate.shape, dtype=bool)
    cx, cy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
    xs = xs + origin_x
    ys = ys + origin_y
    closest = int(np.argmin((xs + left - cx) ** 2 + (ys + top - cy) ** 2))
    seed = (int(xs[closest]), int(ys[closest]))
    # No disconnected foreground island can be reintroduced by the hole fill.
    return _fill_holes(_flood(candidate, seed))


def _fast_mask(source: ImageContent, boxes, expansion: float) -> np.ndarray:
    """Union of the face ellipses, clipped to the image."""
    result = np.zeros((source.height, source.width), dtype=bool)
    for box in boxes:
        found = _ellipse_section(box, (source.width, source.height), expansion)
        if found is not None:
            (x0, y0, x1, y1), section = found
            result[y0:y1, x0:x1] |= section
    return result


def _balanced_mask(source: ImageContent, boxes, people, person: str, expansion: float) -> np.ndarray:
    """Per-face ellipse clipped to its matching person, face-connected and hole-filled, then unioned.

    Every face is matched against all people first; Strongest then keeps only the faces of the highest-scoring
    person that actually owns a detected face (not a person without one).
    """
    matched = []
    for box in boxes:
        found = _ellipse_section(box, (source.width, source.height), expansion)
        if found is None:
            continue
        crop, section = found
        owner = _matching_person(box, people, section, crop)
        if owner is not None:
            matched.append((owner[0], owner[1], box, section, crop))
    if matched and person == "strongest":
        best = max(item[0] for item in matched)
        matched = [item for item in matched if item[0] == best]
    result = np.zeros((source.height, source.width), dtype=bool)
    for _score, target, box, section, crop in matched:
        x0, y0, x1, y1 = crop
        result[y0:y1, x0:x1] |= _connected_face_region(section & (target[y0:y1, x0:x1] > 0), box, crop)
    return result


def geometric_face_pixels(
    source: ImageContent,
    faces: ModelRef,
    segmentation: ModelRef | None,
    *,
    method: str,
    person: str,
    expansion: float,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[bytes | None, str | None, int, int]:
    """Return ``(0/255 source-sized pixels, reason, people, faces)``; pixels are None with a reason when absent."""
    stage = on_stage or (lambda _name: None)
    boxes, people = analyze_face_geometry(source, faces, segmentation, on_stage=stage)
    if not boxes:
        return None, "No face detected", len(people), 0
    if method == "balanced" and not people:
        return None, "No person detected", 0, len(boxes)
    stage("Preparing masks")
    if method == "fast":
        # Fast's Strongest is the highest-confidence NudeNet face; Balanced uses YOLO person score order.
        result = _fast_mask(source, boxes[:1] if person == "strongest" else boxes, expansion)
        empty = "Face region is empty"
    else:
        result = _balanced_mask(source, boxes, people, person, expansion)
        empty = NO_OVERLAP
    if not result.any():
        return None, empty, len(people), len(boxes)
    return (result.astype(np.uint8) * 255).tobytes(), None, len(people), len(boxes)

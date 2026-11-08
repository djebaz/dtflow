"""Headless source-sized face masks for workflows.

Fast uses NudeNet boxes only; Balanced clips their ellipses to a YOLO person
silhouette and keeps one filled, face-anchored connected surface per face.
Legacy Precise modes retain their existing SegFormer face/hair implementation.
The downstream Inpaint step still owns mask grow and feather.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dtflow.core.models import ValidationError
from dtflow.core.workflows import (
    FACE_EXPANSION_DEFAULT,
    GEOMETRIC_FACE_METHODS,
    PERSON_CHOICES,
    validate_face_expansion,
)
from dtflow.persistence.person_models import PersonModels
from dtflow.services.image_preparation import load_source_bytes, mask_png
from dtflow.services.person_selection import analyze_people, apply_selection

MASK_REGION_LAYERS = {"face_skin": frozenset({"face"}), "face_skin_and_hair": frozenset({"face", "hair"})}


@dataclass(frozen=True, slots=True)
class FaceMaskResult:
    mask_png: bytes | None  # source-sized 0/255 PNG, None when the branch has no target
    reason: str | None
    people: int
    faces: int


def _geometric_mask(source, models: PersonModels, *, method: str, person: str, expansion: float, stage):
    """Build the selected geometric mask from the shared CPU image-analysis service."""
    from dtflow.services.face_geometry import geometric_face_pixels  # lazy: keeps NumPy off the startup path

    # Defensive only: the run's model preflight already requires these roles before any submission.
    balanced = method == "balanced"
    if models.faces is None or (balanced and models.segmentation is None):
        needed = "NudeNet faces and YOLO person segmentation" if balanced else "NudeNet faces"
        raise ValidationError(f"{method.title()} face detection needs {needed}; download the required models.")
    pixels, reason, people, faces = geometric_face_pixels(
        source,
        models.faces,
        models.segmentation if balanced else None,
        method=method,
        person=person,
        expansion=expansion,
        on_stage=stage,
    )
    if pixels is None:
        return FaceMaskResult(None, reason, people, faces)
    return FaceMaskResult(mask_png(pixels, source.width, source.height), None, people, faces)


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
    if mask_region not in MASK_REGION_LAYERS:
        raise ValidationError("Mask region must be face_skin or face_skin_and_hair.")
    if person not in PERSON_CHOICES:
        raise ValidationError("Person must be strongest or all.")
    if method is not None and method not in GEOMETRIC_FACE_METHODS:
        raise ValidationError("Mask method must be Fast, Balanced or an existing precise region.")
    stage = on_stage or (lambda _name: None)
    source = load_source_bytes(source_png)
    if method is not None:
        factor = validate_face_expansion(expansion, "Face box expansion")
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
        mask = apply_selection(mask, analysis, target, MASK_REGION_LAYERS[mask_region], remove=False)
    if not mask.strip(b"\0"):
        return FaceMaskResult(None, "Face region is empty", len(analysis.people), faces)
    return FaceMaskResult(mask_png(mask, analysis.width, analysis.height), None, len(analysis.people), faces)

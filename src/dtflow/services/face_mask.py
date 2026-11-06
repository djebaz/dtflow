"""Headless face mask for workflows and (later) the #117 script hooks: one function over ``analyze_people``.

D4: segmentation, clothes (SegFormer) and faces (NudeNet 320n) models are all required. No person, zero face boxes or
an empty chosen-region mask each return a "no target" result (the workflow pauses that branch). D5: ``person`` is
``strongest`` (highest YOLO score) or ``all`` (union of every person's region). The mask is source-sized 0/255 PNG,
ready for ``prepare_image_bytes(mask_bytes=...)``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dtflow.core.models import ValidationError
from dtflow.persistence.person_models import PersonModels
from dtflow.services.image_preparation import load_source_bytes, mask_png
from dtflow.services.person_selection import analyze_people, apply_selection

MASK_REGIONS = {"face_skin": frozenset({"face"}), "face_skin_and_hair": frozenset({"face", "hair"})}
PERSON_CHOICES = ("strongest", "all")


@dataclass(frozen=True, slots=True)
class FaceMaskResult:
    mask_png: bytes | None  # source-sized 0/255 grayscale PNG; None when there is no target
    reason: str | None  # None on success, else "No person detected" | "No face detected" | "Face region is empty"
    people: int  # people found by segmentation
    faces: int  # face boxes found by the faces model


def detect_face_mask(
    source_png: bytes,
    models: PersonModels,
    *,
    mask_region: str = "face_skin",
    person: str = "strongest",
    on_stage: Callable[[str], None] | None = None,
) -> FaceMaskResult:
    """Blocking CPU work (call from a worker). ``on_stage`` receives "Loading model", "Detecting people",
    "Analysing clothing", "Detecting faces", "Preparing masks" as each really starts. Raises ``ValidationError``
    when a required model is missing or fails its check."""
    layers = MASK_REGIONS.get(mask_region)
    if layers is None:
        raise ValidationError("Mask region must be face_skin or face_skin_and_hair.")
    if person not in PERSON_CHOICES:
        raise ValidationError("Person must be strongest or all.")
    if models.segmentation is None or models.clothes is None or models.faces is None:
        raise ValidationError(
            "Face detection needs the segmentation, clothes and faces models; download them before running."
        )
    stage = on_stage or (lambda _name: None)
    source = load_source_bytes(source_png)
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
        mask = apply_selection(mask, analysis, target, layers, remove=False)
    if not mask.strip(b"\0"):
        return FaceMaskResult(None, "Face region is empty", len(analysis.people), faces)
    return FaceMaskResult(mask_png(mask, analysis.width, analysis.height), None, len(analysis.people), faces)

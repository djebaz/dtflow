"""Headless face mask for workflows and (later) the #117 script hooks: one function over ``analyze_people``.

SKELETON (interface fixed by the #223 integration plan; the body is implemented in the models PR).

D4: segmentation, clothes (SegFormer) and faces (NudeNet 320n) models are all required. No person, zero face boxes or
an empty chosen-region mask each return a "no target" result (the workflow pauses that branch). D5: ``person`` is
``strongest`` (highest YOLO score) or ``all`` (union of every person's region). The mask is source-sized 0/255 PNG,
ready for ``prepare_image_bytes(mask_bytes=...)``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dtflow.persistence.person_models import PersonModels


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
    raise NotImplementedError

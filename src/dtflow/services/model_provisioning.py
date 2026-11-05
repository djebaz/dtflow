"""Shared detection-model catalogue and verified downloads for Generate and Workflows (#223, D7/D8).

SKELETON (interface fixed by the #223 integration plan; bodies are implemented in the models PR).

- The read-only catalogue is ``resources/catalogue/detection_models.json``: role, display name, pinned publisher URL,
  SHA-256, approximate size, licence name/URL and source page. DTFlow never redistributes weights (D7).
- Downloads run only on an explicit user request (one preflight confirmation listing each licence), off the Qt thread,
  into ``<state>/models/person/<sha256>.onnx``; each file is SHA-256 checked, contract-checked with
  ``person_selection.classify_models`` and recorded as an ordinary ``ModelRef`` in ``person_models.json``
  (schema 1, no bump). Existing user-selected models are never replaced for a role that is already usable.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from dtflow.persistence.person_models import PersonModels

ROLES = ("segmentation", "clothes", "faces")
WORKFLOW_FACE_ROLES = ROLES  # DTFDetectFaceMask needs all three (D4)


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    role: str  # segmentation | clothes | faces
    name: str  # display name, e.g. "YOLO26n person segmentation"
    url: str  # pinned publisher URL
    sha256: str
    size_mib: float  # approximate, for display; the transfer total comes from the response
    licence: str  # licence name as shown to the user
    licence_url: str
    source_url: str  # publisher page


@dataclass(frozen=True, slots=True)
class ProvisionProgress:
    """One truthful progress report: ``stage`` is a short label such as "Downloading YOLO26n person segmentation",
    "Verifying download" or "Checking model contract"; ``total`` is None when the size is unknown."""

    role: str
    stage: str
    done: int = 0
    total: int | None = None


def catalogue() -> tuple[CatalogueEntry, ...]:
    """The pinned catalogue, in ``ROLES`` order."""
    raise NotImplementedError


def missing_models(state: Path, roles: Iterable[str] = ROLES) -> tuple[CatalogueEntry, ...]:
    """Catalogue entries for the requested roles whose configured model is absent or unreadable (``model_issues``).

    Reads local files only; never downloads or contacts the network."""
    raise NotImplementedError


def download_models(
    state: Path,
    entries: Iterable[CatalogueEntry],
    *,
    on_progress: Callable[[ProvisionProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> PersonModels:
    """Download, verify (SHA-256 + ONNX contract) and record each entry; returns the updated ``PersonModels``.

    Blocking: call from a worker thread. Raises ``ValidationError`` with an actionable reason on network, hash or
    contract failure; a partial file is removed. ``cancel`` stops between chunks."""
    raise NotImplementedError

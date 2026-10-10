"""Owner-selected local person-selection model files (paths and SHA-256) in ``<state>/person_models.json``.

No weights are bundled or downloaded. The file is credential-free, versioned independently of the application and
written atomically; a newer schema is rejected rather than rewritten.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from dtflow.core.models import ValidationError

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ModelRef:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PersonModels:
    segmentation: ModelRef | None = None
    clothes: ModelRef | None = None
    faces: ModelRef | None = None


def _ref(value: object) -> ModelRef | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("path"), str)
        or not isinstance(value.get("sha256"), str)
    ):
        raise ValidationError("Person model settings are malformed.")
    digest = value["sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValidationError("Person model settings contain an invalid SHA-256.")
    return ModelRef(value["path"], digest)


def load_person_models(state: Path) -> PersonModels:
    path = Path(state) / "person_models.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return PersonModels()
    except (OSError, ValueError) as exc:
        raise ValidationError("Person model settings could not be read.") from exc
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int:
        raise ValidationError("Person model settings are malformed.")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("Unsupported person model settings schema; the file was left unchanged.")
    return PersonModels(_ref(document.get("segmentation")), _ref(document.get("clothes")), _ref(document.get("faces")))


def save_person_models(state: Path, models: PersonModels) -> Path:
    load_person_models(state)  # refuse to overwrite an unreadable or newer file
    target = Path(state) / "person_models.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    document = {"schema_version": SCHEMA_VERSION}
    for name, ref in (("segmentation", models.segmentation), ("clothes", models.clothes), ("faces", models.faces)):
        document[name] = None if ref is None else {"path": ref.path, "sha256": ref.sha256}
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent, prefix=".models-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target

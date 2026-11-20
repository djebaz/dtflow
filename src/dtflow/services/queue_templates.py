"""Local queue authoring recipes. No provider, planner execution, queue creation or receipt authority."""

import base64
import hashlib
from dataclasses import asdict, dataclass, fields, replace

from dtflow.core.image_inputs import ImageContent
from dtflow.core.models import ResolvedGenerationRequest, ValidationError, integer
from dtflow.core.pose import PoseControl
from dtflow.core.queue_authoring import PlanForm, queue_edit_from_source
from dtflow.core.request_records import request_from_record
from dtflow.persistence import queue_templates as storage


MAX_POSE_MAP_BYTES = 128 * 1024


@dataclass(frozen=True, slots=True)
class TemplatePose:
    """Template-owned body map and settings, never an attempt reference or extraction history."""

    content: ImageContent
    control: PoseControl

    def __post_init__(self):
        if not isinstance(self.content, ImageContent) or self.content.channels != 3:
            raise ValidationError("A pose template requires an RGB body map.")
        if len(self.content.encoded) > MAX_POSE_MAP_BYTES:
            raise ValidationError("The body map exceeds the pose template's 128 KiB PNG limit; use the queue instead.")
        if hashlib.sha256(self.content.encoded).hexdigest() != self.content.sha256:
            raise ValidationError("The template body map failed its content identity check.")
        if not isinstance(self.control, PoseControl) or self.control.evidence:
            raise ValidationError("Pose templates retain control settings, not execution or extraction evidence.")


def _pose_record(pose: TemplatePose) -> dict:
    if not isinstance(pose, TemplatePose):
        raise ValidationError("Invalid template pose.")
    return {
        "map": {
            "png": base64.b64encode(pose.content.encoded).decode("ascii"),
            "sha256": pose.content.sha256,
            "width": pose.content.width,
            "height": pose.content.height,
            "channels": pose.content.channels,
        },
        "control": asdict(pose.control),
    }


def _pose_from_record(record: dict) -> TemplatePose:
    if not isinstance(record, dict) or set(record) != {"map", "control"}:
        raise ValidationError("Template pose is missing or unsupported.")
    content, control = record["map"], record["control"]
    if not isinstance(content, dict) or set(content) != {"png", "sha256", "width", "height", "channels"}:
        raise ValidationError("Template body map fields are incomplete or unsupported.")
    encoded = content["png"]
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_POSE_MAP_BYTES + 2) // 3):
        raise ValidationError("Template body map exceeds its encoded byte limit.")
    if not isinstance(control, dict) or set(control) != {field.name for field in fields(PoseControl)}:
        raise ValidationError("Template pose control fields are incomplete or unsupported.")
    return TemplatePose(
        ImageContent(
            base64.b64decode(encoded, validate=True),
            content["sha256"],
            content["width"],
            content["height"],
            content["channels"],
        ),
        PoseControl(**{**control, "evidence": tuple(tuple(pair) for pair in control["evidence"])}),
    )


@dataclass(frozen=True, slots=True)
class Recipe:
    request: ResolvedGenerationRequest
    form: PlanForm
    count: int
    backend: str
    reconstructed: bool = False
    pose: TemplatePose | None = None


@dataclass(frozen=True, slots=True)
class QueueTemplate:
    identity: str
    name: str
    revision: int
    recipe: Recipe


def recipe_record(recipe: Recipe) -> dict:
    if not isinstance(recipe, Recipe) or not isinstance(recipe.request, ResolvedGenerationRequest):
        raise ValidationError("Invalid template recipe.")
    if (
        recipe.request.operation != "txt2img"
        or recipe.request.image_inputs is not None
        or recipe.request.pose_control is not None
    ):
        raise ValidationError("Template bases cannot retain image inputs or execution pose references.")
    base = asdict(replace(recipe.request, prepared_assets=(), resolution_evidence=()))
    for name in ("request_id", "prepared_assets", "resolution_evidence"):
        base.pop(name)
    record = {
        "version": 2 if recipe.pose is not None else 1,
        "backend": recipe.backend,
        "reconstructed": recipe.reconstructed,
        "source": {"version": 1, "base": base, "form": asdict(recipe.form), "count": recipe.count},
    }
    if recipe.pose is not None:
        record["pose"] = _pose_record(recipe.pose)
    recipe_from_record(record)
    return record


def recipe_from_record(record: dict) -> Recipe:
    try:
        # A recipe saved by an older version also carries a "cap"; queues have no maximum of their own now (#492), so
        # it is accepted and ignored.
        if not isinstance(record, dict):
            raise ValidationError("Unsupported template fields.")
        version = record.get("version")
        expected = {"version", "backend", "source", "reconstructed"}
        if version == 2:
            expected.add("pose")
        if set(record) - {"cap"} != expected:
            raise ValidationError("Unsupported template fields.")
        if type(version) is not int or version not in (1, 2):
            raise ValidationError("Unsupported template recipe version.")
        if record["backend"] not in ("fake", "local", "cloud"):
            raise ValidationError("Unsupported template backend requirement.")
        if type(record["reconstructed"]) is not bool:
            raise ValidationError("Invalid template reconstruction evidence.")
        pose = _pose_from_record(record["pose"]) if version == 2 else None
        if pose is not None and record["backend"] != "cloud":
            raise ValidationError("A pose template requires DT+ Cloud; no backend was changed.")
        source = record["source"]
        if not isinstance(source, dict) or set(source) != {"version", "base", "form", "count"}:
            raise ValidationError("Unsupported template authoring fields.")
        base = source["base"]
        base_fields = {field.name for field in fields(ResolvedGenerationRequest)} - {
            "request_id",
            "prepared_assets",
            "resolution_evidence",
        }
        if not isinstance(base, dict) or set(base) != base_fields:
            raise ValidationError("Template base fields are incomplete or contain unsupported evidence.")
        if base.get("image_inputs") is not None or base.get("pose_control") is not None:
            raise ValidationError("Template bases cannot retain image inputs or execution pose references.")
        if not isinstance(source["form"], dict) or set(source["form"]) != set(asdict(PlanForm())):
            raise ValidationError("Template form fields are incomplete or unsupported.")
        edited = queue_edit_from_source(
            {
                **source,
                "base": {**base, "request_id": "template-draft", "resolution_evidence": ()},
            }
        )
        # All spin/combo values must be valid before any widget can silently clamp them.
        if edited.form.strengths or any(name == "strength" and count for name, count in edited.form.draws):
            raise ValidationError("Text-to-image templates cannot contain image-operation strengths.")
        if edited.form.uses_randomness:
            try:
                integer(int(edited.form.rng_seed), 0, 2**32 - 1, "Template plan seed")
            except ValueError as exc:
                raise ValidationError(
                    "Set an explicit valid plan seed before saving a random recipe; loading never rerolls."
                ) from exc
        return Recipe(
            request_from_record(edited.base),
            edited.form,
            edited.count,
            record["backend"],
            record["reconstructed"],
            pose,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("Template recipe is invalid or unsupported; draft and library left unchanged.") from exc


def _template(row):
    return QueueTemplate(row["identity"], row["name"], row["revision"], recipe_from_record(row["recipe"]))


def list_templates(state) -> tuple[QueueTemplate, ...]:
    return tuple(_template(row) for row in storage.list_templates(state))


def save_template(state, name: str, recipe: Recipe, *, previous: QueueTemplate | None = None) -> QueueTemplate:
    return _template(
        storage.save_template(
            state,
            name,
            recipe_record(recipe),
            identity=previous.identity if previous else None,
            revision=previous.revision if previous else None,
            validate=recipe_from_record,
        )
    )


def rename_template(state, previous: QueueTemplate, name: str) -> QueueTemplate:
    return _template(
        storage.rename_template(state, previous.identity, previous.revision, name, validate=recipe_from_record)
    )


def delete_template(state, previous: QueueTemplate) -> None:
    storage.delete_template(state, previous.identity, previous.revision, validate=recipe_from_record)


def summary(template: QueueTemplate) -> str:
    r = template.recipe
    scope = f"{r.count} items" if r.form.mode == "Repeat" else "ordered comparison dimensions"
    seeds = "incrementing" if r.form.increment or r.form.mode == "Repeat" else "fixed/listed"
    pose = f" · Pose · {r.pose.control.checkpoint} · strength {r.pose.control.strength:g}" if r.pose else ""
    return f"{r.backend} · {r.form.mode} · {scope} · {r.request.model_id} · {r.form.variation} · image seed {r.request.seed} ({seeds}) · plan seed {r.form.rng_seed or 'not set'}{pose}"

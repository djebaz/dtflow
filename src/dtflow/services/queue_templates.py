"""Local queue authoring recipes. No provider, planner execution, queue creation or receipt authority."""

from dataclasses import asdict, dataclass, fields, replace

from dtflow.core.models import MAX_QUEUE_CONCURRENCY, ResolvedGenerationRequest, ValidationError, integer
from dtflow.core.queue_authoring import PlanForm, queue_edit_from_source
from dtflow.core.request_records import request_from_record
from dtflow.persistence import queue_templates as storage


@dataclass(frozen=True, slots=True)
class Recipe:
    request: ResolvedGenerationRequest
    form: PlanForm
    count: int
    backend: str
    cap: int
    reconstructed: bool = False


@dataclass(frozen=True, slots=True)
class QueueTemplate:
    identity: str
    name: str
    revision: int
    recipe: Recipe


def recipe_record(recipe: Recipe) -> dict:
    if not isinstance(recipe, Recipe) or not isinstance(recipe.request, ResolvedGenerationRequest):
        raise ValidationError("Invalid template recipe.")
    if recipe.request.operation != "txt2img":
        raise ValidationError("Templates currently support text-to-image only; image inputs are not portable.")
    base = asdict(replace(recipe.request, prepared_assets=(), resolution_evidence=()))
    for name in ("request_id", "prepared_assets", "resolution_evidence"):
        base.pop(name)
    record = {
        "version": 1,
        "backend": recipe.backend,
        "cap": recipe.cap,
        "reconstructed": recipe.reconstructed,
        "source": {"version": 1, "base": base, "form": asdict(recipe.form), "count": recipe.count},
    }
    recipe_from_record(record)
    return record


def recipe_from_record(record: dict) -> Recipe:
    try:
        if not isinstance(record, dict) or set(record) != {"version", "backend", "cap", "source", "reconstructed"}:
            raise ValidationError("Unsupported template fields.")
        if type(record["version"]) is not int or record["version"] != 1:
            raise ValidationError("Unsupported template recipe version.")
        if record["backend"] not in ("fake", "local", "cloud"):
            raise ValidationError("Unsupported template backend requirement.")
        integer(record["cap"], 1, MAX_QUEUE_CONCURRENCY, "Template concurrency")
        if type(record["reconstructed"]) is not bool:
            raise ValidationError("Invalid template reconstruction evidence.")
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
            record["cap"],
            record["reconstructed"],
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
    return f"{r.backend} · {r.form.mode} · {scope} · {r.request.model_id} · {r.form.variation} · image seed {r.request.seed} ({seeds}) · plan seed {r.form.rng_seed or 'not set'}"

"""Qt-free mapping between the Queues page's Plan area and the shared planner.

Widgets hand over plain ``PlanForm`` text; this module only splits it into the planner's dimension values (no value
rules, bounds or ordering live here: ``expand_plan``/``describe_plan`` own them) and maps planner errors to the widget
that owns the field, using the same leading-word approach as the Generate form.
"""

import re
from dataclasses import dataclass

from dtflow.core.models import MAX_QUEUE_ITEMS, ValidationError
from dtflow.services.planning import Plan, PlanError, PlanTable, plan_size, seed_range
from dtflow.ui.generation_presenter import FormError

MODES = {"Pair": "pair", "Cross": "cross"}
# Dimension name (or leading error word) -> Designer object name of the owning widget.
ERROR_FIELDS = {
    "prompt": "planPromptsEdit",
    "model": "planModelsEdit",
    "model_id": "planModelsEdit",
    "seed": "planSeedsEdit",
    "steps": "planStepsEdit",
    "guidance": "planGuidanceEdit",
    "sampler": "planSamplersEdit",
    "unsupported": "planSamplersEdit",
    "size": "planSizesEdit",
    "width_px": "planSizesEdit",
    "height_px": "planSizesEdit",
    "lora_weight": "planLorasEdit",
    "lora": "planLorasEdit",
    "strength": "planStrengthsEdit",
    "loras": "planLoraSetsEdit",
}
NO_LORA = "None"  # the LoRA-set chip for an item without LoRAs
SEED_POLICY_TEXT = {
    "fixed": "the base seed is kept for every item",
    "increment": "seeds increment from the base seed",
    "dimension": "seeds come from the listed seed values",
}


@dataclass(frozen=True, slots=True)
class PlanForm:
    """Raw text of the Plan area: scalar lists use ``;`` separators, prompts and LoRAs one entry per line."""

    mode: str = "Cross"
    prompts: str = ""
    models: str = ""
    seeds: str = ""
    steps: str = ""
    guidance: str = ""
    samplers: str = ""
    sizes: str = ""
    loras: str = ""
    increment: bool = False
    strengths: str = ""
    lora_sets: str = ""  # ``;``-separated sets: ``None`` or ``IDENTIFIER: 0.8 + OTHER: 0.4``


def _tokens(text: str, separator: str = ";") -> list[str]:
    return [token.strip() for token in text.replace("\n", separator).split(separator) if token.strip()]


def _numbers(text: str, kind, widget: str, noun: str) -> tuple:
    try:
        return tuple(kind(token) for token in _tokens(text))
    except ValueError:
        raise FormError(f"{noun} must be a ';'-separated list of numbers.", widget) from None


def _seeds(text: str) -> tuple[int, ...]:
    values: list[int] = []
    for token in _tokens(text):
        try:
            if ".." in token:
                low, high = (int(part) for part in token.split("..", 1))
                if high < low:
                    raise ValueError
                values.extend(seed_range(low, high - low + 1))
            else:
                values.append(int(token))
        except PlanError as exc:
            raise FormError(str(exc), "planSeedsEdit") from exc
        except ValueError:
            raise FormError("seed values are integers or LOW..HIGH ranges separated by ';'.", "planSeedsEdit") from None
    return tuple(values)


def _sizes(text: str) -> tuple[tuple[int, int], ...]:
    sizes = []
    for token in _tokens(text):
        match = re.fullmatch(r"(\d+)\s*[x×]\s*(\d+)", token)
        if match is None:
            raise FormError("sizes are WIDTHxHEIGHT pairs separated by ';'.", "planSizesEdit")
        sizes.append((int(match.group(1)), int(match.group(2))))
    return tuple(sizes)


def _loras(text: str) -> list[tuple[str, tuple[float, ...]]]:
    entries = []
    for line in filter(str.strip, text.splitlines()):
        identifier, separator, weights = line.rpartition(":")
        if not separator or not identifier.strip():
            raise FormError("LoRA weights are written IDENTIFIER: 0.6; 0.8, one LoRA per line.", "planLorasEdit")
        entries.append((identifier.strip(), _numbers(weights, float, "planLorasEdit", "LoRA weights")))
    return entries


def _lora_sets(text: str) -> tuple[tuple[tuple[str, float], ...], ...]:
    sets = []
    for token in _tokens(text):
        if token.lower() == NO_LORA.lower():
            sets.append(())
            continue
        entries = []
        for part in token.split("+"):
            identifier, separator, weight = part.rpartition(":")
            try:
                if not separator or not identifier.strip():
                    raise ValueError
                entries.append((identifier.strip(), float(weight)))
            except ValueError:
                raise FormError(
                    "LoRA sets are None or IDENTIFIER: 0.8 + OTHER: 0.4, separated by ';'.", "planLoraSetsEdit"
                ) from None
        sets.append(tuple(entries))
    return tuple(sets)


def lora_set_value(identifier: str, weight: float) -> str:
    """The chip text for a one-LoRA set, as ``_lora_sets`` reads it back."""
    return f"{identifier}: {weight:g}"


def build_plan(form: PlanForm) -> Plan:
    """The ``Plan`` for the non-empty fields; a blank field means that dimension stays fixed from the base request."""
    dimensions: list[tuple[str, tuple]] = []
    prompts = tuple(line.strip() for line in form.prompts.splitlines() if line.strip())
    for name, values in (
        ("prompt", prompts),
        ("model", tuple(_tokens(form.models))),
        ("seed", _seeds(form.seeds)),
        ("steps", _numbers(form.steps, int, "planStepsEdit", "steps")),
        ("guidance", _numbers(form.guidance, float, "planGuidanceEdit", "guidance")),
        ("sampler", tuple(_tokens(form.samplers))),
        ("size", _sizes(form.sizes)),
        ("strength", _numbers(form.strengths, float, "planStrengthsEdit", "strengths")),
    ):
        if values:
            dimensions.append((name, values))
    sets = _lora_sets(form.lora_sets)
    if sets:
        dimensions.append(("loras", sets))
    dimensions.extend((f"lora_weight.{identifier}", weights) for identifier, weights in _loras(form.loras))
    return Plan(MODES.get(form.mode, "cross"), tuple(dimensions), seed_policy="increment" if form.increment else None)


def field_for(message: str) -> str | None:
    """Owning widget of a planner message: a quoted dimension name, else the leading word (``Item N:`` is skipped)."""
    message = re.sub(r"^Item \d+: ", "", message)
    quoted = re.search(r"[Dd]imension '([A-Za-z_]+)", message)
    if quoted:
        return ERROR_FIELDS.get(quoted.group(1).lower())
    if "LoRA set" in message:
        return ERROR_FIELDS["loras"]
    if "LoRA '" in message:
        return ERROR_FIELDS["lora"]
    match = re.match(r"[A-Za-z_]+", message)
    return ERROR_FIELDS.get(match.group(0).lower()) if match else None


def plan_error(exc: ValidationError) -> FormError:
    return exc if isinstance(exc, FormError) else FormError(str(exc), field_for(str(exc)))


def count_text(form: PlanForm) -> tuple[str, str]:
    """(level, text) of the live readout: the arithmetic count, or the reason there is none yet."""
    try:
        total = plan_size(build_plan(form))
    except ValidationError as exc:
        return "unknown", str(exc)
    if total > MAX_QUEUE_ITEMS:
        return "error", f"{total} items: more than one queue holds ({MAX_QUEUE_ITEMS}). Nothing will be created."
    return "ok", f"{total} item{'s' if total != 1 else ''}"


def sentence(text: str) -> str:
    """Messages from shared validators start lowercase with a field name; shown alone they read as a sentence."""
    return text[:1].upper() + text[1:]


def summary_text(table: PlanTable) -> str:
    return f"{table.total} items · {table.mode} · {SEED_POLICY_TEXT[table.seed_policy]}. Nothing is persisted yet."

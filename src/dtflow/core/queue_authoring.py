"""Backend-neutral authoring projection shared by queue history and durable templates."""

from dataclasses import asdict, dataclass

from dtflow.core.models import MAX_QUEUE_ITEMS, ValidationError
from dtflow.core.request_records import request_from_record


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
    draws: tuple[tuple[str, int], ...] = ()  # (dimension, N): draw N at random from the listed values (0: use all)
    variation: str = "Off"  # prompt variation: Off, Random sample or Every combination
    variation_count: int = 4
    wildcards: str = ""  # one inline list per line: ``name: entry; entry``
    shuffle: str = "No shuffle"
    drop: int = 0
    add: int = 0
    add_parts: str = ""  # ``;``-separated parts that may be inserted into each prompt
    rng_seed: str = ""  # the plan RNG seed as typed; empty until a random draw needs one

    @property
    def uses_randomness(self) -> bool:
        edits = self.shuffle != "No shuffle" or self.drop > 0 or (self.add > 0 and bool(self.add_parts.strip()))
        return self.mode != "Repeat" and (
            any(count for _, count in self.draws)
            or self.variation == "Random sample"
            or (self.variation != "Off" and edits)
        )


@dataclass(frozen=True, slots=True)
class QueueEdit:
    """Authoring input for the independent queue draft, never an implicit change to Generate."""

    base: dict
    form: PlanForm
    count: int  # the Repeat count when the items only differ by seed
    note: str  # non-empty when the items were not a plain Repeat, Pair or Cross


def queue_edit_from_source(source: dict) -> QueueEdit:
    """Restore exact authoring input, never reverse-engineer it from randomly resolved item prompts."""
    try:
        if type(source.get("version")) is not int or source["version"] != 1:
            raise ValidationError("Unsupported queue composer source version; recorded items are unchanged.")
        base = request_from_record(source["base"])
        if base.operation != "txt2img":
            raise ValidationError("Only text-to-image queues can be edited as a new queue.")
        values = dict(source["form"])
        values["draws"] = tuple(tuple(pair) for pair in values.get("draws", ()))
        form = PlanForm(**values)
        for name, default in asdict(PlanForm()).items():
            value = getattr(form, name)
            if type(value) is not type(default):
                raise ValidationError("Stored queue composer fields are unreadable.")
        if form.mode not in ("Repeat", "Pair", "Cross") or form.variation not in (
            "Off",
            "Random sample",
            "Every combination",
        ):
            raise ValidationError("Stored queue composer mode is unsupported.")
        if form.shuffle not in ("No shuffle", "Shuffle parts", "Shuffle words"):
            raise ValidationError("Stored queue composer shuffle mode is unsupported.")
        if any(len(pair) != 2 or type(pair[0]) is not str or type(pair[1]) is not int for pair in form.draws):
            raise ValidationError("Stored queue random draws are unreadable.")
        if len(dict(form.draws)) != len(form.draws) or any(
            name not in ("model", "seed", "steps", "guidance", "sampler", "size", "strength", "loras", "lora_weight")
            or not 0 <= value <= MAX_QUEUE_ITEMS
            for name, value in form.draws
        ):
            raise ValidationError("Stored queue random draws are unsupported.")
        if not 1 <= form.variation_count <= MAX_QUEUE_ITEMS or not 0 <= form.drop <= 50 or not 0 <= form.add <= 50:
            raise ValidationError("Stored queue variation values are unsupported.")
        count = source["count"]
        if type(count) is not int or not 1 <= count <= MAX_QUEUE_ITEMS:
            raise ValidationError("Stored queue count is unsupported.")
        return QueueEdit(asdict(base), form, count, "")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Stored queue composer source is unreadable; recorded items are unchanged.") from exc

"""Qt-free mapping between the Queues page's Plan area and the shared planner.

Widgets hand over plain ``PlanForm`` text; this module only splits it into the planner's dimension values (no value
rules, bounds or ordering live here: ``expand_plan``/``describe_plan`` own them) and maps planner errors to the widget
that owns the field, using the same leading-word approach as the Generate form.
"""

import re
from pathlib import Path

from dtflow.core.queue_authoring import (
    PlanForm as PlanForm,
    QueueEdit as QueueEdit,
    queue_edit_from_source as queue_edit_from_source,
)
from dtflow.core.models import MAX_QUEUE_ITEMS, SEED_LIMIT, ValidationError
from dtflow.services.planning import Plan, PlanError, PlanTable, PromptVariation, plan_size, seed_range
from dtflow.services.randomness import RandomSpec
from dtflow.services.wildcards import bind_wildcards
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
    "wildcard": "planWildcardsEdit",
    "wildcards": "planWildcardsEdit",
    "parts": "planAddPartsEdit",
    "plan_seed": "planRngSeedEdit",
}
# Plan dimension -> the suffix of its "Draw at random" spin box (planDraw<suffix>Spin).
DRAW_SPINS = {
    "model": "Models",
    "seed": "Seeds",
    "steps": "Steps",
    "guidance": "Guidance",
    "sampler": "Samplers",
    "size": "Sizes",
    "strength": "Strengths",
    "loras": "LoraSets",
    "lora_weight": "Loras",
}
SHUFFLES = {"Shuffle parts": "parts", "Shuffle words": "words"}
VARIATIONS = {"Random sample": "random", "Every combination": "all"}
WILDCARD_NAME = re.compile(r"[A-Za-z0-9]+(?:[_\-][A-Za-z0-9]+)*")
NO_LORA = "None"  # the LoRA-set chip for an item without LoRAs
SEED_POLICY_TEXT = {
    "fixed": "the base seed is kept for every item",
    "increment": "seeds increment from the base seed",
    "dimension": "seeds come from the listed seed values",
}


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
        for part in re.split(r"\s\+\s", token):  # `` + `` joins LoRAs; a ``+`` inside an identifier stays
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


def _wildcards(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    lists: dict[str, tuple[str, ...]] = {}
    for line in filter(str.strip, text.splitlines()):
        name, separator, entries = line.partition(":")
        name = name.strip()
        values = tuple(_tokens(entries))
        if not separator or not WILDCARD_NAME.fullmatch(name) or not values or name in lists:
            raise FormError(
                "Wildcard lists are written name: entry; entry, one list per line, each name once.",
                "planWildcardsEdit",
            )
        lists[name] = values
    return tuple(lists.items())


def _rng_seed(text: str) -> int | None:
    if not text.strip():
        return None
    try:
        seed = int(text.strip())
    except ValueError:
        raise FormError("The plan seed is a whole number.", "planRngSeedEdit") from None
    if not 0 <= seed <= SEED_LIMIT:
        raise FormError(f"The plan seed is a whole number in 0..{SEED_LIMIT}.", "planRngSeedEdit")
    return seed


def candidate_counts(form: PlanForm) -> dict[str, int]:
    """How many different values each drawable row lists (a draw can take at most that many); seeds are unbounded."""

    def distinct(tokens, kind=str) -> int:
        """Different values as the planner sees them: ``20`` and ``20.0``, ``512x512`` and ``512 x 512`` are one."""
        values = []
        for token in tokens:
            try:
                values.append(kind(token))
            except ValueError:
                values.append(token)
        return len(dict.fromkeys(values))

    def size(token: str):
        match = re.fullmatch(r"(\d+)\s*[x×]\s*(\d+)", token)
        if match is None:
            raise ValueError(token)
        return int(match.group(1)), int(match.group(2))

    lines = [line.rpartition(":")[2] for line in form.loras.splitlines() if line.strip()]
    return {
        "model": distinct(_tokens(form.models)),
        "steps": distinct(_tokens(form.steps), int),
        "guidance": distinct(_tokens(form.guidance), float),
        "sampler": distinct(_tokens(form.samplers)),
        "size": distinct(_tokens(form.sizes), size),
        "strength": distinct(_tokens(form.strengths), float),
        "loras": distinct(_tokens(form.lora_sets)),
        "lora_weight": min((distinct(_tokens(line), float) for line in lines), default=0),
    }


def parse_lora_set(chip: str) -> tuple[tuple[str, float], ...]:
    """The (identifier, weight) pairs of one LoRA-set chip, for editing it; an unreadable chip has none."""
    try:
        return _lora_sets(chip)[0]
    except (FormError, IndexError):
        return ()


def lora_set_value(identifier: str, weight: float) -> str:
    """The chip text for a one-LoRA set, as ``_lora_sets`` reads it back."""
    return f"{identifier}: {weight:g}"


def build_plan(form: PlanForm) -> Plan:
    """The ``Plan`` for the non-empty fields; a blank field means that dimension stays fixed from the base request."""
    draws = dict(form.draws)

    def varied(name: str, values: tuple):
        """The listed values, or a random draw of ``draws[name]`` of them."""
        count = draws.get(name, 0)
        if not count:
            return values
        if not values:
            raise FormError("Add values to draw from.", ERROR_FIELDS[name])
        return RandomSpec(count, candidates=values)

    dimensions: list[tuple[str, object]] = []
    prompts = tuple(line.strip() for line in form.prompts.splitlines() if line.strip())
    if form.variation != "Off":
        if prompts:
            raise FormError("Use either a prompt list or prompt variation, not both.", "planPromptsEdit")
        strategy = VARIATIONS.get(form.variation, "random")
        parts = tuple(_tokens(form.add_parts))
        if form.add and not parts:
            raise FormError("Add parts to insert under Parts to add, or set Add up to to 0.", "planAddPartsEdit")
        prompts = PromptVariation(
            strategy,
            form.variation_count if strategy == "random" else None,
            None,
            _wildcards(form.wildcards),
            shuffle=SHUFFLES.get(form.shuffle),
            drop=form.drop,
            add=parts,
            add_max=form.add if parts else 0,
        )
    for name, values in (
        ("prompt", prompts),
        ("model", varied("model", tuple(_tokens(form.models)))),
        ("seed", RandomSpec(draws["seed"]) if draws.get("seed") else _seeds(form.seeds)),
        ("steps", varied("steps", _numbers(form.steps, int, "planStepsEdit", "steps"))),
        ("guidance", varied("guidance", _numbers(form.guidance, float, "planGuidanceEdit", "guidance"))),
        ("sampler", varied("sampler", tuple(_tokens(form.samplers)))),
        ("size", varied("size", _sizes(form.sizes))),
        ("strength", varied("strength", _numbers(form.strengths, float, "planStrengthsEdit", "strengths"))),
    ):
        if values is not None and (isinstance(values, RandomSpec | PromptVariation) or len(values)):
            dimensions.append((name, values))
    sets = _lora_sets(form.lora_sets)
    if sets:
        dimensions.append(("loras", varied("loras", sets)))
    elif draws.get("loras"):
        raise FormError("Add LoRA sets to draw from.", "planLoraSetsEdit")
    weights = _loras(form.loras)
    if weights:
        dimensions.extend(
            (f"lora_weight.{identifier}", varied("lora_weight", values)) for identifier, values in weights
        )
    elif draws.get("lora_weight"):
        raise FormError("Add LoRA weights to draw from.", "planLorasEdit")
    return Plan(
        MODES.get(form.mode, "cross"),
        tuple(dimensions),
        seed_policy="increment" if form.increment else None,
        rng_seed=_rng_seed(form.rng_seed),
    )


def field_for(message: str) -> str | None:
    """Owning widget of a planner message: a quoted dimension name, else the leading word (``Item N:`` is skipped)."""
    message = re.sub(r"^Item \d+: ", "", message)
    quoted = re.search(r"[Dd]imension '([A-Za-z_]+)", message)
    if quoted:
        return ERROR_FIELDS.get(quoted.group(1).lower())
    lower = message.lower()
    if "wildcard" in lower:
        return ERROR_FIELDS["wildcard"]
    if "plan seed" in lower:
        return ERROR_FIELDS["plan_seed"]
    if "LoRA set" in message:
        return ERROR_FIELDS["loras"]
    if "LoRA '" in message:
        return ERROR_FIELDS["lora"]
    match = re.match(r"[A-Za-z_]+", message)
    return ERROR_FIELDS.get(match.group(0).lower()) if match else None


def plan_error(exc: ValidationError) -> FormError:
    return exc if isinstance(exc, FormError) else FormError(str(exc), field_for(str(exc)))


def count_text(form: PlanForm, prompt: str | None = None, folder: Path | None = None) -> tuple[str, str]:
    """(level, text) of the live readout: the arithmetic count, or the reason there is none yet.

    ``prompt`` is the Generate prompt (the variation template) and ``folder`` the wildcard lists folder, both optional.
    """
    try:
        plan = build_plan(form)
        if folder is not None:
            plan = bind_wildcards(plan, prompt or "", folder)
        total = plan_size(plan, prompt)
    except ValidationError as exc:
        return "unknown", str(exc)
    if total > MAX_QUEUE_ITEMS:
        return "error", f"{total} items: more than one queue holds ({MAX_QUEUE_ITEMS}). Nothing will be created."
    return "ok", f"{total} item{'s' if total != 1 else ''}"


def images_requests(total: int) -> str:
    """``3 images, 3 requests``: every queue item is one single-image request with its own thumbnail and seed (#279)."""
    return f"{total} image{'s' if total != 1 else ''}, {total} request{'s' if total != 1 else ''}"


def readout_text(text: str) -> str:
    """The live count readout worded as images and requests; reasons and errors pass through unchanged."""
    match = re.fullmatch(r"(\d+) items?", text)
    return images_requests(int(match.group(1))) if match else text


def sentence(text: str) -> str:
    """Messages from shared validators start lowercase with a field name; shown alone they read as a sentence."""
    return text[:1].upper() + text[1:]


def summary_text(table: PlanTable) -> str:
    drawn = f" · plan seed {table.rng_seed}" if table.rng_seed is not None else ""
    return f"{images_requests(table.total)} · {table.mode} · {SEED_POLICY_TEXT[table.seed_policy]}{drawn}. Nothing is persisted yet."


def _distinct(values) -> list:
    return list(dict.fromkeys(values))


def queue_edit_from_records(records) -> QueueEdit:
    """Read a recorded queue back into Generate values and a composer plan (text-to-image items only).

    Every dimension that differs between items becomes a list of values; the rest stay with the base request (the
    first item). Seeds that step by one from the first item are the Increment option, not a list. Items that are
    exactly the product of the distinct values load as Cross, items that only differ by incrementing seeds as Repeat,
    and anything else as Pair, whose rows are the items themselves (repeated values included), so the count is kept.
    """
    if not records:
        raise ValidationError("This queue has no recorded items to edit.")
    if any(record.get("operation", "txt2img") != "txt2img" for record in records):
        raise ValidationError("Only text-to-image queues can be edited as a new queue.")

    def lora_set(record) -> str:
        return (
            " + ".join(lora_set_value(item["identifier"], item["weight"]) for item in record.get("loras", ()))
            or NO_LORA
        )

    count = len(records)
    note = ""
    prompts = [record.get("prompt", "") for record in records]
    if any("\n" in prompt for prompt in prompts):
        prompts = [" ".join(prompt.split()) for prompt in prompts]
        note = "A prompt with line breaks was joined onto one line."
    columns = {
        "prompts": prompts,
        "models": [record.get("model_id", "") for record in records],
        "seeds": [record.get("seed", 0) for record in records],
        "steps": [record.get("steps", 20) for record in records],
        "guidance": [record.get("guidance", 7.0) for record in records],
        "samplers": [record.get("sampler_id", "") for record in records],
        "sizes": [f"{record.get('width_px', 512)}x{record.get('height_px', 512)}" for record in records],
    }
    # The same LoRAs in every item that only differ by weight are weights to cross; otherwise whole LoRA sets differ.
    lora_ids = {tuple(item["identifier"] for item in record.get("loras", ())) for record in records}
    names = next(iter(lora_ids))
    if len(lora_ids) == 1 and names and len(set(names)) == len(names):
        for index, identifier in enumerate(names):
            columns[f"weight:{identifier}"] = [record["loras"][index]["weight"] for record in records]
    else:
        columns["lora_sets"] = [lora_set(record) for record in records]
    seeds = columns["seeds"]
    increment = count > 1 and seeds == list(range(seeds[0], seeds[0] + count))
    if increment:
        del columns["seeds"]  # the Increment option reproduces them
    distinct = {name: _distinct(values) for name, values in columns.items()}
    varying = [name for name, values in distinct.items() if len(values) > 1]
    if not varying and not increment and count > 1:
        # Identical items cannot be a Pair (the planner refuses duplicate rows): load a Repeat, whose seeds step.
        increment = True
        note = f"{note} Identical items were loaded as a Repeat; its seeds will step.".strip()
    rows = list(zip(*(columns[name] for name in varying), strict=True)) if varying else []
    product = 1
    for name in varying:
        product *= len(distinct[name])
    if not varying:
        mode = "Repeat"
    elif product == count and len(set(rows)) == count and set(rows) == set(_product(distinct, varying)):
        mode = "Cross"
    else:
        mode = "Pair"

    def text(name: str, separator: str = "; ") -> str:
        if name not in varying:
            return ""
        values = distinct[name] if mode == "Cross" else columns[name]
        return separator.join(f"{value:g}" if isinstance(value, float) else str(value) for value in values)

    form = PlanForm(
        mode=mode,
        prompts=text("prompts", "\n"),
        models=text("models"),
        seeds=text("seeds"),
        steps=text("steps"),
        guidance=text("guidance"),
        samplers=text("samplers"),
        sizes=text("sizes"),
        increment=increment,
        lora_sets=text("lora_sets"),
        loras="\n".join(
            f"{name.removeprefix('weight:')}: {text(name)}" for name in varying if name.startswith("weight:")
        ),
    )
    return QueueEdit(dict(records[0]), form, count, note)


def _product(distinct: dict[str, list], names: list[str]):
    """Every combination of the distinct values of ``names``, as tuples in ``names`` order."""
    combos = [()]
    for name in names:
        combos = [(*combo, value) for combo in combos for value in distinct[name]]
    return combos

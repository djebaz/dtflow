"""Bounded, deterministic queue planning: a base request plus varied dimensions become an ordered request list.

Pure and Qt-, SQLite- and network-free. The planner emits ordinary ``ResolvedGenerationRequest`` values through the same
validated construction used for single requests, so scheduling, reservations, restart and no-replay stay with the queue.
The item count is computed arithmetically and bounded before anything is enumerated. Nothing here infers LoRA or model
compatibility; listed identifiers and weights are forwarded and left to preflight and receipt binding at creation.

Modes: ``seed`` (count items, seed incrementing from the base seed: the original ``queue add -k`` rule), ``pair`` (zip of
equal-length dimensions) and ``cross`` (product; the last listed dimension varies fastest, like nested loops). In pair and
cross the base seed stays fixed for every item unless a seed dimension is listed or the plan says ``seed: increment``.
"""

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import prod
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import (
    MAX_QUEUE_ITEMS,
    SEED_LIMIT,
    LoRASelection,
    PreparedAsset,
    ResolvedGenerationRequest,
    Sampler,
    ValidationError,
)
from dtflow.services import dynamic_prompts
from dtflow.services.config import ConfigError, read_json_file, reject_credentials
from dtflow.services.plan_errors import PlanError
from dtflow.services.randomness import RandomSpec, check, draw

SCHEMA = "dtflow.queue-plan"
VERSION = 2
READABLE = (1, 2)  # version 2 adds random dimensions, the plan RNG seed and prompt variation
MODES = ("seed", "pair", "cross")
SEED_POLICIES = ("fixed", "increment")
SCALARS = ("prompt", "model", "seed", "steps", "guidance", "sampler", "size", "strength")
LORA_WEIGHT = "lora_weight."  # dimension name prefix; the rest is the exact LoRA identifier from the base request
LORA_SETS = "loras"  # each value is a whole LoRA set: ((identifier, weight), ...); an empty set means no LoRA
FIELD_NAMES = {
    "prompt": "prompt",
    "model": "model_id",
    "seed": "seed",
    "steps": "steps",
    "guidance": "guidance",
    "strength": "strength",
}


RANDOM_PICK_ONLY = ("prompt", "model", "sampler", "size", LORA_SETS)  # no numeric range makes sense for these
PROMPT_STRATEGIES = ("random", "all")


@dataclass(frozen=True, slots=True)
class PromptVariation:
    """The ``prompt`` dimension as a dynamic-prompt template (#129); ``template`` None means the base request's prompt.

    ``wildcards`` are the lists in use as (name, entries); ``sources`` are (name, sha256) for lists read from files.
    """

    strategy: str = "random"
    count: int | None = None
    template: str | None = None
    wildcards: tuple[tuple[str, tuple[str, ...]], ...] = ()
    sources: tuple[tuple[str, str], ...] = ()
    shuffle: str | None = None  # "parts" (comma-separated phrases) or "words"; seeded like everything else
    drop: int = 0  # remove up to this many parts per prompt
    add: tuple[str, ...] = ()  # parts that may be inserted ...
    add_max: int = 0  # ... up to this many per prompt

    @property
    def mutation(self) -> dynamic_prompts.Mutation:
        return dynamic_prompts.Mutation(self.shuffle, self.drop, tuple(self.add), self.add_max)

    def __post_init__(self) -> None:
        object.__setattr__(self, "add", tuple(self.add))
        object.__setattr__(self, "wildcards", tuple((name, tuple(entries)) for name, entries in self.wildcards))
        object.__setattr__(self, "sources", tuple((name, digest) for name, digest in self.sources))


@dataclass(frozen=True, slots=True)
class Plan:
    mode: str
    dimensions: tuple[
        tuple[str, Any], ...
    ] = ()  # (name, values) where values may also be a RandomSpec / PromptVariation
    count: int | None = None
    seed_policy: str | None = None  # None: ``seed`` mode increments, pair/cross keep the base seed
    rng_seed: int | None = None  # the one recorded seed behind every random dimension and a random prompt sample
    evidence: tuple[tuple[str, str], ...] = ()  # filled by ``resolve_plan``: how the random values were drawn

    def __post_init__(self) -> None:
        # Lists (from JSON or widgets) become tuples so values are hashable and the plan is immutable.
        normal = tuple(
            (
                name,
                values
                if isinstance(values, RandomSpec | PromptVariation)
                else tuple(tuple(v) if isinstance(v, list) else v for v in values),
            )
            for name, values in self.dimensions
        )
        object.__setattr__(self, "dimensions", normal)

    @property
    def draws(self) -> bool:
        """Whether any dimension is drawn from the plan RNG seed (a prompt listing every combination is not)."""
        return any(
            isinstance(values, RandomSpec)
            or (isinstance(values, PromptVariation) and (values.strategy == "random" or values.mutation.active))
            for _, values in self.dimensions
        )

    @property
    def is_random(self) -> bool:
        return self.rng_seed is not None or any(
            isinstance(values, RandomSpec | PromptVariation) for _, values in self.dimensions
        )

    @property
    def effective_seed_policy(self) -> str:
        if any(name == "seed" for name, _ in self.dimensions):
            return "dimension"
        return self.seed_policy or ("increment" if self.mode == "seed" else "fixed")


@dataclass(frozen=True, slots=True)
class PlanTable:
    """Dry-run output as plain rows: what varies, what each item resolves to, and how seeds are chosen."""

    mode: str
    seed_policy: str
    total: int
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    rng_seed: int | None = None


def seed_range(start: int, count: int) -> tuple[int, ...]:
    """Consecutive seeds ``start..start+count-1`` as an explicit seed dimension, bounded before it is built."""
    if type(start) is not int or type(count) is not int or not 1 <= count <= MAX_QUEUE_ITEMS:
        raise PlanError(f"A seed range needs an integer start and a count in 1..{MAX_QUEUE_ITEMS}.")
    if start < 0 or start + count - 1 > SEED_LIMIT:
        raise PlanError("Seed range would exceed the 32-bit seed range.")
    return tuple(range(start, start + count))


def new_rng_seed() -> int:
    """A fresh plan RNG seed (a *Reroll*); the only non-deterministic step, and it is recorded."""
    return secrets.randbelow(SEED_LIMIT + 1)


def with_rng_seed(plan: Plan, seed: int | None = None) -> Plan:
    """The plan carrying an RNG seed: ``seed``, or its own, or a fresh one when it has none."""
    if seed is None:
        seed = plan.rng_seed if plan.rng_seed is not None else new_rng_seed()
    if type(seed) is not int or not 0 <= seed <= SEED_LIMIT:
        raise PlanError("The plan seed is a whole number in 0..4294967295.")
    return replace(plan, rng_seed=seed)


def _check_spec(name: str, spec: RandomSpec) -> None:
    if name.startswith(LORA_WEIGHT) or name in ("steps", "guidance", "strength", "seed"):
        pass
    elif name in RANDOM_PICK_ONLY and spec.kind != "pick":
        raise PlanError(f"Dimension {name!r} draws from a list of candidates, not a range.")
    check(name, spec)


def _check_variation(variation: PromptVariation) -> None:
    if variation.strategy not in PROMPT_STRATEGIES:
        raise PlanError("Prompt variation is a random sample or every combination.")
    if variation.strategy == "random" and (
        type(variation.count) is not int or not 1 <= variation.count <= MAX_QUEUE_ITEMS
    ):
        raise PlanError(f"A random sample of prompts needs a count in 1..{MAX_QUEUE_ITEMS}.")
    if variation.strategy == "all" and variation.count is not None:
        raise PlanError("Every combination takes no count.")
    dynamic_prompts.check_mutation(variation.mutation)


def _variation_total(variation: PromptVariation, prompt: str | None, bound: bool) -> int | None:
    """The structural variant count, or None while it cannot be known: no template yet, or wildcard lists not bound."""
    if variation.strategy == "random":
        return variation.count
    template = variation.template if variation.template is not None else prompt
    if template is None:
        return None  # the base prompt is not known yet; the exact count comes with the request
    lists = dynamic_prompts.Lists(dict(variation.wildcards))
    try:
        return dynamic_prompts.count(dynamic_prompts.parse(template), lists)
    except dynamic_prompts.UndefinedWildcard:
        if bound:
            raise
        return None  # a plan file's wildcard lists may still come from the wildcards folder


def resolve_plan(plan: Plan, prompt: str | None = None) -> Plan:
    """Turn random specs and prompt variation into concrete value lists, recording how they were drawn.

    Idempotent. ``prompt`` is the base request's prompt, the template when the variation names none. A plan with random
    dimensions must already carry an ``rng_seed`` (see ``with_rng_seed``): nothing here invents one.
    """
    if not any(isinstance(values, RandomSpec | PromptVariation) for _, values in plan.dimensions):
        return plan
    drawn = [
        isinstance(values, RandomSpec) or getattr(values, "strategy", "") == "random" for _, values in plan.dimensions
    ]
    if any(drawn) and plan.rng_seed is None:
        raise PlanError("A random plan needs a plan seed; reroll to draw one.")
    seed = plan.rng_seed if plan.rng_seed is not None else 0
    evidence: list[tuple[str, str]] = [("plan.rng_seed", str(plan.rng_seed))] if plan.rng_seed is not None else []
    dimensions = []
    for name, values in plan.dimensions:
        if isinstance(values, RandomSpec):
            _check_spec(name, values)
            evidence.append((f"plan.random.{name}", values.describe()))
            values = draw(name, values, seed)
        elif isinstance(values, PromptVariation):
            if name != "prompt":
                raise PlanError("Only the prompt dimension takes prompt variation.")
            _check_variation(values)
            template = values.template if values.template is not None else prompt
            if template is None:
                raise PlanError("Prompt variation needs a template or a base prompt.")
            resolved, used = dynamic_prompts.variants(
                template,
                dict(values.wildcards),
                strategy=values.strategy,
                n=values.count,
                rng_seed=seed,
                mutation=values.mutation,
            )
            sources = dict(values.sources)
            evidence.append(("plan.prompt_strategy", values.strategy + (f" {values.count}" if values.count else "")))
            evidence.append(("plan.prompt_template", template))
            if values.mutation.active:
                evidence.append(("plan.prompt_mutation", values.mutation.describe()))
            evidence.extend((f"plan.wildcard.{wild}", sources.get(wild, "inline")) for wild in used)
            values = resolved
        dimensions.append((name, values))
    return replace(plan, dimensions=tuple(dimensions), evidence=(*plan.evidence, *evidence))


def plan_size(plan: Plan, prompt: str | None = None, *, bound: bool = True) -> int:
    """Arithmetic item count, computed before any expansion. Structural problems raise; an oversize count is returned."""
    if plan.mode not in MODES:
        raise PlanError(f"Plan mode must be one of {', '.join(MODES)}.")
    if plan.seed_policy not in (None, *SEED_POLICIES):
        raise PlanError("Seed policy must be fixed or increment.")
    if plan.mode == "seed":
        if plan.dimensions:
            raise PlanError("A seed plan takes no dimensions; use pair or cross.")
        if plan.seed_policy == "fixed":
            raise PlanError("A seed plan always increments the seed.")
        if type(plan.count) is not int or not 1 <= plan.count <= MAX_QUEUE_ITEMS:
            raise PlanError(f"count must be an integer in 1..{MAX_QUEUE_ITEMS}.")
        return plan.count
    if plan.count is not None:
        raise PlanError("count applies to seed plans only.")
    if not plan.dimensions:
        raise PlanError(f"A {plan.mode} plan needs at least one dimension.")
    names = [name for name, _ in plan.dimensions]
    for name in names:
        if name not in (*SCALARS, LORA_SETS) and not (name.startswith(LORA_WEIGHT) and name != LORA_WEIGHT):
            raise PlanError(f"Unknown plan dimension {name!r}.")
    if len(set(names)) != len(names):
        raise PlanError("A dimension may be listed only once.")
    if LORA_SETS in names and any(name.startswith(LORA_WEIGHT) for name in names):
        raise PlanError("LoRA sets already carry their weights; list loras or lora_weight, not both.")
    if "seed" in names and plan.seed_policy == "increment":
        raise PlanError("Seed cannot be both a listed dimension and incremented.")
    lengths = []
    deferred = False
    for name, values in plan.dimensions:
        if isinstance(values, RandomSpec):
            _check_spec(name, values)
            lengths.append(values.count)
        elif isinstance(values, PromptVariation):
            if name != "prompt":
                raise PlanError("Only the prompt dimension takes prompt variation.")
            _check_variation(values)
            total = _variation_total(values, prompt, bound)
            deferred = deferred or total is None
            lengths.append(1 if total is None else total)
        else:
            lengths.append(len(values))
    if 0 in lengths:
        raise PlanError(f"Dimension {names[lengths.index(0)]!r} has no values.")
    if deferred:  # an every-combination prompt whose size depends on wildcard files: checked once they are bound
        return max(lengths) if plan.mode == "pair" else prod(lengths)
    if plan.mode == "pair":
        if len(set(lengths)) != 1:
            detail = ", ".join(f"{name}={length}" for name, length in zip(names, lengths, strict=True))
            raise PlanError(f"Pair dimensions must have equal lengths ({detail}).")
        return lengths[0]
    return prod(lengths)


def _check_duplicates(plan: Plan) -> None:
    """Reject values that would create identical requests (not applicable when each item gets its own seed)."""
    if plan.effective_seed_policy == "increment":
        return
    try:
        if plan.mode == "cross":
            for name, values in plan.dimensions:
                if len(set(values)) != len(values):
                    raise PlanError(f"Dimension {name!r} lists a value more than once; items would be identical.")
        else:
            rows = list(zip(*(values for _, values in plan.dimensions), strict=True))
            if len(set(rows)) != len(rows):
                raise PlanError("Two paired rows are identical; items would be identical.")
    except TypeError as exc:
        raise PlanError("Dimension values must be text, numbers or [width, height] pairs.") from exc


def _size(value: Any) -> tuple[int, int]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise PlanError("A size is a [width, height] pair.")
    return value


def lora_set(value: Any) -> tuple[LoRASelection, ...]:
    """One ``loras`` dimension value as selections: a sequence of (identifier, weight) pairs, empty for no LoRA."""
    if not isinstance(value, tuple) or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in value):
        raise PlanError("A LoRA set is a list of {identifier, weight} entries (empty for no LoRA).")
    selections = tuple(LoRASelection(identifier, weight) for identifier, weight in value)
    if len({item.identifier for item in selections}) != len(selections):
        raise PlanError("A LoRA set lists the same LoRA twice.")
    return selections


def lora_set_text(selections) -> str:
    """``none`` or ``style.ckpt 0.8 + detail.ckpt 0.4``: a LoRA set as the dry-run table and evidence show it."""
    return " + ".join(f"{item.identifier} {item.weight:g}" for item in selections) or "none"


def _set(loras: list, name: str, value: Any) -> dict[str, Any]:
    if name == LORA_SETS:
        loras[:] = lora_set(value)
        return {}
    if name in FIELD_NAMES:
        return {FIELD_NAMES[name]: value}
    if name == "sampler":
        try:
            return {"sampler_id": Sampler(value)}
        except ValueError as exc:
            raise PlanError("Unsupported sampler.") from exc
    if name == "size":
        width, height = _size(value)
        return {"width_px": width, "height_px": height}
    identifier = name[len(LORA_WEIGHT) :]
    for index, item in enumerate(loras):
        if item.identifier == identifier:
            loras[index] = replace(item, weight=value)
            return {}
    raise PlanError(f"Plan varies LoRA {identifier!r}, which is not in the base request.")


def _read(request: ResolvedGenerationRequest, name: str) -> str:
    if name == "strength" and request.operation == "txt2img":  # dry run of an edit that is not staged yet
        return dict(request.resolution_evidence)["plan.dimension.strength"]
    if name == "size":
        return f"{request.width_px}x{request.height_px}"
    if name == "sampler":
        return request.sampler_id.value
    if name in FIELD_NAMES:
        return str(getattr(request, FIELD_NAMES[name]))
    if name == LORA_SETS:
        return lora_set_text(request.loras)
    identifier = name[len(LORA_WEIGHT) :]
    return str(next(item.weight for item in request.loras if item.identifier == identifier))


def _combinations(plan: Plan, total: int):
    columns = [values for _, values in plan.dimensions]
    if plan.mode == "pair":
        return zip(*columns, strict=True)
    # Product with the last dimension fastest; the size was bounded, so this never builds more than ``total`` tuples.
    combos: list[tuple] = [()]
    for values in columns:
        combos = [(*prefix, value) for prefix in combos for value in values]
    return combos


def expand_plan(
    base: ResolvedGenerationRequest,
    plan: Plan,
    *,
    edit: bool = False,
    receipts: tuple[PreparedAsset, ...] = (),
) -> list[ResolvedGenerationRequest]:
    """Ordered, fully resolved requests for the plan; every failure is raised before anything is enqueued.

    ``edit`` previews a staged edit from its text-to-image settings base (dry run): strength is validated and shown
    in the evidence only; the real items carry it once the edit is staged. ``receipts`` are confirmed receipts for
    LoRAs named only in LoRA sets; with the base request's own, each item carries those of the LoRAs it selects.
    """
    plan = resolve_plan(plan, base.prompt)
    varied = {name for name, _ in plan.dimensions}
    preview = edit and base.operation == "txt2img"
    if base.operation == "txt2img" and not edit and "strength" in varied:
        raise PlanError("Strength applies to staged image edits only.")
    if (edit or base.operation != "txt2img") and "size" in varied:
        raise PlanError("A staged edit is fitted to one size; size cannot vary.")
    total = plan_size(plan)
    if total > MAX_QUEUE_ITEMS:
        raise PlanError(f"The plan would create {total} items; one queue holds at most {MAX_QUEUE_ITEMS}.")
    policy = plan.effective_seed_policy
    if policy == "increment" and base.seed + total - 1 > SEED_LIMIT:
        raise PlanError("Item seeds would exceed the 32-bit seed range.")
    if plan.mode == "seed":
        return [
            replace(
                base,
                request_id=uuid4().hex,
                seed=base.seed + index,
                resolution_evidence=(*base.resolution_evidence, ("queue_position", str(index + 1))),
            )
            for index in range(total)
        ]
    _check_duplicates(plan)
    pool = {asset.identifier: asset for asset in (*receipts, *base.prepared_assets)}
    # Receipt evidence is per item when LoRA sets vary: an item claims only the receipts it carries.
    base_evidence = tuple(
        pair for pair in base.resolution_evidence if not (LORA_SETS in varied and pair[0] == "prepared_assets")
    )
    items = []
    for index, combo in enumerate(_combinations(plan, total)):
        loras = list(base.loras)
        changes: dict[str, Any] = {}
        evidence = [("plan.index", str(index + 1)), ("plan.mode", plan.mode), ("plan.seed", policy), *plan.evidence]
        try:
            for (name, _), value in zip(plan.dimensions, combo, strict=True):
                changes.update(_set(loras, name, value))
                if preview and name == "strength":
                    if type(value) not in (int, float) or not 0 <= value <= 1:
                        raise PlanError("Strength must be a number in 0..1.")
                    changes.pop("strength")
                text = f"{value[0]}x{value[1]}" if name == "size" else str(value)
                evidence.append((f"plan.dimension.{name}", lora_set_text(loras) if name == LORA_SETS else text))
            if LORA_SETS in varied:
                # Each item carries the confirmed receipts of the LoRAs it selects; nothing is inferred for others.
                bound = tuple(pool[item.identifier] for item in loras if item.identifier in pool)
                changes["prepared_assets"] = bound
                if bound:
                    evidence.append(("prepared_assets", ",".join(asset.receipt_id for asset in bound)))
            if policy == "increment":
                changes["seed"] = base.seed + index
            items.append(
                replace(
                    base,
                    request_id=uuid4().hex,
                    loras=tuple(loras),
                    resolution_evidence=(*base_evidence, *evidence),
                    **changes,
                )
            )
        except PlanError:
            raise
        except ValidationError as exc:
            raise PlanError(f"Item {index + 1}: {exc}") from exc
    return items


def describe_plan(base: ResolvedGenerationRequest, plan: Plan, *, edit: bool = False) -> PlanTable:
    """The dry-run table: the exact items ``expand_plan`` would produce, as plain rows (nothing is persisted)."""
    plan = resolve_plan(plan, base.prompt)
    items = expand_plan(base, plan, edit=edit)
    varied = [name for name, _ in plan.dimensions]
    columns = ("#", *varied, *(() if "seed" in varied else ("seed",)))
    rows = tuple(
        (
            str(index),
            *(_read(item, name) for name in varied),
            *(() if "seed" in varied else (str(item.seed),)),
        )
        for index, item in enumerate(items, 1)
    )
    return PlanTable(plan.mode, plan.effective_seed_policy, len(items), columns, rows, plan.rng_seed)


def _lora_sets(value: Any) -> tuple[tuple[tuple[str, Any], ...], ...]:
    if not isinstance(value, list):
        raise PlanError("loras must be a list of LoRA sets.")
    sets = []
    for entry in value:
        if not isinstance(entry, list) or any(
            not isinstance(item, dict) or set(item) != {"identifier", "weight"} for item in entry
        ):
            raise PlanError("Each LoRA set is a list of {identifier, weight} entries (empty for no LoRA).")
        sets.append(tuple((item["identifier"], item["weight"]) for item in entry))
    return tuple(sets)


def _random(name: str, raw: dict) -> RandomSpec:
    """``{"random": {"count": N, "from": [...]}}`` or ``{"count": N, "min": a, "max": b, "step": s}`` as a spec."""
    if not isinstance(raw, dict) or set(raw) - {"count", "from", "min", "max", "step"}:
        raise PlanError("A random dimension takes count plus from (a list) or min, max and step.")
    count = raw.get("count")
    if "from" in raw:
        if set(raw) != {"count", "from"} or not isinstance(raw["from"], list):
            raise PlanError("A random pick takes exactly count and from (a list).")
        candidates = _lora_sets(raw["from"]) if name == LORA_SETS else tuple(raw["from"])
        return RandomSpec(count, candidates=candidates)
    if {"min", "max", "step"} & set(raw):
        if set(raw) != {"count", "min", "max", "step"}:
            raise PlanError("A random range takes exactly count, min, max and step.")
        return RandomSpec(count, low=raw["min"], high=raw["max"], step=raw["step"], integer=name == "steps")
    if set(raw) != {"count"}:
        raise PlanError("A random dimension takes count plus from (a list) or min, max and step.")
    return RandomSpec(count)


def _variation(raw: Any) -> PromptVariation:
    keys = {"strategy", "count", "template", "wildcards", "shuffle", "drop", "add", "add_max"}
    if not isinstance(raw, dict) or set(raw) - keys:
        raise PlanError("Prompt variation takes strategy, count, template and wildcards.")
    wildcards = raw.get("wildcards", {})
    if not isinstance(wildcards, dict) or any(
        not isinstance(entries, list) or any(not isinstance(e, str) for e in entries) for entries in wildcards.values()
    ):
        raise PlanError("Wildcards are an object of name to a list of text entries.")
    template = raw.get("template")
    if template is not None and not isinstance(template, str):
        raise PlanError("A prompt template is text.")
    extra = raw.get("add", [])
    if not isinstance(extra, list) or any(not isinstance(part, str) for part in extra):
        raise PlanError("Parts to add are a list of text.")
    return PromptVariation(
        raw.get("strategy", "random"),
        raw.get("count"),
        template,
        tuple((name, tuple(entries)) for name, entries in wildcards.items()),
        shuffle=raw.get("shuffle"),
        drop=raw.get("drop", 0),
        add=tuple(extra),
        add_max=raw.get("add_max", 1 if extra else 0),
    )


def _dimension(name: str, value: Any, version: int = VERSION) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    if isinstance(value, Mapping) and set(value) == {"random"}:
        if version < 2:
            raise PlanError("Random dimensions need plan version 2.")
        if name == "lora_weight":
            raise PlanError("A random lora_weight sets random weights per LoRA entry.")
        return ((name, _random(name, value["random"])),)
    if name == "prompt" and isinstance(value, Mapping) and set(value) == {"variation"}:
        if version < 2:
            raise PlanError("Prompt variation needs plan version 2.")
        return ((name, _variation(value["variation"])),)
    if name == "seed" and isinstance(value, Mapping):
        if set(value) != {"start", "count"}:
            raise PlanError("A seed range needs exactly start and count.")
        return (("seed", seed_range(value["start"], value["count"])),)
    if name == "lora_weight":
        if not isinstance(value, list):
            raise PlanError("lora_weight must be a list of {identifier, weights}.")
        entries = []
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"identifier", "weights"}:
                raise PlanError("Each lora_weight entry needs exactly an identifier and weights.")
            weights = entry["weights"]
            if not isinstance(entry["identifier"], str):
                raise PlanError("lora_weight needs a text identifier and a list of weights.")
            if isinstance(weights, dict) and set(weights) == {"random"} and version >= 2:
                entries.append((LORA_WEIGHT + entry["identifier"], _random("lora_weight", weights["random"])))
                continue
            if not isinstance(weights, list):
                raise PlanError("lora_weight needs a text identifier and a list of weights.")
            entries.append((LORA_WEIGHT + entry["identifier"], tuple(weights)))
        return tuple(entries)
    if name == LORA_SETS:
        return ((LORA_SETS, _lora_sets(value)),)
    if name not in SCALARS:
        raise PlanError(f"Unknown plan dimension {name!r}.")
    if not isinstance(value, list):
        raise PlanError(f"Dimension {name!r} must be a list of values.")
    return ((name, tuple(value)),)


def parse_plan(document: Any) -> Plan:
    """Validate one parsed plan document (``dtflow.queue-plan`` version 1 or 2) and return its ``Plan``."""
    if not isinstance(document, dict):
        raise PlanError("Plan must be a JSON object.")
    try:
        reject_credentials(document)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    if document.get("schema") != SCHEMA:
        raise PlanError(f"Unsupported plan schema (expected {SCHEMA}).")
    version = document.get("version")
    if type(version) is not int or version not in READABLE:
        raise PlanError(f"Unsupported plan version {version!r}; this build reads versions {READABLE[0]} to {VERSION}.")
    unknown = sorted(set(document) - {"schema", "version", "mode", "count", "seed", "dimensions", "rng_seed"})
    if unknown:
        raise PlanError("Unknown plan field(s): " + ", ".join(unknown))
    raw = document.get("dimensions", {})
    if not isinstance(raw, dict):
        raise PlanError("dimensions must be an object.")
    dimensions: list[tuple[str, tuple[Any, ...]]] = []
    for name, value in raw.items():
        dimensions.extend(_dimension(name, value, version))
    rng_seed = document.get("rng_seed")
    if rng_seed is not None and (version < 2 or type(rng_seed) is not int or not 0 <= rng_seed <= SEED_LIMIT):
        raise PlanError("rng_seed is a whole number in 0..4294967295 (plan version 2).")
    plan = Plan(document.get("mode"), tuple(dimensions), document.get("count"), document.get("seed"), rng_seed)
    plan_size(plan, bound=False)  # wildcard files are bound after parsing, so their counts wait for ``expand_plan``
    return plan


def load_plan(path: Path) -> Plan:
    return parse_plan(read_json_file(path, "Plan"))

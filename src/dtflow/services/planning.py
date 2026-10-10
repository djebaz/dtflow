"""Bounded, deterministic queue planning: a base request plus varied dimensions become an ordered request list.

Pure and Qt-, SQLite- and network-free. The planner emits ordinary ``ResolvedGenerationRequest`` values through the same
validated construction used for single requests, so scheduling, reservations, restart and no-replay stay with the queue.
The item count is computed arithmetically and bounded before anything is enumerated. Nothing here infers LoRA or model
compatibility; listed identifiers and weights are forwarded and left to preflight and receipt binding at creation.

Modes: ``seed`` (count items, seed incrementing from the base seed: the original ``queue add -k`` rule), ``pair`` (zip of
equal-length dimensions) and ``cross`` (product; the last listed dimension varies fastest, like nested loops). In pair and
cross the base seed stays fixed for every item unless a seed dimension is listed or the plan says ``seed: increment``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import prod
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import MAX_QUEUE_ITEMS, SEED_LIMIT, ResolvedGenerationRequest, Sampler, ValidationError
from dtflow.services.config import ConfigError, read_json_file, reject_credentials

SCHEMA = "dtflow.queue-plan"
VERSION = 1
MODES = ("seed", "pair", "cross")
SEED_POLICIES = ("fixed", "increment")
SCALARS = ("prompt", "model", "seed", "steps", "guidance", "sampler", "size", "strength")
LORA_WEIGHT = "lora_weight."  # dimension name prefix; the rest is the exact LoRA identifier from the base request
FIELD_NAMES = {
    "prompt": "prompt",
    "model": "model_id",
    "seed": "seed",
    "steps": "steps",
    "guidance": "guidance",
    "strength": "strength",
}


class PlanError(ConfigError):
    """Unreadable, unsupported or invalid plan; nothing was enqueued."""


@dataclass(frozen=True, slots=True)
class Plan:
    mode: str
    dimensions: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    count: int | None = None
    seed_policy: str | None = None  # None: ``seed`` mode increments, pair/cross keep the base seed

    def __post_init__(self) -> None:
        # Lists (from JSON or widgets) become tuples so values are hashable and the plan is immutable.
        normal = tuple(
            (name, tuple(tuple(v) if isinstance(v, list) else v for v in values)) for name, values in self.dimensions
        )
        object.__setattr__(self, "dimensions", normal)

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


def seed_range(start: int, count: int) -> tuple[int, ...]:
    """Consecutive seeds ``start..start+count-1`` as an explicit seed dimension, bounded before it is built."""
    if type(start) is not int or type(count) is not int or not 1 <= count <= MAX_QUEUE_ITEMS:
        raise PlanError(f"A seed range needs an integer start and a count in 1..{MAX_QUEUE_ITEMS}.")
    if start < 0 or start + count - 1 > SEED_LIMIT:
        raise PlanError("Seed range would exceed the 32-bit seed range.")
    return tuple(range(start, start + count))


def plan_size(plan: Plan) -> int:
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
        if name not in SCALARS and not (name.startswith(LORA_WEIGHT) and name != LORA_WEIGHT):
            raise PlanError(f"Unknown plan dimension {name!r}.")
    if len(set(names)) != len(names):
        raise PlanError("A dimension may be listed only once.")
    if "seed" in names and plan.seed_policy == "increment":
        raise PlanError("Seed cannot be both a listed dimension and incremented.")
    lengths = [len(values) for _, values in plan.dimensions]
    if 0 in lengths:
        raise PlanError(f"Dimension {names[lengths.index(0)]!r} has no values.")
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


def _set(loras: list, name: str, value: Any) -> dict[str, Any]:
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
    if name == "size":
        return f"{request.width_px}x{request.height_px}"
    if name == "sampler":
        return request.sampler_id.value
    if name in FIELD_NAMES:
        return str(getattr(request, FIELD_NAMES[name]))
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


def expand_plan(base: ResolvedGenerationRequest, plan: Plan) -> list[ResolvedGenerationRequest]:
    """Ordered, fully resolved requests for the plan; every failure is raised before anything is enqueued."""
    varied = {name for name, _ in plan.dimensions}
    if base.operation == "txt2img" and "strength" in varied:
        raise PlanError("Strength applies to staged image edits only.")
    if base.operation != "txt2img" and "size" in varied:
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
    items = []
    for index, combo in enumerate(_combinations(plan, total)):
        loras = list(base.loras)
        changes: dict[str, Any] = {}
        evidence = [("plan.index", str(index + 1)), ("plan.mode", plan.mode), ("plan.seed", policy)]
        try:
            for (name, _), value in zip(plan.dimensions, combo, strict=True):
                changes.update(_set(loras, name, value))
                evidence.append((f"plan.dimension.{name}", f"{value[0]}x{value[1]}" if name == "size" else str(value)))
            if policy == "increment":
                changes["seed"] = base.seed + index
            items.append(
                replace(
                    base,
                    request_id=uuid4().hex,
                    loras=tuple(loras),
                    resolution_evidence=(*base.resolution_evidence, *evidence),
                    **changes,
                )
            )
        except PlanError:
            raise
        except ValidationError as exc:
            raise PlanError(f"Item {index + 1}: {exc}") from exc
    return items


def describe_plan(base: ResolvedGenerationRequest, plan: Plan) -> PlanTable:
    """The dry-run table: the exact items ``expand_plan`` would produce, as plain rows (nothing is persisted)."""
    items = expand_plan(base, plan)
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
    return PlanTable(plan.mode, plan.effective_seed_policy, len(items), columns, rows)


def _dimension(name: str, value: Any) -> tuple[tuple[str, tuple[Any, ...]], ...]:
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
            if not isinstance(entry["identifier"], str) or not isinstance(entry["weights"], list):
                raise PlanError("lora_weight needs a text identifier and a list of weights.")
            entries.append((LORA_WEIGHT + entry["identifier"], tuple(entry["weights"])))
        return tuple(entries)
    if name not in SCALARS:
        raise PlanError(f"Unknown plan dimension {name!r}.")
    if not isinstance(value, list):
        raise PlanError(f"Dimension {name!r} must be a list of values.")
    return ((name, tuple(value)),)


def parse_plan(document: Any) -> Plan:
    """Validate one parsed plan document (``dtflow.queue-plan`` version 1) and return its ``Plan``."""
    if not isinstance(document, dict):
        raise PlanError("Plan must be a JSON object.")
    try:
        reject_credentials(document)
    except ConfigError as exc:
        raise PlanError(str(exc)) from exc
    if document.get("schema") != SCHEMA:
        raise PlanError(f"Unsupported plan schema (expected {SCHEMA}).")
    version = document.get("version")
    if type(version) is not int or version != VERSION:
        raise PlanError(f"Unsupported plan version {version!r}; this build reads version {VERSION}.")
    unknown = sorted(set(document) - {"schema", "version", "mode", "count", "seed", "dimensions"})
    if unknown:
        raise PlanError("Unknown plan field(s): " + ", ".join(unknown))
    raw = document.get("dimensions", {})
    if not isinstance(raw, dict):
        raise PlanError("dimensions must be an object.")
    dimensions: list[tuple[str, tuple[Any, ...]]] = []
    for name, value in raw.items():
        dimensions.extend(_dimension(name, value))
    plan = Plan(document.get("mode"), tuple(dimensions), document.get("count"), document.get("seed"))
    plan_size(plan)
    return plan


def load_plan(path: Path) -> Plan:
    return parse_plan(read_json_file(path, "Plan"))

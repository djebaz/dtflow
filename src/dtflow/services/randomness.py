"""Seeded random draws for plan dimensions (#128): pure, deterministic and bounded.

A ``RandomSpec`` stands where a dimension's value list would be. ``draw`` turns it into that list from one recorded plan
RNG seed, so the same seed and inputs always expand to the same items. Draws are distinct (a list pick or a range grid
is sampled without replacement), so a random dimension never trips the planner's duplicate rule by itself.
"""

import random
from dataclasses import dataclass
from math import floor
from typing import Any

from dtflow.core.models import MAX_QUEUE_ITEMS, SEED_LIMIT
from dtflow.services.plan_errors import PlanError

GRID_LIMIT = 10_000_000  # a numeric range is a lazy grid; this only bounds a nonsensical min/max/step
STREAM = "dtflow-plan"


@dataclass(frozen=True, slots=True)
class RandomSpec:
    """``count`` distinct random values: from ``candidates``, from a min/max/step grid, or random seeds (neither)."""

    count: int
    candidates: tuple[Any, ...] | None = None
    low: float | None = None
    high: float | None = None
    step: float | None = None
    integer: bool = False  # the grid holds whole numbers (steps)

    def __post_init__(self) -> None:
        if self.candidates is not None:
            object.__setattr__(
                self, "candidates", tuple(tuple(v) if isinstance(v, list) else v for v in self.candidates)
            )

    @property
    def kind(self) -> str:
        if self.candidates is not None:
            return "pick"
        return "seeds" if self.low is None else "range"

    def describe(self) -> str:
        """The evidence text for this spec, such as ``pick 3 of 5`` or ``4 from 0.4 to 1.2 step 0.1``."""
        if self.kind == "pick":
            return f"pick {self.count} of {len(dict.fromkeys(self.candidates or ()))}"
        if self.kind == "seeds":
            return f"{self.count} random seeds"
        return f"{self.count} from {self.low:g} to {self.high:g} step {self.step:g}"


def _grid_size(spec: RandomSpec) -> int:
    return floor((spec.high - spec.low) / spec.step + 1e-9) + 1


def check(name: str, spec: RandomSpec) -> None:
    """Reject a spec that cannot yield ``count`` distinct values; ``name`` only words the message."""
    if type(spec.count) is not int or not 1 <= spec.count <= MAX_QUEUE_ITEMS:
        raise PlanError(f"Dimension {name!r}: a random count is an integer in 1..{MAX_QUEUE_ITEMS}.")
    if spec.kind == "pick":
        unique = len(dict.fromkeys(spec.candidates))
        if unique == 0:
            raise PlanError(f"Dimension {name!r} has no values to pick from.")
        if spec.count > unique:
            raise PlanError(f"Dimension {name!r}: picking {spec.count} needs at least {spec.count} different values.")
    elif spec.kind == "range":
        numbers = (spec.low, spec.high, spec.step)
        if any(type(n) not in (int, float) or n != n or abs(n) == float("inf") for n in numbers):
            raise PlanError(f"Dimension {name!r}: a random range needs numbers for min, max and step.")
        if spec.integer and any(type(n) is not int for n in numbers):
            raise PlanError(f"Dimension {name!r}: a random range of whole numbers needs whole min, max and step.")
        if spec.step <= 0 or spec.high < spec.low:
            raise PlanError(f"Dimension {name!r}: a random range needs min <= max and a positive step.")
        if (spec.high - spec.low) / spec.step + 1 > GRID_LIMIT:
            raise PlanError(f"Dimension {name!r}: the random range step is too small for its span.")
        if spec.count > _grid_size(spec):
            raise PlanError(
                f"Dimension {name!r}: the range has only {_grid_size(spec)} different values, fewer than {spec.count}."
            )
    elif name != "seed":
        raise PlanError(f"Dimension {name!r} needs candidates or a min, max and step to draw from.")


def draw(name: str, spec: RandomSpec, rng_seed: int) -> tuple[Any, ...]:
    """The drawn values, in drawing order. One stream per dimension, so listing another dimension changes nothing."""
    check(name, spec)
    rng = random.Random(f"{STREAM}/{rng_seed}/{name}")  # str seeding is stable across runs and Python versions
    if spec.kind == "pick":
        return tuple(rng.sample(tuple(dict.fromkeys(spec.candidates)), spec.count))
    if spec.kind == "seeds":
        return tuple(rng.sample(range(SEED_LIMIT + 1), spec.count))
    values = []
    for index in rng.sample(range(_grid_size(spec)), spec.count):
        value = spec.low + index * spec.step
        values.append(int(value) if spec.integer else round(value, 10))
    return tuple(values)

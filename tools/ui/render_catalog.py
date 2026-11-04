"""Qt-free catalogue and selection for offline UI captures.

The checked-in catalogue is authoring metadata, not a record of accepted renders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

CATEGORY_ORDER = ("shell", "connection", "generate", "inpaint", "queues", "scripts", "images", "logs")
STAGE_ORDER = ("empty", "setup", "ready", "running", "results", "failure", "recovery", "layout")
CATALOG_PATH = Path(__file__).with_suffix(".json")

# Window groups that can run as separate processes, each with its own temp state, fake cloud and scenario.
# Every fixture not listed here belongs to the "workspace" window (connection, generate configuration, queue
# read-views, gallery composition).
WORKER_FIXTURES = {
    "main": ("generation-flow", "queue", "shell", "inpaint", "logs"),
    "scripts": ("scripts",),
    "gallery": ("gallery-composition",),
    "images-windows": ("mapped-gallery", "trash-unavailable", "initial-refresh-recovery"),
    "splash": ("startup-splash",),
}
WORKER_ORDER = (
    "workspace",
    "gallery",
    "main",
    "scripts",
    "images-windows",
    "splash",
)  # also the order they run in one process
# Rough seconds of render work: (window build + setup, per selected state). Used only to balance processes.
WORKER_COST = {
    "workspace": (3.0, 0.9),
    "main": (5.0, 0.9),
    "scripts": (2.0, 1.3),
    "gallery": (3.0, 0.9),
    "images-windows": (3.0, 0.9),
    "splash": (0.1, 0.1),
}


def plan_processes(selection, jobs):
    """Pack the window groups of this selection into at most `jobs` processes, balanced by estimated cost.

    Each process pays Python and Qt startup once, so a trivial group (the splash) never gets a process of its own.
    Returns labels such as "workspace" or "main+scripts+splash" (groups joined in their run order)."""
    counts = {}
    for state in selection.states:
        counts[worker_of(state.fixture)] = counts.get(worker_of(state.fixture), 0) + 1
    cost = {name: WORKER_COST[name][0] + WORKER_COST[name][1] * count for name, count in counts.items()}
    heavy = sorted((n for n in cost if cost[n] >= 2.0), key=lambda n: -cost[n])
    light = [n for n in cost if cost[n] < 2.0]
    bins = [[] for _ in range(max(1, min(jobs, len(heavy))))]
    loads = [0.0] * len(bins)
    for name in heavy + light:
        index = loads.index(min(loads))
        bins[index].append(name)
        loads[index] += cost[name]
    return ["+".join(sorted(group, key=WORKER_ORDER.index)) for group in bins if group]


def worker_of(fixture):
    return next((name for name, fixtures in WORKER_FIXTURES.items() if fixture in fixtures), "workspace")


LEVEL_ORDER = ("f", "m", "s")  # fast ⊂ medium ⊂ slow (every state)


@dataclass(frozen=True)
class State:
    id: str
    category: str
    subcategory: str
    fixture: str
    stage: str
    path: str
    level: str = "s"

    @property
    def group(self):
        return f"{self.category}/{self.subcategory}"


def load_catalog(path=CATALOG_PATH):
    data = json.loads(path.read_text(encoding="utf-8"))
    states = tuple(State(**row) for row in data["states"])
    ids, paths = set(), set()
    for state in states:
        if state.id in ids or state.path in paths:
            raise ValueError(f"Duplicate render catalogue identity/path: {state.id}")
        if state.level not in LEVEL_ORDER:
            raise ValueError(f"Unknown render level: {state.id}")
        if state.category not in CATEGORY_ORDER or state.stage not in STAGE_ORDER:
            raise ValueError(f"Unknown render category/stage: {state.id}")
        relative = Path(state.path)
        if relative.is_absolute() or ".." in relative.parts or relative.stem != state.id:
            raise ValueError(f"Invalid render output path: {state.path}")
        ids.add(state.id)
        paths.add(state.path)
    return states


class Selection:
    def __init__(self, catalog, *, category="all", groups=(), states=(), prefixes=(), level="s", worker=None):
        self.catalog_by_id = {state.id: state for state in catalog}
        ids = {state.id for state in catalog}
        available_groups = {state.group for state in catalog}
        for value in states:
            if value not in ids:
                raise ValueError(f"Unknown render state: {value}; use --list.")
        for value in groups:
            if value not in available_groups:
                raise ValueError(f"Unknown render group: {value}; use --list.")
        for prefix in prefixes:
            if not any(state.id.startswith(prefix) for state in catalog):
                raise ValueError(f"Render prefix matches no states: {prefix}")
        explicit = self.explicit = bool(groups or states or prefixes)
        self.level = level
        reach = LEVEL_ORDER[: LEVEL_ORDER.index(level) + 1]
        self.states = tuple(
            state
            for state in catalog
            if (
                category == "all"
                or state.category == category
                or category == "image-navigation"
                and state.subcategory == "navigation"
                and state.category == "images"
            )
            and state.level in reach
            and (worker is None or worker_of(state.fixture) in worker)
            and (
                not explicit
                or state.id in states
                or state.group in groups
                or any(state.id.startswith(prefix) for prefix in prefixes)
            )
        )
        if not self.states:
            raise ValueError("The combined selectors match no render states.")
        self.by_id = {state.id: state for state in self.states}
        self.fixtures = frozenset(state.fixture for state in self.states)
        self.categories = tuple(
            category for category in CATEGORY_ORDER if any(s.category == category for s in self.states)
        )

    def wants_fixture(self, name, category=None):
        return bool(self.fixture_ids(name, category))

    def fixture_ids(self, name, category=None):
        return frozenset(
            s.id for s in self.states if s.fixture == name and (category is None or s.category == category)
        )


def parse_inputs(root, argv=None):
    parser = argparse.ArgumentParser(
        description="Select and render real Qt widgets with offline fixtures; no providers."
    )
    parser.add_argument("-o", "--out", type=Path, default=root / "tmp/ui-renders", help="Screenshot/report directory")
    parser.add_argument("-c", "--category", choices=("all", *CATEGORY_ORDER, "image-navigation"), default="all")
    parser.add_argument("-g", "--group", action="append", default=[], help="Select category/subcategory; repeatable")
    parser.add_argument("--state", action="append", default=[], help="Select an exact stable state ID; repeatable")
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=0,
        help="Worker processes at once (default: one per core, 2 to 4; 1 = one process)",
    )
    parser.add_argument("--worker", help=argparse.SUPPRESS)  # "a+b": window groups rendered by this process
    levels = parser.add_mutually_exclusive_group()
    levels.add_argument("-f", "--fast", dest="level", action="store_const", const="f", help="Key states only (default)")
    levels.add_argument(
        "-m", "--medium", dest="level", action="store_const", const="m", help="Fast plus common variants"
    )
    levels.add_argument("-s", "--slow", dest="level", action="store_const", const="s", help="Every state")
    parser.add_argument(
        "-p", "--prefix", action="append", default=[], help="Select state IDs by literal prefix; repeatable"
    )
    parser.add_argument(
        "-l", "--list", action="store_true", help="List the selected catalogue, without Qt or rendering"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Also print detailed fixture/platform diagnostics")
    args = parser.parse_args(argv)
    # Explicit group/state/prefix selectors name what to render, so they reach every level unless one is given.
    explicit = bool(args.group or args.state or args.prefix)
    args.level = args.level or ("s" if explicit else "f")
    try:
        catalog = load_catalog()
        args.selection = Selection(
            catalog,
            category=args.category,
            groups=args.group,
            states=args.state,
            prefixes=args.prefix,
            level=args.level,
            worker=args.worker and tuple(args.worker.split("+")),
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    selectors = sorted(
        [*(f"g:{v}" for v in args.group), *(f"s:{v}" for v in args.state), *(f"p:{v}" for v in args.prefix)]
    )
    # Concurrent runs with different selectors must not share a log or manifest.
    digest = hashlib.sha1("\n".join(selectors).encode()).hexdigest()[:8] if selectors else ""
    args.report_key = f"{args.category}-{digest}" if digest else args.category
    if args.level != "s":
        args.report_key += f"-{args.level}"
    # The parent plans workers from the whole selection; a worker's own selection is already narrowed to its group.
    args.jobs = args.jobs if args.jobs > 0 else max(2, min(4, os.cpu_count() or 2))
    args.workers = plan_processes(args.selection, args.jobs) if args.worker is None and not args.list else []
    if args.jobs == 1 or len(args.workers) < 2:
        args.workers = []
    if args.worker:
        args.report_key += f"-{args.worker}"
    if args.list:
        for index, state in enumerate(args.selection.states, 1):
            print(
                f"{index:3}  {state.level} {state.group:30} {state.stage:8} {state.id}  [fixture: {state.fixture}, worker: {worker_of(state.fixture)}]"
            )
        print(f"Selected {len(args.selection.states)} states in {len(args.selection.fixtures)} fixture groups.")
        parser.exit()
    return args

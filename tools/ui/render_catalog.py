"""Qt-free catalogue and selection for offline UI captures.

The checked-in catalogue is authoring metadata, not a record of accepted renders.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

CATEGORY_ORDER = ("shell", "connection", "generate", "inpaint", "queues", "scripts", "images", "logs")
STAGE_ORDER = ("empty", "setup", "ready", "running", "results", "failure", "recovery", "layout")
CATALOG_PATH = Path(__file__).with_suffix(".json")
FIXTURE_PREREQUISITES = {"removal-and-selection": ("mapped-gallery",)}


@dataclass(frozen=True)
class State:
    id: str
    category: str
    subcategory: str
    fixture: str
    stage: str
    path: str

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
        if state.category not in CATEGORY_ORDER or state.stage not in STAGE_ORDER:
            raise ValueError(f"Unknown render category/stage: {state.id}")
        relative = Path(state.path)
        if relative.is_absolute() or ".." in relative.parts or relative.stem != state.id:
            raise ValueError(f"Invalid render output path: {state.path}")
        ids.add(state.id)
        paths.add(state.path)
    return states


class Selection:
    def __init__(self, catalog, *, category="all", groups=(), states=(), prefixes=()):
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
        explicit = bool(groups or states or prefixes)
        self.states = tuple(
            state
            for state in catalog
            if (
                category == "all"
                or state.category == category
                or category == "image-navigation" and state.subcategory == "navigation" and state.category == "images"
            )
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
        self.categories = tuple(category for category in CATEGORY_ORDER if any(s.category == category for s in self.states))

    def wants_fixture(self, name, category=None):
        return bool(self.fixture_ids(name, category))

    def fixture_ids(self, name, category=None):
        return frozenset(
            s.id
            for s in self.states
            if (s.fixture == name or name in FIXTURE_PREREQUISITES.get(s.fixture, ()))
            and (category is None or s.category == category)
        )


def parse_inputs(root, argv=None):
    parser = argparse.ArgumentParser(description="Select and render real Qt widgets with offline fixtures; no providers.")
    parser.add_argument("-o", "--out", type=Path, default=root / "tmp/ui-renders", help="Screenshot/report directory")
    parser.add_argument("-c", "--category", choices=("all", *CATEGORY_ORDER, "image-navigation"), default="all")
    parser.add_argument("-g", "--group", action="append", default=[], help="Select category/subcategory; repeatable")
    parser.add_argument("-s", "--state", action="append", default=[], help="Select an exact stable state ID; repeatable")
    parser.add_argument("-p", "--prefix", action="append", default=[], help="Select state IDs by literal prefix; repeatable")
    parser.add_argument("-l", "--list", action="store_true", help="List the selected catalogue, without Qt or rendering")
    parser.add_argument("-v", "--verbose", action="store_true", help="Also print detailed fixture/platform diagnostics")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog()
        args.selection = Selection(catalog, category=args.category, groups=args.group, states=args.state, prefixes=args.prefix)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.list:
        for index, state in enumerate(args.selection.states, 1):
            print(f"{index:3}  {state.group:30} {state.stage:8} {state.id}  [fixture: {state.fixture}]")
        print(f"Selected {len(args.selection.states)} states in {len(args.selection.fixtures)} fixture groups.")
        parser.exit()
    return args

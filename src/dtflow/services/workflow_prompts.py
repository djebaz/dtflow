"""Wildcard prompts of workflow nodes (#317): list snapshot, combination counts and the prompt of one branch.

A workflow run snapshots the wildcard lists its nodes reach when the run is created (name -> entries and the file's
SHA-256) and stores the snapshot in the run record. Planning, execution and Retry all read that snapshot, never the
folder again, so a list edited later cannot change a Retry. Drawing is pure and seeded: the same snapshot, node seed
and branch always give the same prompt, whatever order the branches finish in. Error messages name the node and the
wildcard, never the prompt text.
"""

from __future__ import annotations

import zlib
from pathlib import Path
from typing import Any

from dtflow.core.models import ValidationError
from dtflow.core.workflows import (
    GENERATE,
    MAX_PROMPT_COUNT,
    Branch,
    Graph,
    GraphNode,
    output_index,
    parent_branch,
    prompt_branches,
)
from dtflow.services import dynamic_prompts as dp
from dtflow.services.plan_errors import PlanError
from dtflow.services.wildcards import NAME_PATTERN, read_list

Snapshot = dict[str, Any]  # {"lists": {name: [entries]}, "sources": {name: sha256}}
EVIDENCE_TEMPLATE = "plan.prompt_template"
EVIDENCE_MODE = "plan.prompt_mode"
EVIDENCE_WILDCARD = "plan.wildcard."


def wildcard_nodes(graph: Graph) -> list[GraphNode]:
    return [node for node in graph.nodes if node.prompt_mode is not None]


def read_snapshot(graph: Graph, folder: Path | None) -> Snapshot | None:
    """The lists the wildcard nodes reach (including lists their entries name), read from ``folder``; None when no
    node uses a wildcard mode. A list that does not exist is left out: drawing then names it."""
    nodes = wildcard_nodes(graph)
    if not nodes:
        return None
    lists: dict[str, list[str]] = {}
    sources: dict[str, str] = {}
    pending = [name for node in nodes for name in NAME_PATTERN.findall(str(node.params["prompt"]))]
    while pending:
        name = pending.pop()
        if name in lists or folder is None:
            continue
        found = read_list(folder, name)
        if found is None:
            continue
        entries, sha = found
        lists[name], sources[name] = list(entries), sha
        pending.extend(inner for entry in entries for inner in NAME_PATTERN.findall(entry))
    return {"lists": lists, "sources": sources}


def _lists(snapshot: Snapshot | None) -> dict[str, tuple[str, ...]]:
    return {name: tuple(entries) for name, entries in ((snapshot or {}).get("lists") or {}).items()}


def _fail(node: GraphNode, exc: PlanError) -> ValidationError:
    return ValidationError(f"{node.label}: {exc}")


_DRAWS: dict[tuple, tuple[tuple[str, dp.Runs], ...]] = {}  # recent draws: planning asks again on every edit
_DRAWS_MAX = 64


def draw(node: GraphNode, snapshot: Snapshot | None, key: str = "") -> tuple[tuple[str, dp.Runs], ...]:
    """The node's prompts with their variation runs: ``prompt_count`` random distinct ones, or every combination.

    ``key`` names the incoming image (its Generate node and branch number), so each image's random draw is its own."""
    template = str(node.params["prompt"])
    lists = _lists(snapshot)
    memo = (
        template,
        node.prompt_mode,
        node.prompt_count,
        node.id,
        int(node.params["seed"]),
        key,
        tuple(sorted(lists.items())),
    )
    found = _DRAWS.get(memo)
    if found is not None:
        return found
    try:
        if node.prompt_mode == "all":
            total = dp.count(dp.parse(template), dp.Lists(lists))
            if total > MAX_PROMPT_COUNT:
                raise PlanError(
                    f"the prompt has {total} combinations; one node can make 1 to {MAX_PROMPT_COUNT}. "
                    "Use the random sample mode or simplify the prompt."
                )
            drawn, _ = dp.tagged_variants(template, lists, strategy="all", n=None, rng_seed=0, limit=MAX_PROMPT_COUNT)
        else:
            seed = zlib.crc32(f"{node.id}:{int(node.params['seed'])}:{key}".encode())
            drawn, _ = dp.tagged_variants(
                template, lists, strategy="random", n=node.prompt_count, rng_seed=seed, limit=MAX_PROMPT_COUNT
            )
    except PlanError as exc:
        raise _fail(node, exc) from exc
    if not drawn:
        raise ValidationError(f"{node.label}: the prompt resolves to nothing; keep some text outside the choices.")
    if len(_DRAWS) >= _DRAWS_MAX:
        _DRAWS.pop(next(iter(_DRAWS)))  # oldest first
    _DRAWS[memo] = drawn
    return drawn


def prompt_counts(graph: Graph, snapshot: Snapshot | None) -> dict[int, int]:
    """Combinations of each ``all`` node (the number planning needs); every wildcard node is also drawn once, so a
    missing list, bad syntax or a sample larger than the prompt's variants is refused before the run."""
    counts: dict[int, int] = {}
    for node in wildcard_nodes(graph):
        drawn = draw(node, snapshot)
        if node.prompt_mode == "all":
            counts[node.id] = len(drawn)
    return counts


def resolve(
    node: GraphNode, snapshot: Snapshot | None, branch: Branch, counts: dict[int, int]
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """(prompt, evidence pairs) of one branch of a wildcard node.

    Generate image ``i`` takes entry ``i`` of the node's one draw. An Inpaint/Img2img node that splits takes entry
    ``output`` of a draw seeded by its incoming image (Generate node and branch), so every incoming image gets its own
    prompts. One that does not split (a single prompt) takes entry 0 of a draw keyed by the incoming branch; the last
    digit of that branch belongs to an upstream node, not to this one."""
    expected = prompt_branches(node, counts)
    if node.type == GENERATE:
        key, index = "", branch[1]
    elif expected <= 1:
        key, index = f"{branch[0]}.{branch[1]}", 0
    else:
        parent = parent_branch(branch)
        incoming = parent if parent is not None else branch
        key, index = f"{incoming[0]}.{incoming[1]}", output_index(branch)
    drawn = draw(node, snapshot, key)
    if len(drawn) != expected or index >= len(drawn):
        raise ValidationError(f"{node.label}: the wildcard lists changed since the run was planned.")
    prompt, runs = drawn[index]
    sources = (snapshot or {}).get("sources") or {}
    used = _used(node, snapshot)
    mode = node.prompt_mode if node.prompt_count is None else f"{node.prompt_mode}:{node.prompt_count}"
    evidence = (
        (dp.PROMPT_TAGS_KEY, dp.encode_runs(prompt, runs)),
        (EVIDENCE_TEMPLATE, str(node.params["prompt"])),
        (EVIDENCE_MODE, str(mode)),
        *((f"{EVIDENCE_WILDCARD}{name}", sources[name]) for name in used if name in sources),
    )
    return prompt, evidence


def _used(node: GraphNode, snapshot: Snapshot | None) -> tuple[str, ...]:
    lists = dp.Lists(_lists(snapshot))
    try:
        dp.count(dp.parse(str(node.params["prompt"])), lists)
    except PlanError:
        return ()
    return tuple(sorted(lists.used))

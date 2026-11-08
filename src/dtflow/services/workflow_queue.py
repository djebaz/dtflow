"""Workflow runs as queue items (#223, D6): creation, branch decisions, Retry and read views. Qt-free.

A workflow run is ONE queue item of kind ``workflow`` (store schema 8). The item's record holds the validated
workflow snapshot and the run state (per-step status, outputs, pauses); there is no separate run store. Creation
touches only local state and never generates. Start dispatches it like any queue (``dispatch_queue``).

Run state shape (``read_workflow_run`` / the queue view's ``workflow`` field)::

    {"run_id": str, "name": str, "state": "pending|running|succeeded|partial|failed|cancelled|unknown",
     "planned": int, "submitted": int, "message": str | None,
     "steps": [{"node_id": int, "type": str, "title": str, "branch": [gen_node_id, index], "branch_label": str,
                "state": "pending|running|succeeded|failed|unknown|skipped|paused|stopped|cancelled",
                "stage": str | None, "message": str | None, "output": str | None, "mask": str | None,
                "parent": str | None, "settings": dict | None, "retries": int,
                "started_at": str | None, "finished_at": str | None}],
     "pauses": [{"node_id": int, "branch": [int, int], "branch_label": str, "reason": str}],
     "results": [{"label": str, "path": str, "branch": [int, int], "node_id": int}]}

The run also carries ``started_at`` / ``finished_at`` and, for a Retry run, ``retry_of`` (the source run id). Only the
dispatch owner writes it. ``pauses`` lists the branches waiting for a "No face detected" decision right now; an
answered pause leaves the list (its step keeps the reason in ``message``).

Controls go through the queue control table: ``workflow_branch`` ``{"node_id", "branch", "decision"}`` and
``workflow_retry`` ``{"node_id", "branch"}``. They are validated here against the run state the caller can read and
applied by the runner at its next poll; the runner checks them again (a stale request is ignored with an event).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

from dtflow.core.models import ValidationError
from dtflow.core.workflows import (
    DETECT_FACE,
    GENERATIVE,
    MASK,
    STATIC_SOURCES,
    Graph,
    WorkflowPlan,
    branch_label,
    dump_workflow,
    is_descendant,
    load_workflow,
    plan_workflow,
    source_branch,
)
from dtflow.persistence.queues import (
    BRANCH_DECISIONS,
    insert_workflow_queue,
    post_control,
    read_queue_requests,
    read_queues,
)
from dtflow.persistence.store import SQLiteAttempts, StateBusyError
from dtflow.persistence.workflow_items import KIND_WORKFLOW, is_workflow_record, workflow_record

BRANCH_CONTROL = "workflow_branch"  # queue control action; payload {"node_id", "branch", "decision"}
RETRY_CONTROL = "workflow_retry"  # queue control action; payload {"node_id", "branch"}
WORKFLOW_BACKENDS = ("local", "cloud", "fake")  # fake runs text-to-image only; image steps fail with a plain reason
RETRYABLE = ("failed", "unknown")

Branch = tuple[int, int]


# -- graph scope helpers (shared with the runner) -----------------------------------------------------------------


def descendants(graph: Graph, node_id: int) -> set[int]:
    seen: set[int] = set()
    stack = list(graph.children(node_id))
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(graph.children(current))
    return seen


def ancestors(graph: Graph, node_id: int) -> set[int]:
    seen: set[int] = set()
    stack = [link.origin_id for link in graph.links if link.target_id == node_id]
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(link.origin_id for link in graph.links if link.target_id == current)
    return seen


def run_scope(graph: Graph, plan: WorkflowPlan, seed: dict[str, Any] | None = None) -> list[tuple[int, Branch]]:
    """The (node, branch) steps a run executes, in topological then branch order.

    Without a seed: every node for every branch it runs on. With a Retry seed: the seed's node and its downstream
    nodes on the seed's branch (and branches split from it) only; a Generate seed reruns that one output and what it
    feeds, never its sibling outputs."""
    if seed is None:
        return [(node_id, branch) for node_id in plan.order for branch in plan.nodes[node_id].branches]
    start, branch = int(seed["node_id"]), (int(seed["branch"][0]), int(seed["branch"][1]))
    wanted = descendants(graph, start) | {start}
    # The step itself and, below it, every branch split from this one by a multiplying node (#276).
    return [
        (node_id, item)
        for node_id in plan.order
        if node_id in wanted
        for item in plan.nodes[node_id].branches
        if is_descendant(item, branch)
    ]


def planned_submissions(graph: Graph, scope: list[tuple[int, Branch]]) -> int:
    """Images the scope submits: one per Generate output, Inpaint or Img2Img branch it actually contains."""
    return sum(1 for node_id, _ in scope if graph.node(node_id).type in GENERATIVE)


def new_step(graph: Graph, node_id: int, branch: Branch) -> dict[str, Any]:
    node = graph.node(node_id)
    return {
        "node_id": node_id,
        "type": node.type,
        "title": node.label,
        "branch": [branch[0], branch[1]],
        "branch_label": branch_label(branch, graph),
        "state": "pending",
        "stage": None,
        "message": None,
        "output": None,
        "mask": None,
        "parent": None,
        "settings": None,
        "retries": 0,
        "started_at": None,
        "finished_at": None,
    }


def initial_run(
    run_id: str, name: str, graph: Graph, scope: list[tuple[int, Branch]], planned: int, *, retry_of: str | None = None
) -> dict[str, Any]:
    run: dict[str, Any] = {
        "run_id": run_id,
        "name": name,
        "state": "pending",
        "planned": planned,
        "submitted": 0,
        "message": None,
        "steps": [new_step(graph, node_id, branch) for node_id, branch in scope],
        "pauses": [],
        "results": [],
        "started_at": None,
        "finished_at": None,
    }
    if retry_of is not None:
        run["retry_of"] = retry_of
    return run


# -- creation -----------------------------------------------------------------------------------------------------


def _check_backend(backend: dict[str, Any]) -> None:
    if backend.get("name") not in WORKFLOW_BACKENDS:
        raise ValidationError("Workflows run on the local, cloud or fake backend only.")
    if backend.get("name") == "local" and not backend.get("endpoint"):
        raise ValidationError("The local backend requires --endpoint HOST:PORT.")


async def _persist(state: Path, backend: dict[str, Any], record: dict[str, Any], up_next: bool) -> str:
    store = SQLiteAttempts(state)
    try:
        await store.open()
        return await store.create_workflow_queue(backend, record)
    except StateBusyError:
        return await asyncio.to_thread(insert_workflow_queue, state, backend, record, up_next=up_next)
    finally:
        await store.aclose()


async def create_workflow_queue(
    state: Path, backend: dict[str, Any], workflow: dict[str, Any], name: str, *, up_next: bool = False
) -> tuple[str, str]:
    """Validate (``load_workflow`` + ``plan_workflow``) and persist one paused workflow queue (one item, cap 1).

    ``backend`` is the same dict the queue composer records (``name``, ``endpoint``, fake simulation keys).
    Returns ``(queue id, run id)``. Contacts no backend, never generates and never downloads."""
    _check_backend(backend)
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("Give the workflow run a name.")
    graph = load_workflow(workflow)
    plan = plan_workflow(graph)
    scope = run_scope(graph, plan)
    run_id = uuid4().hex
    planned = planned_submissions(graph, scope)
    record = workflow_record(
        run_id=run_id,
        name=name.strip(),
        workflow=dump_workflow(graph),
        backend=backend["name"],
        planned=planned,
        run=initial_run(run_id, name.strip(), graph, scope, planned),
    )
    return await _persist(Path(state), backend, record, up_next), run_id


# -- reads and controls -------------------------------------------------------------------------------------------


async def _view(state: Path, queue_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    views = await asyncio.to_thread(read_queues, Path(state), queue_id)
    view = views[0]
    if view.get("kind") != KIND_WORKFLOW:
        return None
    item = next((item for item in view["items"] if item.get("workflow") is not None), None)
    return (view, item) if item is not None else None


async def read_workflow_run(state: Path, queue_id: str) -> dict[str, Any] | None:
    """The run state of a workflow queue (shape in the module docstring), or None when it is not a workflow queue."""
    found = await _view(state, queue_id)
    return None if found is None else found[1]["workflow"]


def _branch(branch: Any) -> Branch:
    try:
        first, second = branch
    except (TypeError, ValueError):
        raise ValidationError("A branch is a (Generate node id, image index) pair.") from None
    if type(first) is not int or type(second) is not int:
        raise ValidationError("A branch is a (Generate node id, image index) pair.")
    return first, second


def _live(view: dict[str, Any], run: dict[str, Any]) -> bool:
    return run.get("state") == "running" and view.get("status") in ("running", "paused")


async def decide_branch(state: Path, queue_id: str, node_id: int, branch: tuple[int, int], decision: str) -> None:
    """Answer a branch's "No face detected" pause from any process: ``continue`` or ``stop`` (that branch only)."""
    if decision not in BRANCH_DECISIONS:
        raise ValidationError("Answer a paused branch with continue or stop.")
    branch = _branch(branch)
    found = await _view(state, queue_id)
    if found is None:
        raise ValidationError("This queue is not a workflow run.")
    view, item = found
    run = item["workflow"]
    waiting = any(
        pause.get("node_id") == node_id and tuple(pause.get("branch") or ()) == branch
        for pause in run.get("pauses", ())
    )
    if not waiting or not _live(view, run):
        raise ValidationError("This branch is not waiting for a decision.")
    payload = {"node_id": node_id, "branch": list(branch), "decision": decision}
    await asyncio.to_thread(post_control, Path(state), queue_id, BRANCH_CONTROL, payload)


def _step(run: dict[str, Any], node_id: int, branch: Branch) -> dict[str, Any] | None:
    return next(
        (
            step
            for step in run.get("steps", ())
            if step.get("node_id") == node_id and tuple(step.get("branch") or ()) == branch
        ),
        None,
    )


def _seed_values(
    graph: Graph,
    plan: WorkflowPlan,
    run: dict[str, Any],
    start: int,
    branch: Branch,
    inherited: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The saved inputs a Retry run starts from: every recorded output and mask on that branch that the retried scope
    (the step and everything downstream of it) reads from outside itself, including converging parallel paths.

    Every such input must still exist on disk; a face mask skipped by "Continue anyway" passes on as no mask. A Retry
    run's own steps cover only its scope, so its ``inherited`` seed supplies the inputs from before it. Each value
    sits on the branch its node ran on: the retried branch, or the ancestor it was split from (#276)."""
    scope = descendants(graph, start) | {start}
    outside: set[int] = set()
    for node_id in scope:
        outside |= ancestors(graph, node_id)
    outside -= scope
    earlier = {}
    if isinstance(inherited, dict) and inherited.get("branch"):
        home = tuple(inherited["branch"])
        for value in inherited.get("values") or ():
            if isinstance(value, dict):
                earlier[(value["node_id"], tuple(value.get("branch") or home))] = value
    values = []
    for node_id in sorted(outside):
        origin = source_branch(plan, node_id, branch)
        if origin is None:
            continue
        step = _step(run, node_id, origin)
        if step is None and (node_id, origin) in earlier:
            values.append({**earlier[(node_id, origin)], "branch": list(origin)})
        elif step is not None:
            value = {"node_id": node_id, "branch": list(origin), "output": step.get("output"), "mask": step.get("mask")}
            if step.get("type") == DETECT_FACE and step.get("state") == "skipped":
                value["no_target"] = True
            values.append(value)
    known = {value["node_id"]: value for value in values}
    for node_id in scope:
        node = graph.node(node_id)
        for slot, port in enumerate(node.spec.inputs):
            link = graph.input_link(node_id, slot)
            if link is None or link.origin_id in scope:
                continue
            value = known.get(link.origin_id) or {}
            if port.type == MASK and value.get("no_target"):
                continue
            path = value.get("mask") if port.type == MASK else value.get("output")
            if graph.node(link.origin_id).type == DETECT_FACE and port.type == MASK and path is None:
                raise ValidationError("The face mask this step needs was never made; run the workflow again.")
            if not isinstance(path, str) or not Path(path).is_file():
                raise ValidationError("A saved input of this step is no longer available; run the workflow again.")
    return values


async def retry_step(state: Path, queue_id: str, node_id: int, branch: tuple[int, int]) -> str:
    """Retry a failed/unknown step with its original settings, then continue its downstream branch.

    While the run is live the owner applies it in place and this returns ``queue_id``; after the run ended, a new
    paused workflow queue seeded with the step's saved inputs is created and its id returned (the caller starts it)."""
    branch = _branch(branch)
    found = await _view(state, queue_id)
    if found is None:
        raise ValidationError("This queue is not a workflow run.")
    view, item = found
    run = item["workflow"]
    step = _step(run, node_id, branch)
    if step is None or step.get("state") not in RETRYABLE:
        raise ValidationError("Only a failed step or one with no confirmed outcome can be retried.")
    if step.get("type") in STATIC_SOURCES:
        raise ValidationError("A file that could not be loaded stops the whole run: fix the file and run it again.")
    if _live(view, run):
        payload = {"node_id": node_id, "branch": list(branch)}
        await asyncio.to_thread(post_control, Path(state), queue_id, RETRY_CONTROL, payload)
        return queue_id
    if any(other.get("state") == "running" for other in run.get("steps", ())):
        raise ValidationError("This run is still settling; try again in a moment.")
    backend, _, items = await asyncio.to_thread(read_queue_requests, Path(state), queue_id)
    record = next((record for _, _, record in items if is_workflow_record(record)), None)
    if record is None:
        raise ValidationError("This workflow run's record is unreadable; nothing was created.")
    graph = load_workflow(record["workflow"])
    plan = plan_workflow(graph)
    if plan.nodes[node_id].static:
        raise ValidationError("A file that could not be loaded stops the whole run: fix the file and run it again.")
    seed = {
        "node_id": node_id,
        "branch": list(branch),
        "values": _seed_values(graph, plan, run, node_id, branch, record.get("seed")),
    }
    scope = run_scope(graph, plan, seed)
    run_id = uuid4().hex
    planned = planned_submissions(graph, scope)
    retry = workflow_record(
        run_id=run_id,
        name=record["name"],
        workflow=record["workflow"],
        backend=record["backend"],
        planned=planned,
        run=initial_run(run_id, record["name"], graph, scope, planned, retry_of=record["run_id"]),
        seed=seed,
    )
    return await _persist(Path(state), backend, retry, False)
